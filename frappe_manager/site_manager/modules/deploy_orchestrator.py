"""DeployOrchestrator - image-based switch/rollback pipeline (Phase 4 v1).

Implements the decomposed image deploy (recreate-swap) that ``fmx restart
--migrate`` cannot express in image mode:

    fetch -> pre-flight -> render image compose(new tag) -> resolve migrate
    (probe when 'auto') -> maintenance(if migrate) -> drain(old) -> backup (at
    the quiesced point) -> migrate(one-shot, new image) -> swap (rolling when
    the overlap is safe, else recreate) -> finalize(resume + site DB ops +
    maintenance off) -> record deploy_state

Rolling scale-2 is the default web swap whenever the overlap is
safe (no migrate, additive-asserted, or a maintenance window covering the
migrate); recreate-swap remains for migrate deploys that disable the
maintenance window. Supervisor stays.
"""

import base64
import contextlib
import re
import shlex
import shutil
import subprocess
import tempfile
import time
from datetime import UTC, datetime
from pathlib import Path

from frappe_manager.docker import DockerException
from frappe_manager.exceptions import NonInteractiveError
from frappe_manager.logger import get_logger
from frappe_manager.output_manager import OutputHandler
from frappe_manager.output_manager.rich_output import RichOutputHandler
from frappe_manager.services_manager.database_service_manager import (
    DatabaseServerServiceInfo,
    MariaDBManager,
)
from frappe_manager.site_manager.bench_config import (
    BenchRuntime,
    DatabaseConfig,
    DeployState,
    DeployStateEntry,
    SwitchConfig,
    WorkersConfig,
)
from frappe_manager.site_manager.hooks import hook_env, hook_script
from frappe_manager.site_manager.modules import db_probe, db_tls
from frappe_manager.utils.docker import run_command_with_exit_code

BENCH_BIN = "/opt/user/.bin/bench"
FRAPPE_SERVICE = "frappe"
# coreutils `timeout` exits 124 when it had to kill the command it wrapped; that
# is how a migrate over [switch].migrate_timeout reports back (see _migrate).
TIMEOUT_EXIT_CODE = 124
# fmx is installed as a uv tool; a bare `python` is not on the exec PATH.
FMX_PYTHON = "/opt/uv-tools/fmx/bin/python"


def external_dump_tls_hint(site: str) -> str:
    """What to check when a dump-based step fails on an external database.

    Frappe's ``get_command`` builds every ``mariadb`` and ``mariadb-dump``
    invocation from user, socket, host, port and password alone and never reads
    ``db_ssl_*``, so a dump or restore takes its TLS from an option file and not
    from ``site_config.json``. That is the failure nobody guesses: the site keeps
    connecting happily over TLS while every dump is refused. The bundle is named
    because it is the file a hand-replaced CA leaves stale.
    """
    return (
        "The `mariadb` client that dumps and restores go through does not read db_ssl_* from "
        f"sites/{site}/site_config.json. It takes TLS from the \\[client] section of the option file "
        f"found through MYSQL_HOME, which fm points at config/tls/{site}/my.cnf for this site. "
        "Check that file, and config/tls/ca-bundle.pem: the bundle is what a CA replaced by hand "
        "leaves stale, and `fm update <bench> --db-ca PATH` rewrites both."
    )


class DeployError(Exception):
    """Raised when an image deploy cannot proceed or fails."""


class RestoreNotConfirmed(DeployError):
    """Raised when the operator declines a dump import into an external schema.

    A subclass rather than a bare DeployError so the two paths can differ: an
    explicit `--restore-db` that is declined aborts the deploy, while fm's own
    insurance restores (`rollback_db`) carry on without the import. Declining to
    write to a database fm does not own must never strand the bench on an image
    it was rolling away from.
    """


class DrainUnavailable(DeployError):
    """Raised when the drain command never ran inside the frappe container.

    A subclass rather than a bare DeployError because the caller has to tell it
    apart from a drain that ran and timed out. Images predating fmx have no
    ``FMX_PYTHON`` at all, so the exec fails in milliseconds; reporting that as
    "drain timed out after 300s" sends the operator to raise a timeout that was
    never the problem, and no timeout can ever make the exec succeed.
    """


def _exec_error_text(exc: Exception) -> str:
    """The one useful line out of a failed container exec.

    DockerException stringifies to the whole command, stdout and stderr blocks;
    for an error message the operator reads once, its stderr is the part that
    names the cause (``stat /opt/uv-tools/fmx/bin/python: no such file``).
    """
    stderr = getattr(getattr(exc, "output", None), "stderr", None) or []
    text = " ".join(line.strip() for line in stderr if line.strip())
    return text or str(exc)


def rolling_eligible(
    migrate: bool,
    maintenance_mode: bool,
    maintenance_mode_phases: list[str],
    override: bool | None = None,
) -> bool:
    """Decide whether a deploy may use the rolling web swap.

    ``override`` is the CLI ``--rolling/--no-rolling`` flag (None = auto). In
    auto mode a deploy is rolling-eligible whenever the replica overlap cannot
    break old code that shares the DB:

    * no migrate at all, or
    * the operator asserted a backward-compatible (additive) migration by
      clearing ``maintenance_mode_phases``, or
    * the migrate runs under an active maintenance window -- both replicas
      serve the maintenance 503 (``maintenance_mode`` lives in the shared
      ``common_site_config.json``), so old code never executes real requests
      against the migrated schema.

    Only a migrate deploy with the maintenance window disabled must recreate.
    """
    if override is not None:
        return override
    if migrate is False or maintenance_mode_phases == []:
        return True
    return maintenance_mode


MIGRATE_PROBE_MARKER = "FM-MIGRATE-PROBE"


def parse_migrate_probe(lines) -> dict | None:
    """Structured verdict from the migrate-probe marker line, or None.

    ``{"needed": bool, "pending": int | None, "drift": [app, ...]}`` -- the
    pending patch count and drifted-app names feed the migrate/backup
    decisions and are exported to hook env (MIGRATE_PROBE /
    MIGRATE_PENDING_PATCHES / MIGRATE_APP_DRIFT)."""
    for line in lines or []:
        if MIGRATE_PROBE_MARKER not in line:
            continue
        tail = line.split(MIGRATE_PROBE_MARKER, 1)[1]
        pending_m = re.search(r"pending=(\d+)", tail)
        drift_m = re.search(r"drift=(\S+)", tail)
        drift = [] if not drift_m or drift_m.group(1) == "none" else drift_m.group(1).split(",")
        return {
            "needed": tail.split()[:1] != ["clean"],
            "pending": int(pending_m.group(1)) if pending_m else None,
            "drift": drift,
        }
    return None


def pin_workers_to_image(workers, site: str, deploy_tag: str) -> None:
    """Pin the workers compose to ``deploy_tag`` with image-mode data binds.

    Thin delegator over the compose_shape projection -- the same specs the
    workers' own generate_compose uses, with ``deploy_tag`` as the candidate
    tag. No-op when the bench has no workers compose. Idempotent; user extras
    (override.yml / non-managed mounts) pass through untouched.
    """
    from frappe_manager.site_manager.modules.compose_shape import (
        RenderContext,
        apply_specs,
        worker_service_specs,
    )

    if not workers.compose_path.exists():
        return
    cfm = workers.compose_file_manager
    svcs = cfm.get_services_list()
    if not svcs:
        return
    specs = worker_service_specs(workers.bench.bench_config, svcs, RenderContext(deploy_tag=deploy_tag))
    apply_specs(cfm, specs, site)
    cfm.write_to_file()


def _parse_installed_apps(lines) -> set[str]:
    """Parse ``bench list-apps`` output into a set of installed app names.

    Tolerant of the version/branch columns some Frappe versions print: takes the
    first whitespace token of each line when it is a valid (lowercase) app module
    name, dropping headers/noise."""
    names: set[str] = set()
    for line in lines or []:
        tokens = line.strip().split()
        if tokens and re.fullmatch(r"[a-z][a-z0-9_]*", tokens[0]):
            names.add(tokens[0])
    return names


def _new_apps(wanted: list[str], installed: set[str]) -> list[str]:
    """Apps in ``wanted`` (image/config order) not present in ``installed``."""
    return [a for a in wanted if a not in installed]


def plan_release_prune(history: list, keep: int) -> tuple[list, list]:
    """Split deploy history into ``(kept, pruned)`` -- pure newest-``keep`` rows.

    Rows are audit lines only; artifact safety (images/dumps a live tag still
    needs) is decided separately by :func:`plan_artifact_removal`.
    """
    keep = max(1, keep)
    if len(history) <= keep:
        return list(history), []
    return list(history[-keep:]), list(history[:-keep])


def plan_artifact_removal(kept: list, pruned: list, protected_tags: set[str]) -> tuple[list[str], list[str]]:
    """``(backup_paths, image_tags)`` safe to delete for the pruned rows.

    A backup survives while ANY kept row references it; an image tag survives
    while any kept row OR the protected set (current / previous / seed / base)
    references it -- pruning must never orphan a tag the bench can still
    switch back to.
    """
    kept_backups = {entry.backup for entry in kept if entry.backup}
    backups = sorted({entry.backup for entry in pruned if entry.backup} - kept_backups)
    kept_tags = {entry.tag for entry in kept} | protected_tags
    tags = sorted({entry.tag for entry in pruned} - kept_tags)
    return backups, tags


class DeployOrchestrator:
    """Runs the image deploy / rollback pipeline for a single bench."""

    def __init__(self, bench, output_handler: OutputHandler | None = None):
        self.bench = bench
        self.config = bench.bench_config
        self.switch_config = self.config.switch
        self.workers_config = self.config.workers or WorkersConfig()
        self.site = bench.name
        self.bench_path = Path(bench.path)
        self.docker = bench.docker_client
        # migrate='auto' probe details (verdict/pending/drift); exported to hook env.
        self._probe_result: dict | None = None
        # bench-migrate outcome + persisted log paths; exported to hook env so
        # after_migrate hooks (which also run on FAILURE) can ship notifications.
        self._migrate_status: str | None = None
        self._migrate_log_host: Path | None = None
        self._migrate_log_container: str | None = None
        self.compose = bench.compose_file_manager
        self.docker_ops = bench.docker_ops
        self.output = output_handler or RichOutputHandler()
        self.logger = get_logger(component="deploy")

    # ------------------------------------------------------------------ helpers

    def _require_image_mode(self) -> None:
        if self.config.runtime != BenchRuntime.image:
            raise DeployError(
                f"Bench '{self.site}' is not in image runtime "
                f"(runtime={self.config.runtime.value}). Set runtime = 'image'.",
            )
        if not self.config.image:
            raise DeployError("No image configured; set top-level image (or --image).")
        self.switch_config = self.config.switch or SwitchConfig()
        self.workers_config = self.config.workers or WorkersConfig()

    def _switch_hook(self, name, host=False):
        hooks = self.switch_config.hooks
        if hooks is None:
            return None
        return getattr(hooks.host if host else hooks, name, None)

    def _config_path(self) -> Path:
        return Path(self.config.root_path)

    def _frappe_running(self) -> bool:
        try:
            statuses = self.docker.compose.get_all_services_status()
            containers = self.compose.get_container_names()
            target = containers.get(FRAPPE_SERVICE)
            return any(s.get("Name") == target and s.get("State") == "running" for s in statuses)
        except Exception:
            return False

    def _exec_frappe(self, command: str, user: str = "frappe"):
        return self.docker.compose.exec(
            service=FRAPPE_SERVICE,
            command=command,
            user=user,
            workdir="/workspace/frappe-bench",
            stream=False,
        )

    def _set_maintenance(self, value: int) -> None:
        self._exec_frappe(f"{BENCH_BIN} --site {self.site} set-config -g maintenance_mode {value}")

    def _fetch_image(self, tag: str) -> None:
        """Ensure ``tag`` (+ its derived nginx tag) is present on the target daemon.

        Delegates to the shared ``transport.fetch_image``; ``TransportError`` AND
        ``BakeError`` are re-raised as ``DeployError`` to preserve deploy's error
        contract. ``BakeError`` belongs here because fetch_image derives the nginx
        tag first: a tag with no ``':'`` (``fm switch mybench local/mybench`` --
        a plausible typo) dies there, and untranslated it escapes the CLI's
        ``except DeployError`` as a traceback instead of the typo's message.
        """
        from frappe_manager.site_manager.modules.bake import BakeError
        from frappe_manager.site_manager.modules.transport import TransportError, fetch_image

        try:
            fetch_image(self.docker, getattr(self.config, "registry", None), tag, output=self.output)
        except (TransportError, BakeError) as e:
            raise DeployError(str(e)) from e

    def _health_check(self, retries: int = 45, interval: int = 2) -> bool:
        for i in range(retries):
            try:
                result = self.docker.compose.exec(
                    service=FRAPPE_SERVICE,
                    command='curl -s -o /dev/null -w "%{http_code}" http://localhost:80',
                    user="frappe",
                    stream=False,
                )
                code = "".join(result.stdout).strip()
                # 503 = maintenance page (server up, migrate window). Finalize
                # clears maintenance after the gate passes.
                if code in ("200", "404", "503"):
                    return True
            except Exception as e:
                self.logger.debug(f"health check attempt {i + 1}: {e}")
            time.sleep(interval)
        return False

    def _ensure_nginx(self) -> None:
        """(Re)start nginx once frappe is up. nginx aborts with ``[emerg] host
        not found in upstream "frappe:80"`` if it wins the startup race, so this
        re-runs ``up`` for nginx after the frappe upstream resolves."""
        with contextlib.suppress(Exception):
            self.docker.compose.up(services=["nginx"], detach=True, pull="never", stream=False)
        time.sleep(3)

    # ---------------------------------------------------------- rolling swap

    def _raw_compose(self, *args: str):
        """Run a raw ``docker compose`` subcommand against the bench compose.

        The wrapper's ``up`` cannot express ``--scale``, so the rolling path
        builds compose commands directly."""
        cmd = list(self.docker.compose.docker_compose_cmd) + list(args)
        return run_command_with_exit_code(cmd, stream=False, capture_output=True)

    def _raw_docker(self, *args: str):
        return run_command_with_exit_code(["docker", *args], stream=False, capture_output=True)

    def _compose_ps_ids(self, service: str) -> list[str]:
        out = self._raw_compose("ps", "-q", service)
        return [ln.strip() for ln in out.stdout if ln.strip()]

    def _scale(self, scales: dict[str, int]) -> None:
        """``compose up -d --no-recreate`` at the given per-service replica counts.

        ``--no-recreate`` keeps the old (old-tag) container in place and only
        adds the new replica; the compose file is already pinned to the new tag.

        A non-zero compose exit never comes back as an exit code: the capturing
        path raises DockerException while the output is drained. Translate it to
        DeployError so the swap aborts through ``_rolling_swap``'s handler
        instead of a raw DockerException escaping with the compose still in the
        rolling (container_name-less) render."""
        args = ["up", "-d", "--no-recreate", "--pull", "never"]
        for svc, n in scales.items():
            args += ["--scale", f"{svc}={n}"]
        try:
            self._raw_compose(*args)
        except DockerException as e:
            raise DeployError(f"compose scale {scales} failed: {e}") from e

    def _new_container_id(self, service: str, old_ids: list[str]) -> str | None:
        for cid in self._compose_ps_ids(service):
            if cid not in old_ids:
                return cid
        return None

    def _container_health(self, container_id: str, retries: int = 45, interval: int = 2) -> bool:
        """Poll ``/api/method/ping`` inside ``container_id`` (curl or wget). Honors
        the timing rule: retries*interval spans well past the app's boot +
        in-flight window before we drain the old replica."""
        probe = (
            'if command -v curl >/dev/null 2>&1; then '
            'curl -s -o /dev/null -w "%{http_code}" http://localhost:80/api/method/ping; '
            'else wget -q -O /dev/null -S http://localhost:80/api/method/ping 2>&1 | '
            'awk "/HTTP\\//{print \\$2; exit}"; fi'
        )
        for i in range(retries):
            try:
                out = self._raw_docker("exec", container_id, "sh", "-c", probe)
                code = "".join(out.stdout).strip().split()[-1] if out.stdout else ""
                if code in ("200", "404", "503"):
                    return True
            except Exception as e:
                self.logger.debug(f"container health {container_id[:12]} attempt {i + 1}: {e}")
            time.sleep(interval)
        return False

    # app-nginx runs under supervisord as a NON-root user, so `/run/nginx.pid` is
    # never written and `nginx -s reload` fails ("open /run/nginx.pid failed").
    # Find the master via /proc (no `ps` in the image) and SIGHUP it directly so
    # nginx re-parses config and re-resolves the static `upstream frappe:80`.
    # Match on /proc/PID/comm (== "nginx"), NOT cmdline: this very script's
    # `sh -c` argv would otherwise contain the search string and HUP itself. The
    # master is the nginx process whose parent is not nginx (workers' parent is).
    _NGINX_HUP = (
        'for p in /proc/[0-9]*; do '
        '[ "$(cat "$p/comm" 2>/dev/null)" = "nginx" ] || continue; '
        'pp=$(awk "/^PPid:/{print \\$2}" "$p/status" 2>/dev/null); '
        '[ "$(cat "/proc/$pp/comm" 2>/dev/null)" = "nginx" ] && continue; '
        'kill -HUP "${p#/proc/}" && exit 0; done; '
        'nginx -s reload'
    )

    def _reload_nginx(self, container_id: str) -> None:
        """Graceful SIGHUP reload so the surviving app-nginx re-resolves the static
        ``server frappe:80`` upstream (drops the drained replica, keeps the new)."""
        with contextlib.suppress(Exception):
            self._raw_docker("exec", container_id, "sh", "-c", self._NGINX_HUP)

    def _stop(self, container_id: str) -> None:
        # Graceful stop (SIGTERM): gunicorn/nginx finish in-flight and CLOSE their
        # listener, so new connections are refused-fast (not black-holed). `stop`
        # also drops the container from Docker's embedded DNS immediately.
        with contextlib.suppress(Exception):
            self._raw_docker("stop", container_id)

    def _rm(self, container_id: str) -> None:
        with contextlib.suppress(Exception):
            self._raw_docker("rm", "-f", container_id)

    def _stop_rm(self, container_id: str, drain: float = 3.0) -> None:
        self._stop(container_id)
        if drain:
            time.sleep(drain)
        self._rm(container_id)

    def _rename(self, container_id: str, name: str) -> None:
        # A canonical container may still exist if a prior deploy was interrupted.
        with contextlib.suppress(Exception):
            self._raw_docker("rename", container_id, name)

    def _abort_rolling(
        self,
        web: list[str],
        old_ids: dict[str, list[str]],
        old_tag: str | None,
        snaps: dict[Path, bytes],
    ) -> None:
        """New replica unhealthy: OLD never stopped -> still serving. Tear down the
        new replicas and restore the pre-deploy (old-tag) compose. Zero downtime
        even on a failed rolling deploy."""
        self.output.warning("Rolling: new replica unhealthy; keeping old, tearing down new replicas")
        for svc in web:
            nid = self._new_container_id(svc, old_ids[svc])
            if nid:
                self._stop_rm(nid, drain=0)
        self._restore_compose(snaps)
        if old_tag:
            self._pin_workers(old_tag)

    def _rolling_swap(self, new_tag: str, old_tag: str | None, snaps: dict[Path, bytes]) -> None:
        """Rolling web swap: run new + old web replicas concurrently, drain old,
        then reduce to the new replica -- zero dropped requests for a no-migrate
        deploy (vs the recreate-swap's brief blip). Recreate-swap stays the
        migrate/fallback path.

        Scale target = BOTH web tiers (frappe gunicorn + nginx). The global
        jwilder/nginx-proxy routes to app-nginx (VIRTUAL_HOST) which in turn
        proxies to frappe; scaling only one tier would drop the other during its
        recreate. Frappe is scaled first so the new app-nginx (added next)
        resolves both frappe replicas. CAVEAT: during the overlap a request may
        cross version tiers (new-nginx -> old-frappe or vice-versa); for a plain
        code+assets deploy against one shared DB this is zero-DOWNTIME (requests
        succeed), not zero-skew -- a client may momentarily get old assets with
        new code or vice-versa. Eligibility (no migrate / additive) guarantees
        both code versions are DB-compatible, so the skew cannot 500."""
        services = self.compose.get_services_list()
        web = [s for s in ("frappe", "nginx") if s in services]
        canonical = self.compose.get_container_names()
        old_ids = {svc: self._compose_ps_ids(svc) for svc in web}

        # Steps 1-3 all run while the OLD replicas are untouched and still
        # serving, so EVERY failure in them -- an unhealthy new replica, or a
        # `compose --scale` that failed (DeployError out of `_scale`) -- unwinds
        # through `_abort_rolling`: new replicas torn down, canonical (old-tag)
        # compose restored. Without that, a mid-scale failure would escape with
        # the compose left in the rolling render (no `container_name`), and a
        # later `compose up` would create containers under generated names that
        # `get_container_names()` no longer matches -- fm would read the bench as
        # down. The drain (step 4 onward) is deliberately NOT covered: once the
        # old replicas are stopped, tearing the new ones down would cause the
        # very outage `_abort_rolling` exists to prevent.
        try:
            # 1. Re-render the compose without container_name on the web tiers so
            #    docker compose accepts --scale, and pin workers to the new tag.
            self.output.change_head("Rolling: rendering scalable image compose")
            self.docker_ops.render_image_compose(new_tag, rolling=True)
            self._pin_workers(new_tag)

            # 2. Add the new frappe replica alongside the old (old keeps serving).
            self.output.change_head("Rolling: starting new frappe replica")
            self._scale({"frappe": 2})
            new_frappe = self._new_container_id("frappe", old_ids["frappe"])
            if not new_frappe or not self._container_health(new_frappe):
                raise DeployError("new frappe replica failed health check; kept old, no swap")

            # 3. Add the new nginx replica (now resolves both frappe replicas).
            self.output.change_head("Rolling: starting new nginx replica")
            self._scale({"frappe": 2, "nginx": 2})
            new_nginx = self._new_container_id("nginx", old_ids["nginx"])
            if not new_nginx or not self._container_health(new_nginx):
                raise DeployError("new nginx replica failed health check; kept old, no swap")
        except Exception:
            self._abort_rolling(web, old_ids, old_tag, snaps)
            raise

        # 4. Drain OLD replicas. jwilder/nginx-proxy 1.11 does NOT honor container
        #    health, so proxy routing changes only on container add/remove;
        #    nginx's default `proxy_next_upstream error timeout` retries the
        #    surviving upstream during the brief proxy->nginx churn.
        #
        #    app-nginx's `upstream frappe:80` is resolved ONCE at config load, so
        #    a killed old-frappe would leave a dead IP that black-holes SYNs
        #    (connect-timeout hang, no fast failover). We therefore `stop` old
        #    frappe first (drops it from Docker DNS + closes its listener) and
        #    `nginx -s reload` the survivor to re-resolve to only the new replica
        #    BEFORE removing it, so no request is ever routed to a dead IP.
        self.output.change_head("Rolling: draining old web replicas")
        time.sleep(5)
        for cid in old_ids["nginx"]:
            self._stop(cid)
        self._reload_nginx(new_nginx)  # re-resolve frappe upstream on survivor
        for cid in old_ids["nginx"]:
            self._rm(cid)
        for cid in old_ids["frappe"]:
            self._stop(cid)
        self._reload_nginx(new_nginx)  # drop the just-stopped old frappe upstream
        for cid in old_ids["frappe"]:
            self._rm(cid)
        self._reload_nginx(new_nginx)

        # 5. Survivors are compose replica #2; rename to the canonical names and
        #    re-render the canonical (container_name-bearing) compose WITHOUT a
        #    `compose up`, so get_container_names() keeps matching and no recreate
        #    (no blip) happens.
        self.output.change_head("Rolling: restoring canonical container names")
        self._rename(new_frappe, canonical["frappe"])
        self._rename(new_nginx, canonical["nginx"])
        self.docker_ops.render_image_compose(new_tag, rolling=False)

        # 6. Bring the non-web code tiers (socketio, schedule) + workers to the
        #    new tag. These are out of the /api HTTP path; a brief socketio
        #    reconnect is acceptable and not in the request histogram.
        with contextlib.suppress(Exception):
            self._raw_compose("up", "-d", "--pull", "never", "socketio", "schedule")
        self._up_workers()

    def _worker_services(self):
        """Return ``(compose_file_manager, docker_client, service_list)`` for the
        workers compose, or ``None`` when the bench has no workers compose."""
        try:
            workers = self.bench.workers
            if not workers.compose_path.exists():
                return None
            cfm = workers.compose_file_manager
            svcs = cfm.get_services_list()
        except Exception as e:
            self.logger.debug(f"workers compose unavailable: {e}")
            return None
        if not svcs:
            return None
        return cfm, workers.docker_client, svcs

    def _pin_workers(self, deploy_tag: str) -> None:
        pin_workers_to_image(self.bench.workers, self.site, deploy_tag)

    def _up_workers(self) -> None:
        info = self._worker_services()
        if not info:
            return
        _cfm, dc, _svcs = info
        dc.compose.up(services=[], detach=True, pull="never", wait=True, stream=False)

    # ---------------------------------------------------------------- pipeline

    def _snapshot_compose(self) -> dict[Path, bytes]:
        snaps: dict[Path, bytes] = {}
        for p in (self.compose.compose_path, self.bench.workers.compose_path):
            if p and Path(p).exists():
                snaps[Path(p)] = Path(p).read_bytes()
        return snaps

    def _restore_compose(self, snaps: dict[Path, bytes]) -> None:
        for p, data in snaps.items():
            p.write_bytes(data)
        # Reload the in-memory compose managers from the restored files.
        self.compose.__init__(self.compose.compose_path)  # type: ignore[misc]

    def _external_db(self) -> DatabaseConfig | None:
        """This site's external database entry, or None for the global-db container."""
        return self.config.get_database_config(self.site)

    def _db_manager(self) -> tuple[MariaDBManager, str | None]:
        db_info = DatabaseServerServiceInfo.import_from_bench(
            bench_name=self.site,
            bench_path=self.bench_path,
            raise_exception=False,
        )
        db_name = db_info.name or self.config.db_name
        # MYSQL_HOME is the only way the client learns a CA: it reads <dir>/my.cnf.
        # None for global-db, whose certificate an external CA would not describe.
        mysql_home = db_tls.site_mysql_home(self.site) if self._external_db() else None
        manager = MariaDBManager(
            db_info,
            self.compose,
            self.docker,
            run_on_compose_service=FRAPPE_SERVICE,
            output_handler=self.output,
            mysql_home=mysql_home,
        )
        return manager, db_name

    def _backup(self, backup_dir: Path) -> Path | None:
        """DB dump + config copies into ``backup_dir``. Returns the DB dump path
        (host) or None when skipped/unavailable."""
        backup_dir.mkdir(parents=True, exist_ok=True)
        sites = self.bench_path / "workspace" / "frappe-bench" / "sites"
        for rel in (f"{self.site}/site_config.json", "common_site_config.json"):
            src = sites / rel
            if src.exists():
                dest = backup_dir / rel.replace("/", "__")
                shutil.copy2(src, dest)

        if not self._frappe_running():
            self.output.warning("frappe container not running; skipping DB backup.")
            return None

        manager, db_name = self._db_manager()
        if not db_name:
            self.output.warning("Could not resolve DB name; skipping DB backup.")
            return None

        # db_export writes inside the container; /workspace maps to the bench workspace.
        container_path = Path("/workspace") / "frappe-bench" / "logs" / "deploy-db-backup.sql"
        host_path = self.bench_path / "workspace" / "frappe-bench" / "logs" / "deploy-db-backup.sql"
        try:
            manager.db_export(db_name, container_path)
        except DockerException as e:
            self.output.warning(f"DB export failed; continuing without DB backup: {e}")
            if self._external_db():
                self.output.warning(external_dump_tls_hint(self.site))
            return None
        final = backup_dir / f"db-{db_name}.sql"
        if host_path.exists():
            shutil.move(str(host_path), str(final))
            return final
        return None

    def _restore_db(self, db_dump: Path) -> None:
        if not db_dump or not db_dump.exists():
            self.output.warning("No DB backup available to restore.")
            return
        manager, db_name = self._db_manager()
        if not db_name:
            self.output.warning("Could not resolve DB name; skipping DB restore.")
            return
        if self._external_db() is not None:
            self._confirm_external_restore(manager, db_name, db_dump)
        self.output.change_head("Restoring database backup")
        try:
            manager.db_import(db_name, db_dump, force=True)
        except Exception as e:
            if not self._external_db():
                raise
            raise DeployError(
                f"Restoring {db_dump} into {db_name} failed: {e}\n{external_dump_tls_hint(self.site)}",
            ) from e

    def _confirm_external_restore(self, manager: MariaDBManager, schema: str, db_dump: Path) -> None:
        """Typed confirmation before a dump lands on a database fm does not own.

        ``db_import(force=True)`` imports OVER the live schema, and a Frappe dump
        opens with ``DROP TABLE IF EXISTS`` per table, so this is the most
        destructive operation fm owns. ``--restore-db <path>`` proves the operator
        meant that dump; it does not prove they meant this database, and that is
        the half that bites. The table count is read here and not carried over from
        an earlier phase, because a stale count is exactly the reassurance this
        guard must not give.

        Reached only when the site has a `[database]` entry: a global-db restore
        neither prompts nor queries, since that schema is fm's own. A `bench
        restore` typed by hand inside `fm shell` never reaches fm at all, and this
        guard makes no claim over it.
        """
        endpoint = manager.database_server_info
        host = f"{endpoint.host}:{endpoint.port}"

        if not self.output.is_interactive():
            # The global --non-interactive escape hatch (and a missing TTY), so
            # unattended deploys keep working. No prompt means no reason to query.
            self.output.warning(
                f"Non-interactive: importing {db_dump.name} over schema '{schema}' on {host} unconfirmed.",
            )
            return

        state = self._live_schema_state(manager, schema)
        if state is None:
            holds = "fm could not read how many tables it holds right now"
        elif not state[0]:
            holds = "it does not exist on that server yet, so this import would create it"
        else:
            holds = f"it holds {state[1]} tables right now, every one of which the dump drops and recreates"

        self.output.warning(
            f"About to import {db_dump} into schema '{schema}' on {host}, a database fm does not own: {holds}.",
        )
        try:
            answer = self.output.prompt_ask(prompt=f"Type the schema name '{schema}' to confirm this overwrite")
        except NonInteractiveError:
            # Output modes that cannot prompt even on a TTY (json, silent) are the
            # same escape hatch under another name.
            self.output.warning(f"This output mode cannot prompt: importing into '{schema}' on {host} unconfirmed.")
            return

        if answer.strip() != schema:
            raise RestoreNotConfirmed(
                f"Restore into schema '{schema}' on {host} was not confirmed: the schema name was not typed. "
                "Nothing was imported.",
            )

    def _live_schema_state(self, manager: MariaDBManager, schema: str) -> tuple[bool, int] | None:
        """``(schema exists, table count)`` read from the server now, None if unreadable.

        Same query the create-time probe uses, run through the client the restore
        itself will use, so a TLS problem shows up here rather than mid-import.
        A count fm cannot read is reported as unknown: the confirmation still has
        to be typed, it just cannot quote a number.
        """
        sql = db_probe.schema_state_sql(schema)
        try:
            output = manager.db_run_query(shlex.quote(sql), capture_output=True)
        except Exception as e:
            self.output.warning(f"Could not count the tables in '{schema}': {e}")
            self.output.warning(external_dump_tls_hint(self.site))
            return None
        for line in getattr(output, "stdout", None) or ():
            columns = line.split("\t")
            if len(columns) < 2:
                continue
            try:
                return columns[0].strip() not in {"0", ""}, int(columns[1].strip() or 0)
            except ValueError:
                continue
        return None

    def drain_workers(self) -> bool:
        """Suspend RQ workers and wait for in-flight jobs.

        Returns True when it is safe to restart (fully drained, drain disabled,
        or frappe not running); False when the drain ran and timed out with
        workers still busy. Raises DrainUnavailable when the drain could not run
        at all -- that is not a timeout and callers must not report it as one."""
        if not (self.workers_config.drain and self._frappe_running()):
            return True
        self.output.change_head("Draining RQ workers")
        timeout = self.workers_config.drain_timeout
        poll = self.workers_config.drain_poll
        skip_stale = self.workers_config.skip_stale
        stale_timeout = self.workers_config.stale_timeout
        py = (
            "import sys; "
            "from fmx.rq_controller import control_rq_workers, ActionEnum, wait_for_rq_workers_suspended; "
            "control_rq_workers(ActionEnum.suspend); "
            f"sys.exit(0 if wait_for_rq_workers_suspended({timeout}, {poll}, "
            f"skip_stale={skip_stale}, stale_timeout={stale_timeout}) else 3)"
        )
        try:
            self._exec_frappe(f'{FMX_PYTHON} -c "{py}"')
        except Exception as e:
            # Exit 3 is the wait above giving up: the drain ran, workers are still
            # busy, and callers treat it as the gate closing. Any other status
            # means the command never got as far as waiting.
            if getattr(getattr(e, "output", None), "exit_code", None) == 3:
                return False
            raise DrainUnavailable(
                f"Could not drain RQ workers: {FMX_PYTHON} failed to run in the frappe container "
                f"({_exec_error_text(e)}). The image may predate fmx -- 'fm self update-images' "
                "installs one that supports draining."
            ) from e
        return True

    def resume_workers(self) -> None:
        if not self.workers_config.drain:
            return
        py = "from fmx.rq_controller import control_rq_workers, ActionEnum; control_rq_workers(ActionEnum.resume)"
        try:
            self._exec_frappe(f'{FMX_PYTHON} -c "{py}"')
        except Exception as e:
            self.output.warning(f"Could not resume RQ workers (continuing): {e}")

    def _unwind_maintenance(self) -> None:
        """Drop the maintenance 503 and un-suspend RQ after an aborted deploy.

        Shared by both abort windows -- pre-swap and the swap itself -- because
        the damage is identical either side of the swap: an abort that leaves
        ``maintenance_mode`` at 1 has the surviving old stack serve 503 to every
        visitor while the CLI reports the old image was kept, and `drain_workers`
        suspends RQ through a PERSISTENT Redis key, so skipping the resume leaves
        the bench silently processing no background jobs at all -- no scheduled
        jobs, no emails, no backups -- with the site otherwise healthy and
        nothing on screen to say why. `resume_workers` is already a no-op when
        draining is disabled and swallows its own failures, so it is safe on
        every abort path.
        """
        if self._frappe_running():
            with contextlib.suppress(Exception):
                self._set_maintenance(0)
        self.resume_workers()

    def _site_installed_apps(self) -> set[str]:
        """App names installed on the site, parsed from ``bench list-apps``.

        Returns an empty set when listing fails; callers must treat a set that
        lacks the always-present ``frappe`` app as unreliable (see
        :meth:`_install_new_apps`)."""
        try:
            result = self._exec_frappe(f"{BENCH_BIN} --site {self.site} list-apps")
        except Exception as e:
            self.output.warning(f"Could not list installed apps: {e}")
            return set()
        return _parse_installed_apps(getattr(result, "stdout", None))

    def _install_new_apps(self) -> None:
        """Install apps baked into the image but not yet on the site (``[switch].install_apps``).

        Currently unreachable, and deliberately left in place rather than deleted. The
        wanted set comes from ``config.apps_list``, which ``fm bake`` populates in memory
        only: it is deliberately not persisted to ``bench_config.toml`` (see
        ``BakeManager._derive_apps_list``). That was fine while one command baked and
        switched in a single process, but with ``fm bake`` and ``fm switch`` as separate
        commands the switch always loads an empty list, so nothing is reconciled. Making
        it work again means deriving the baked set from the new image itself rather than
        from bench config. Runs in the new container during finalize (under maintenance
        when migrating). Defensive: only installs when the installed set is read reliably
        (must contain ``frappe``) so a parse failure skips rather than blindly
        reinstalling every app."""
        if not self.switch_config.install_apps:
            return
        wanted = [a.name for a in (self.config.apps_list or [])]
        if not wanted:
            return
        installed = self._site_installed_apps()
        if "frappe" not in installed:
            self.output.warning(
                "Skipping new-app install: could not reliably read installed apps "
                "(no 'frappe' in `bench list-apps` output).",
            )
            return
        new = _new_apps(wanted, installed)
        if not new:
            return
        self.output.change_head(f"Installing new app(s) on site: {', '.join(new)}")
        for app in new:
            try:
                self._exec_frappe(f"{BENCH_BIN} --site {self.site} install-app {app}")
                self.output.print(f"Installed app '{app}' on site")
            except Exception as e:
                raise DeployError(f"Failed to install new app '{app}' on the site during finalize: {e}") from e

    def _apply_config_merges(self) -> None:
        """Merge ``[deploy].common_site_config`` / ``site_config`` keys into the
        site's ``common_site_config.json`` / ``site_config.json``.

        Both files are host-mounted data, so the new container picks up the merge
        immediately (clear-cache follows in finalize). ``save_dict_to_file``
        merges, so unrelated keys are preserved."""
        common = self.switch_config.common_site_config
        site = self.switch_config.site_config
        if common:
            self.output.change_head("Merging common_site_config keys")
            self.bench.set_common_bench_config(common)
        if site:
            self.output.change_head("Merging site_config keys")
            self.bench.set_bench_site_config(site)

    def _hook_script(self, value: str, deploy_tag: str) -> str:
        """``set -e`` + exported env + resolved content, so no exec env passthrough is needed."""
        core = {"SITE_NAME": self.site, "BENCH_PATH": str(self.bench_path), "DEPLOY_TAG": deploy_tag}
        if self._probe_result is not None:
            # migrate='auto' probe details, for hooks concerned with schema state.
            core["MIGRATE_PROBE"] = self._probe_result["verdict"]
            pending = self._probe_result["pending"]
            core["MIGRATE_PENDING_PATCHES"] = "unknown" if pending is None else str(pending)
            core["MIGRATE_APP_DRIFT"] = ",".join(self._probe_result["drift"]) or "none"
        if self._migrate_status is not None:
            core["MIGRATE_STATUS"] = self._migrate_status
        if self._migrate_log_container is not None:
            core["MIGRATE_LOG_FILE"] = self._migrate_log_container
            core["MIGRATE_LOG_FILE_HOST"] = str(self._migrate_log_host)
        env = hook_env(core, self.switch_config)
        return hook_script(value, env)

    def _run_host_hook(self, value: str | None, phase: str, deploy_tag: str) -> None:
        if not value:
            return
        self.output.change_head(f"Running {phase} hook (host)")
        with tempfile.NamedTemporaryFile("w", suffix=".sh", delete=False) as fh:
            fh.write(self._hook_script(value, deploy_tag))
            script_path = fh.name
        try:
            proc = subprocess.run(  # noqa: S603
                ["bash", script_path],  # noqa: S607
                cwd=str(self.bench_path),
                capture_output=True,
                text=True,
                check=False,
            )
            for line in (proc.stdout or "").splitlines():
                if line.strip():
                    self.output.print(line.strip())
            if proc.returncode != 0:
                raise DeployError(
                    f"{phase} hook (host) failed (exit {proc.returncode}): {(proc.stderr or '').strip()}",
                )
        finally:
            with contextlib.suppress(OSError):
                Path(script_path).unlink()

    def _run_container_hook(self, value: str | None, phase: str, deploy_tag: str) -> None:
        if not value:
            return
        if not self._frappe_running():
            self.output.warning(f"Skipping {phase} hook: no running frappe container.")
            return
        self.output.change_head(f"Running {phase} hook (container)")
        logs_dir = self.bench_path / "workspace" / "frappe-bench" / "logs"
        logs_dir.mkdir(parents=True, exist_ok=True)
        name = f".fm_hook_{phase}_{int(time.time())}.sh"
        host_script = logs_dir / name
        container_script = f"/workspace/frappe-bench/logs/{name}"
        host_script.write_text(self._hook_script(value, deploy_tag))
        try:
            result = self._exec_frappe(f"bash {container_script}")
            for line in getattr(result, "stdout", None) or []:
                if line.strip():
                    self.output.print(line.strip())
        except Exception as e:
            raise DeployError(f"{phase} hook (container) failed: {e}") from e
        finally:
            with contextlib.suppress(OSError):
                host_script.unlink()

    def _migrate(self, deploy_tag: str) -> bool:
        """Run bench migrate in a one-shot container from the newly-pinned image.

        The full migrate output is persisted to ``logs/deploy-migrate-<ts>.log``
        (bind-mounted: readable by host AND container hooks) and exported to hook
        env as MIGRATE_LOG_FILE / MIGRATE_LOG_FILE_HOST -- on failure too, so
        after_migrate notification hooks can ship the log.

        ``[switch].migrate_timeout`` is enforced INSIDE the container, by wrapping
        the command in coreutils ``timeout``: a wedged migrate (lock wait, hung
        patch) has to be killed where it runs, because the drain of a live child
        never returns and abandoning the wait on the host would leave the migrate
        running against the DB under an open maintenance window. ``<= 0`` means no
        budget (the pre-timeout behaviour).
        """
        self.output.change_head("Running migrations (one-shot new-image container)")
        command = self.switch_config.migrate_command or f"--site {self.site} migrate"
        budget = self.switch_config.migrate_timeout
        entrypoint, argv = (BENCH_BIN, command)
        if budget > 0:
            entrypoint, argv = ("timeout", f"{budget} {BENCH_BIN} {command}")
        log_name = f"deploy-migrate-{datetime.now(UTC).strftime('%Y%m%d%H%M%S')}.log"
        self._migrate_log_host = self.bench_path / "workspace" / "frappe-bench" / "logs" / log_name
        self._migrate_log_container = f"/workspace/frappe-bench/logs/{log_name}"

        def _persist(lines) -> None:
            with contextlib.suppress(OSError):
                self._migrate_log_host.write_text("\n".join(ln.rstrip("\n") for ln in lines or []))

        try:
            result = self.docker.compose.run(
                service=FRAPPE_SERVICE,
                entrypoint=entrypoint,
                command=argv,
                user="frappe",
                rm=True,
                stream=False,
            )
        except DockerException as e:
            out = getattr(e, "output", None)
            _persist(getattr(out, "combined", None) or [str(e)])
            if budget > 0 and getattr(out, "exit_code", None) == TIMEOUT_EXIT_CODE:
                raise DeployError(
                    f"Migration exceeded \\[switch].migrate_timeout ({budget}s) and was killed. "
                    f"The migrate log is at {self._migrate_log_host}; a lock wait or a hung patch "
                    "is the usual cause. Raise \\[switch].migrate_timeout (0 disables the budget) "
                    "and re-run -- bench migrate is resumable.",
                ) from e
            raise
        _persist(getattr(result, "combined", None))
        self.output.print("Migrations applied")
        return True

    def _notify_after_migrate(self, new_tag: str) -> None:
        """Failure-path after_migrate hooks (notifications): best-effort, container
        then host, with MIGRATE_STATUS=failed and the persisted migrate log in the
        hook env. A broken notification hook must never mask the migrate error."""
        for value, phase, runner in (
            (self._switch_hook("after_migrate"), "after_migrate", self._run_container_hook),
            (self._switch_hook("after_migrate", host=True), "host_after_migrate", self._run_host_hook),
        ):
            try:
                runner(value, phase, new_tag)
            except Exception as e:
                self.output.warning(f"{phase} hook failed on the migrate-failure path (continuing): {e}")

    def _probe_migrate_needed(self, new_tag: str) -> bool:
        """``migrate = "auto"``: probe the NEW image against the live site DB.

        Runs a one-shot container from the (already re-pinned) compose and feeds a
        base64-encoded script to the image's python with frappe initialized -- the
        same mechanism as ``fm shell --bench-console``. Migrate is needed when the
        new code has pending patches (patches.txt vs tabPatch Log) or app-version
        drift (code ``__version__`` vs tabInstalled Application). A failed or
        verdict-less probe returns True (conservative: full maintenance+migrate).
        """
        probe = f"""import sys
import os
os.chdir('/workspace/frappe-bench/sites')
sys.path.insert(0, '/workspace/frappe-bench/apps')
import frappe
frappe.init(site='{self.site}')
frappe.connect()
from frappe.modules.patch_handler import get_all_patches
executed = set(frappe.get_all("Patch Log", pluck="patch"))
pending = [p for p in map(str, get_all_patches()) if p not in executed]
installed = {{r.app_name: r.app_version for r in frappe.get_all("Installed Application", fields=["app_name", "app_version"])}}
drift = []
for app in frappe.get_installed_apps():
    code_v = getattr(frappe.get_module(app), "__version__", None)
    if code_v and installed.get(app) != code_v:
        drift.append(app)
status = "needed" if (pending or drift) else "clean"
print("{MIGRATE_PROBE_MARKER}", status, "pending=%d" % len(pending), "drift=%s" % (",".join(drift) or "none"))
"""
        encoded = base64.b64encode(probe.encode()).decode()
        assumed = {"needed": True, "pending": None, "drift": [], "verdict": "assumed-needed"}
        try:
            result = self.docker.compose.run(
                service=FRAPPE_SERVICE,
                entrypoint="/bin/bash",
                command=f"-c 'echo {encoded} | base64 -d | /workspace/frappe-bench/env/bin/python'",
                user="frappe",
                rm=True,
                stream=False,
            )
        except DockerException as e:
            self.output.warning(f"Migrate probe failed ({e}); assuming migrate is needed.")
            self._probe_result = assumed
            return True
        lines = list(getattr(result, "stdout", None) or []) + list(getattr(result, "stderr", None) or [])
        parsed = parse_migrate_probe(lines)
        if parsed is None:
            self.output.warning("Migrate probe produced no verdict; assuming migrate is needed.")
            self._probe_result = assumed
            return True
        parsed["verdict"] = "needed" if parsed["needed"] else "clean"
        self._probe_result = parsed
        marker = next(ln.strip() for ln in lines if MIGRATE_PROBE_MARKER in ln)
        self.output.print(f"Migrate probe: {marker}")
        return parsed["needed"]

    def _current_deployed_tag(self) -> str | None:
        state = self.config.deploy_state
        if state and state.current_tag:
            return state.current_tag
        return None

    def _record(self, new_tag: str, migrate_status: str, backup: Path | None = None) -> None:
        now = datetime.now(UTC).isoformat()
        state = self.config.deploy_state or DeployState()
        # Re-recording the tag that is ALREADY current (the health-gate rollback
        # re-pins the running old tag) must not rotate it into previous_tag:
        # previous would collapse onto current, turning the operator's next
        # escape hatch (`fm switch --previous`) into a redeploy of what is
        # already live and stranding the genuinely older release.
        if state.current_tag != new_tag:
            state.previous_tag = state.current_tag
        state.current_tag = new_tag
        state.last_deploy_at = now
        state.history.append(
            DeployStateEntry(
                tag=new_tag,
                deployed_at=now,
                migrate_status=migrate_status,
                backup=str(backup) if backup else None,
            ),
        )
        self.config.deploy_state = state
        self.config.export_to_toml(self._config_path())

    # ------------------------------------------------------------------ public

    def deploy(
        self,
        new_tag: str,
        rolling: bool | None = None,
        migrate_override: bool | None = None,
        restore_db_dump: Path | None = None,
        prune_keep: int | None = None,
    ) -> None:
        """Run the image deploy to ``new_tag``.

        Uses the rolling web swap when eligible (see ``rolling_eligible``) and
        the old stack is up; otherwise the recreate-swap. ``rolling`` is the
        ``--rolling/--no-rolling`` override.

        ``migrate_override`` overrides ``[switch].migrate`` for THIS run only
        (rollbacks pass False: old code must never migrate a newer schema).
        ``restore_db_dump`` imports the given dump at the quiesced point before
        the swap -- code and data go back together. A restore is a schema-grade
        step: it gates maintenance/rolling exactly like a migrate."""
        self._require_image_mode()
        old_tag = self._current_deployed_tag()

        # 1. Fetch (registry login+pull, or verify save_load-loaded image present)
        self.output.change_head(f"Fetching image {new_tag}")
        self._fetch_image(new_tag)

        # 2. Pre-flight boot check (nonzero => abort before any change)
        self.output.change_head("Pre-flight boot check")
        try:
            self.docker.run(
                image=new_tag,
                entrypoint=BENCH_BIN,
                command="version",
                workdir="/workspace/frappe-bench",
                user="frappe",
                rm=True,
                pull="never",
                stream=False,
            )
        except DockerException as e:
            raise DeployError(f"Pre-flight boot check failed for {new_tag}; aborting deploy: {e}") from e
        self.output.print("Pre-flight boot check passed")

        backup_dir = self.bench_path / "backups" / f"deploy-{datetime.now(UTC).strftime('%Y%m%d%H%M%S')}"
        db_dump: Path | None = None

        snaps = self._snapshot_compose()

        # 3. Render the image-mode compose pinned to the new tag. From here until
        # the swap, every abort path restores the snapshots (old stack serving).
        self.output.change_head("Rendering image-mode compose")
        self.docker_ops.render_image_compose(new_tag)
        self._pin_workers(new_tag)

        # 4. Resolve migrate: runtime override first, else config: explicit bool,
        # or 'auto' -> probe the NEW image against the live DB (pending patches /
        # app-version drift).
        requested = self.switch_config.migrate if migrate_override is None else migrate_override
        if requested == "auto":
            self.output.change_head("Probing new image for pending migrations")
            migrate = self._probe_migrate_needed(new_tag)
            self.output.print(f"Migrate probe verdict: {'migrate needed' if migrate else 'no migration needed'}")
        else:
            migrate = bool(requested)
        # A DB restore changes schema/data under running code the same way a
        # migrate does: same maintenance window, same rolling-eligibility rules.
        schema_step = migrate or restore_db_dump is not None
        # An EMPTY ``maintenance_mode_phases`` is the operator asserting the
        # migration is backward-compatible, i.e. "no page" -- the same assertion
        # that makes this deploy rolling-eligible below. Honour it here or the
        # 503 goes up anyway and the assertion only bought the rolling swap.
        maintenance = (
            schema_step and self.switch_config.maintenance_mode and bool(self.switch_config.maintenance_mode_phases)
        )

        do_rolling = (
            rolling_eligible(
                schema_step,
                self.switch_config.maintenance_mode,
                self.switch_config.maintenance_mode_phases,
                rolling,
            )
            and self._frappe_running()
        )
        if (rolling or do_rolling) and not self._frappe_running():
            self.output.warning(
                "Rolling swap requested but no running web to swap alongside; using recreate-swap.",
            )

        migrate_status = "skipped"
        try:
            # 5. Maintenance ON (only for schema-grade steps: migrate/restore)
            if maintenance and self._frappe_running():
                self.output.change_head("Enabling maintenance mode")
                self._set_maintenance(1)

            # 6. Drain workers (old container). A gate, like `fm restart`:
            # proceeding past a timed-out drain would take the backup while a
            # still-busy worker keeps writing, so the dump would no longer be
            # the exact quiesced state step 7 documents. Nothing irreversible
            # has happened yet (maintenance is unwound below): abort.
            # The resume is NOT done here: the pre-swap abort handler resumes on every abort path,
            # so doing it here too would just cost a second container exec.
            if not self.drain_workers():
                raise DeployError(
                    f"Drain timed out after {self.workers_config.drain_timeout}s: workers still busy. "
                    "Deploy aborted before backup/migrate/swap; workers resumed, the old stack keeps serving. "
                    "Raise \\[workers].drain_timeout, or set \\[workers].drain = false to deploy without "
                    "waiting (in-flight jobs die when the worker containers are replaced)."
                )

            # 7. Backup at the quiesced point: requests are already 503'd (when
            # migrating) and drained workers have finished writing, so the dump
            # is the exact pre-migrate state -- a rollback_db restore loses
            # nothing written between dump and migrate.
            requested_backup = self.switch_config.backup_db
            do_backup = schema_step if requested_backup == "auto" else bool(requested_backup)
            if do_backup:
                self.output.change_head("Backing up DB + site config")
                db_dump = self._backup(backup_dir)
            elif requested_backup == "auto":
                self.output.print("Backup skipped (backup_db=auto: no schema change)")

            # 7b. Restore a recorded dump (rollback path): after the insurance
            # backup of the CURRENT state, before migrate/swap.
            if restore_db_dump is not None:
                self._restore_db(restore_db_dump)

            # 8. Migrate in a one-shot new-image container.
            if migrate:
                self._run_host_hook(self._switch_hook("before_migrate", host=True), "host_before_migrate", new_tag)
                self._run_container_hook(self._switch_hook("before_migrate"), "before_migrate", new_tag)
                try:
                    self._migrate(new_tag)
                    migrate_status = self._migrate_status = "migrated"
                except (DockerException, DeployError) as e:
                    # Migrate failure: NO swap. Keep old tag + report. migrate is
                    # transactional/resumable so default is keep-old (re-runnable).
                    # DeployError is in the tuple because ``_migrate`` translates a
                    # migrate_timeout kill into one: it must land HERE (notify +
                    # rollback_db), not on the generic pre-swap abort above.
                    migrate_status = self._migrate_status = "failed"  # noqa: F841
                    self._notify_after_migrate(new_tag)
                    if self.switch_config.rollback_db and db_dump:
                        # A declined external-restore confirmation must not swallow the
                        # migrate failure below: that message is the one worth reading.
                        try:
                            self._restore_db(db_dump)
                        except RestoreNotConfirmed as declined:
                            self.output.warning(str(declined))
                    raise DeployError(
                        f"Migration failed; kept old image ({old_tag or 'dev/mount'}). "
                        f"Compose reverted, no swap performed. Re-run deploy after fixing: {e}",
                    ) from e
                self._run_container_hook(self._switch_hook("after_migrate"), "after_migrate", new_tag)
                self._run_host_hook(self._switch_hook("after_migrate", host=True), "host_after_migrate", new_tag)

            # Switch hooks (pre-restart): host first, then the still-running old container.
            self._run_host_hook(self._switch_hook("before_restart", host=True), "host_before_restart", new_tag)
            self._run_container_hook(self._switch_hook("before_restart"), "before_restart", new_tag)
        except Exception:
            # Abort BEFORE the swap (hook/migrate/maintenance/drain failure): the OLD
            # stack is still the live one. Revert the compose re-pin, then drop the
            # page and un-suspend RQ so an aborted deploy never leaves the site dark
            # or the compose half-switched (a later plain `compose up` must not jump
            # tags).
            self._restore_compose(snaps)
            self._unwind_maintenance()
            raise

        # 7b. Swap. Rolling when eligible -> zero dropped requests;
        # otherwise recreate-swap (the maintenance window covers the brief blip).
        # A failure IN the swap is its own abort window: the swap paths restore the
        # compose themselves (`_abort_rolling` for rolling; the recreate is already
        # pinned to the tag it brought up), but the page and the suspended workers
        # are this pipeline's to unwind -- otherwise the surviving stack serves 503
        # to everyone while the CLI reports the old image was kept.
        try:
            if do_rolling:
                self.output.change_head("Rolling web swap")
                self._rolling_swap(new_tag, old_tag, snaps)
            else:
                # Recreate-swap. No ``--wait``: nginx emerg-exits on the frappe:80
                # upstream DNS if it wins the startup race, so we gate on the frappe
                # curl health check and then (re)start nginx once frappe resolves.
                self.output.change_head("Swapping to new image (recreate)")
                self.docker.compose.up(services=[], detach=True, pull="never", stream=False)
                self._up_workers()
        except Exception:
            self._unwind_maintenance()
            raise

        # Health gate (503 = maintenance page = server up; finalize clears it).
        self.output.change_head("Health-gating new containers")
        if not self._health_check():
            if self.switch_config.rollback_image and old_tag:
                self.output.warning("New image unhealthy; rolling back to previous tag.")
                self.rollback(old_tag, restore_db_dump=db_dump if self.switch_config.rollback_db else None)
                raise DeployError(
                    f"Deploy of {new_tag} failed health check; rolled back to {old_tag}.",
                )
            raise DeployError(
                f"Deploy of {new_tag} failed health check and is halted in maintenance mode "
                f"(no previous tag to roll back to). Investigate the new containers.",
            )
        if not do_rolling:
            self._ensure_nginx()
        self.output.print("New containers are healthy")

        # 8. Finalize.
        self.output.change_head("Finalizing (resume workers, install new apps, clear cache, maintenance off)")
        self.resume_workers()
        self._install_new_apps()
        self._apply_config_merges()
        try:
            self._exec_frappe(f"{BENCH_BIN} --site {self.site} clear-cache")
        except Exception as e:
            self.output.warning(f"clear-cache failed (continuing): {e}")
        # Switch hooks (post-restart): new container first, then host. The swap has
        # already happened -- a failing post hook must not leave the site in
        # maintenance or the deploy unrecorded (rollback bookkeeping stays truthful).
        try:
            self._run_container_hook(self._switch_hook("after_restart"), "after_restart", new_tag)
            self._run_host_hook(self._switch_hook("after_restart", host=True), "host_after_restart", new_tag)
        except DeployError:
            if maintenance:
                with contextlib.suppress(Exception):
                    self._set_maintenance(0)
            self._record(new_tag, migrate_status, backup=db_dump)
            raise

        if maintenance:
            self._set_maintenance(0)

        # 9. Record.
        self._record(new_tag, migrate_status, backup=db_dump)
        self.output.print(f"Deployed {new_tag}", emoji_code=":rocket:")

        # Opt-in housekeeping: prune old releases only when the caller asked
        # (--keep N); never by default, and never failing a successful deploy.
        if prune_keep is not None:
            try:
                self.prune_releases(keep=prune_keep)
            except Exception as e:
                self.output.warning(f"Release prune failed (continuing): {e}")

    def rolling_restart(self) -> None:
        """Zero-downtime web-tier recreate on the CURRENT tag (`fm restart --rolling`).

        Same engine as the deploy swap, pointed at the running tag: new frappe +
        nginx replicas come up alongside the old ones, health-gate, drain, rename
        back to canonical. No migrate, no hooks, no record -- nothing about the
        release changes, only the containers are fresh.
        """
        self._require_image_mode()
        tag = self._current_deployed_tag()
        if not tag:
            raise DeployError("No deployed image tag recorded; bake and switch first (fm bake, then fm switch).")
        if not self._frappe_running():
            raise DeployError("Web tier is not running; use a plain start/restart instead of --rolling.")

        snaps = self._snapshot_compose()
        self._fetch_image(tag)
        self._rolling_swap(tag, tag, snaps)
        self.output.print(f"Rolling restart complete on {tag}", emoji_code=":arrows_counterclockwise:")

    def prune_releases(self, keep: int | None = None, dry_run: bool = False) -> dict:
        """Prune old releases: history rows, recorded DB-dump dirs, local image tags.

        History rows: keep the newest ``keep`` (default
        ``[switch] keep_releases``). Artifacts are refcounted
        separately: a recorded backup's ``deploy-*`` dir is deleted only when
        no kept row references it; an image tag is rmi'd (app + paired -nginx,
        best-effort) only when neither a kept row nor the protected set
        (current/previous/seed/base) references it.
        Invoked by ``fm prune``, or after a successful deploy/switch when
        ``--keep N`` was passed. ``dry_run`` only reports.
        """
        state = self.config.deploy_state
        summary: dict = {"entries": 0, "backups": [], "images": [], "kept": 0}
        if not state or not state.history:
            return summary

        limit = self.switch_config.keep_releases if keep is None else keep
        protected = {
            tag
            for tag in (
                state.current_tag,
                state.previous_tag,
                self.config.seed_image,
                getattr(self.config, "base_image", None),
            )
            if tag
        }
        kept, pruned = plan_release_prune(state.history, limit)
        summary["kept"] = len(kept)
        if not pruned:
            return summary
        summary["entries"] = len(pruned)

        backups, tags = plan_artifact_removal(kept, pruned, protected)
        for backup in backups:
            backup_dir = Path(backup).parent
            if backup_dir.name.startswith("deploy-") and backup_dir.exists():
                summary["backups"].append(str(backup_dir))

        from frappe_manager.site_manager.modules.bake import BakeManager

        for tag in tags:
            summary["images"].extend([tag, BakeManager.nginx_image_tag(tag)])

        if dry_run:
            return summary

        for backup_dir in summary["backups"]:
            shutil.rmtree(backup_dir, ignore_errors=True)
        for image in summary["images"]:
            with contextlib.suppress(DockerException):
                self.docker.rmi(image, stream=False)

        state.history = kept
        self.config.deploy_state = state
        self.config.export_to_toml(self._config_path())
        self.output.print(
            f"Pruned {summary['entries']} old release(s): {len(summary['backups'])} backup dir(s), "
            f"{len(summary['images'])} image tag(s); kept {summary['kept']}.",
            emoji_code=":broom:",
        )
        return summary

    def rollback(self, previous_tag: str, restore_db_dump: Path | None = None) -> None:
        """INTERNAL health-gate recovery: re-pin to ``previous_tag`` and recreate.

        Called only from ``deploy()`` when the new stack fails its health gate
        (``rollback_image``) -- deliberately minimal (no probe/hooks/backup/drain)
        because it runs mid-failure. User-facing rollback is ``fm switch
        --previous`` (the full pipeline pointed backwards). ``restore_db_dump``
        (``rollback_db``) is imported BEFORE the swap.
        """
        self._require_image_mode()
        self.output.change_head(f"Rolling back to {previous_tag}")

        self._fetch_image(previous_tag)

        self.docker_ops.render_image_compose(previous_tag)
        self._pin_workers(previous_tag)

        if restore_db_dump is not None:
            # Declining the DB import is a decision about someone else's database,
            # not a reason to leave the bench on the image that just failed its
            # health gate. The image rollback continues either way.
            try:
                self._restore_db(restore_db_dump)
            except RestoreNotConfirmed as declined:
                self.output.warning(str(declined))

        self.docker.compose.up(services=[], detach=True, pull="never", stream=False)
        self._up_workers()

        if not self._health_check():
            # The compose IS pinned to previous_tag at this point; record reality
            # so deploy_state matches what a later `compose up` would run.
            self._record(previous_tag, "rollback")
            raise DeployError(
                f"Rollback to {previous_tag} failed health check; bench halted. Investigate the containers.",
            )
        self._ensure_nginx()

        self.resume_workers()
        try:
            self._exec_frappe(f"{BENCH_BIN} --site {self.site} set-config -g maintenance_mode 0")
        except Exception as e:
            self.output.warning(f"Could not clear maintenance mode (continuing): {e}")

        self._record(previous_tag, "rollback")
        state = self.config.deploy_state
        if state and state.previous_tag:
            self.output.print(
                f"Previous tag is now {state.previous_tag} -- running `fm rollback` again would re-deploy it.",
                emoji_code=":information:",
            )
        self.output.print(f"Rolled back to {previous_tag}", emoji_code=":rewind:")
