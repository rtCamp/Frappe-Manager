"""DeployOrchestrator - image-based switch/rollback pipeline (Phase 4 v1).

Implements the decomposed image deploy (recreate-swap) that ``fmx restart
--migrate`` cannot express in image mode:

    fetch -> pre-flight -> backup -> maintenance(if migrate) -> drain(old)
    -> render image compose(new tag) -> migrate(one-shot, new image)
    -> recreate-swap(compose up -d --wait) -> finalize(resume + site DB ops +
    maintenance off) -> record deploy_state

v1 uses recreate-swap (the maintenance window covers the brief web restart);
rolling scale-2 is deferred to Phase 4b. Supervisor stays.
"""

import contextlib
import re
import shutil
import subprocess
import tempfile
import time
from datetime import UTC, datetime
from pathlib import Path

from frappe_manager.docker import DockerException, DockerVolumeMount, DockerVolumeType
from frappe_manager.logger import log
from frappe_manager.logger.context import LoggerContext
from frappe_manager.logger.contextual import ContextualLogger
from frappe_manager.output_manager import OutputHandler
from frappe_manager.output_manager.rich_output import RichOutputHandler
from frappe_manager.services_manager.database_service_manager import (
    DatabaseServerServiceInfo,
    MariaDBManager,
)
from frappe_manager.site_manager.bench_config import (
    BenchRuntime,
    DeployState,
    DeployStateEntry,
)
from frappe_manager.site_manager.hooks import hook_env, hook_script
from frappe_manager.utils.docker import run_command_with_exit_code

BENCH_BIN = "/opt/user/.bin/bench"
FRAPPE_SERVICE = "frappe"
# fmx is installed as a uv tool; a bare `python` is not on the exec PATH.
FMX_PYTHON = "/opt/uv-tools/fmx/bin/python"


class DeployError(Exception):
    """Raised when an image deploy cannot proceed or fails."""


def rolling_eligible(
    migrate: bool,
    maintenance_mode_phases: list[str],
    override: bool | None = None,
) -> bool:
    """Decide whether a deploy may use the rolling (blue-green) web swap.

    ``override`` is the CLI ``--rolling/--no-rolling`` flag (None = auto). In auto
    mode a deploy is rolling-eligible only when it cannot break old code that
    shares the DB during the overlap: either it does not migrate at all, or the
    operator has asserted a backward-compatible (additive) migration by clearing
    ``maintenance_mode_phases``. Migrate deploys with a maintenance window keep
    the existing recreate-swap path (Decision 1)."""
    if override is not None:
        return override
    return migrate is False or maintenance_mode_phases == []


def _apply_image_mounts(compose_file_manager, site: str, services: list[str]) -> None:
    """Replace the wholesale ``./workspace:/workspace`` bind on ``services`` with
    data-only binds so the immutable image's baked code/assets are not shadowed."""
    compose_path = compose_file_manager.compose_path
    sites_rel = "./workspace/frappe-bench/sites"
    specs = [
        (f"{sites_rel}/{site}", f"/workspace/frappe-bench/sites/{site}"),
        (f"{sites_rel}/common_site_config.json", "/workspace/frappe-bench/sites/common_site_config.json"),
        (f"{sites_rel}/apps.txt", "/workspace/frappe-bench/sites/apps.txt"),
        ("./workspace/frappe-bench/logs", "/workspace/frappe-bench/logs"),
        ("./workspace/frappe-bench/config", "/workspace/frappe-bench/config"),
    ]
    for svc in services:
        existing = compose_file_manager.get_service_volumes(svc)
        kept = [v for v in existing if str(v.container) != "/workspace"]
        data = [
            DockerVolumeMount(host=h, container=c, type=DockerVolumeType.bind, compose_path=compose_path)
            for h, c in specs
        ]
        compose_file_manager.set_service_volumes(svc, kept + data)


def pin_workers_to_image(workers, site: str, deploy_tag: str) -> None:
    """Pin the workers compose services to ``deploy_tag`` and swap in data-only
    image mounts. No-op when the bench has no workers compose."""
    if not workers.compose_path.exists():
        return
    cfm = workers.compose_file_manager
    svcs = cfm.get_services_list()
    if not svcs:
        return
    repo, _, tagpart = deploy_tag.rpartition(":")
    cfm.set_all_images({svc: {"name": repo, "tag": tagpart} for svc in svcs})
    _apply_image_mounts(cfm, site, svcs)
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


class DeployOrchestrator:
    """Runs the image deploy / rollback pipeline for a single bench."""

    def __init__(self, bench, output_handler: OutputHandler | None = None, logger: ContextualLogger | None = None):
        self.bench = bench
        self.config = bench.bench_config
        self.deploy_config = self.config.deploy
        self.site = bench.name
        self.bench_path = Path(bench.path)
        self.docker = bench.docker_client
        self.compose = bench.compose_file_manager
        self.docker_ops = bench.docker_ops
        self.output = output_handler or RichOutputHandler()
        self.logger = logger or ContextualLogger(
            log.get_logger(),
            context=LoggerContext(bench=bench.name, operation="deploy"),
        )

    # ------------------------------------------------------------------ helpers

    def _require_image_mode(self) -> None:
        if self.config.runtime != BenchRuntime.image:
            raise DeployError(
                f"Bench '{self.site}' is not in image runtime "
                f"(runtime={self.config.runtime.value}). Set runtime = 'image'.",
            )
        if self.deploy_config is None or not self.deploy_config.image:
            raise DeployError("No [deploy].image configured; cannot deploy an image.")

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

        Delegates to the shared ``transport.fetch_image``; ``TransportError`` is
        re-raised as ``DeployError`` to preserve deploy's error contract.
        """
        from frappe_manager.site_manager.modules.transport import TransportError, fetch_image

        try:
            fetch_image(self.docker, getattr(self.config, "registry", None), tag, output=self.output)
        except TransportError as e:
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
        builds compose commands directly. Inherits ``DOCKER_HOST`` from the
        surrounding deploy env, so it targets the same (possibly remote) daemon."""
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
        adds the new replica; the compose file is already pinned to the new tag."""
        args = ["up", "-d", "--no-recreate", "--pull", "never"]
        for svc, n in scales.items():
            args += ["--scale", f"{svc}={n}"]
        out = self._raw_compose(*args)
        if out.exit_code not in (0, None):
            raise DeployError(f"compose scale {scales} failed: {''.join(out.stderr) or ''.join(out.stdout)}")

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
        self, web: list[str], old_ids: dict[str, list[str]], old_tag: str | None, snaps: dict[Path, bytes],
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
        """Blue-green web swap: run new + old web replicas concurrently, drain old,
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
            self._abort_rolling(web, old_ids, old_tag, snaps)
            raise DeployError("new frappe replica failed health check; kept old, no swap")

        # 3. Add the new nginx replica (now resolves both frappe replicas).
        self.output.change_head("Rolling: starting new nginx replica")
        self._scale({"frappe": 2, "nginx": 2})
        new_nginx = self._new_container_id("nginx", old_ids["nginx"])
        if not new_nginx or not self._container_health(new_nginx):
            self._abort_rolling(web, old_ids, old_tag, snaps)
            raise DeployError("new nginx replica failed health check; kept old, no swap")

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

    def _db_manager(self) -> tuple[MariaDBManager, str | None]:
        db_info = DatabaseServerServiceInfo.import_from_bench(
            bench_name=self.site, bench_path=self.bench_path, raise_exception=False,
        )
        db_name = db_info.name or self.config.db_name
        manager = MariaDBManager(
            db_info, self.compose, self.docker, run_on_compose_service=FRAPPE_SERVICE, output_handler=self.output,
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
        self.output.change_head("Restoring database backup")
        manager.db_import(db_name, db_dump, force=True)

    def _drain_workers(self) -> None:
        if not (self.deploy_config.drain_workers and self._frappe_running()):
            return
        self.output.change_head("Draining RQ workers")
        timeout = self.deploy_config.drain_workers_timeout
        poll = self.deploy_config.drain_workers_poll
        skip_stale = self.deploy_config.skip_stale_workers
        py = (
            "from fmx.rq_controller import control_rq_workers, ActionEnum, wait_for_rq_workers_suspended; "
            "control_rq_workers(ActionEnum.suspend); "
            f"wait_for_rq_workers_suspended({timeout}, {poll}, skip_stale={skip_stale})"
        )
        try:
            self._exec_frappe(f'{FMX_PYTHON} -c "{py}"')
        except Exception as e:
            # Draining is best-effort; recreate-swap recreates workers regardless.
            self.output.warning(f"Worker drain did not complete cleanly (continuing): {e}")

    def _resume_workers(self) -> None:
        if not self.deploy_config.drain_workers:
            return
        py = (
            "from fmx.rq_controller import control_rq_workers, ActionEnum; "
            "control_rq_workers(ActionEnum.resume)"
        )
        try:
            self._exec_frappe(f'{FMX_PYTHON} -c "{py}"')
        except Exception as e:
            self.output.warning(f"Could not resume RQ workers (continuing): {e}")

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
        """Install apps baked into the image but not yet on the site (``[deploy].install_apps``).

        ``config.apps_list`` is populated by bake's ``_derive_apps_list`` on the
        ``fm deploy`` path; on ``switch``/``rollback`` (no bake) it is empty and
        nothing is reconciled. Runs in the new container during finalize (under
        maintenance when migrating). Defensive: only installs when the installed
        set is read reliably (must contain ``frappe``) so a parse failure skips
        rather than blindly reinstalling every app."""
        if not self.deploy_config.install_apps:
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
        common = self.deploy_config.common_site_config
        site = self.deploy_config.site_config
        if common:
            self.output.change_head("Merging common_site_config keys")
            self.bench.set_common_bench_config(common)
        if site:
            self.output.change_head("Merging site_config keys")
            self.bench.set_bench_site_config(site)

    def _hook_script(self, value: str, deploy_tag: str) -> str:
        """``set -e`` + exported env + resolved content, so no exec env passthrough is needed."""
        env = hook_env(
            self.deploy_config,
            {"SITE_NAME": self.site, "BENCH_PATH": str(self.bench_path), "DEPLOY_TAG": deploy_tag},
        )
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
        """Run bench migrate in a one-shot container from the newly-pinned image."""
        self.output.change_head("Running migrations (one-shot new-image container)")
        command = self.deploy_config.migrate_command or f"--site {self.site} migrate"
        self.docker.compose.run(
            service=FRAPPE_SERVICE,
            entrypoint=BENCH_BIN,
            command=command,
            user="frappe",
            rm=True,
            stream=False,
        )
        self.output.print("Migrations applied")
        return True

    def _current_deployed_tag(self) -> str | None:
        state = self.config.deploy_state
        if state and state.current_tag:
            return state.current_tag
        return None

    def _record(self, new_tag: str, migrate_status: str) -> None:
        now = datetime.now(UTC).isoformat()
        state = self.config.deploy_state or DeployState()
        state.previous_tag = state.current_tag
        state.current_tag = new_tag
        state.last_deploy_at = now
        state.history.append(DeployStateEntry(tag=new_tag, deployed_at=now, migrate_status=migrate_status))
        self.config.deploy_state = state
        self.config.export_to_toml(self._config_path())

    # ------------------------------------------------------------------ public

    def deploy(self, new_tag: str, rolling: bool | None = None) -> None:
        """Run the image deploy to ``new_tag``.

        Uses the rolling (blue-green) web swap when eligible (see
        ``rolling_eligible``) and the old stack is up; otherwise the
        recreate-swap. ``rolling`` is the ``--rolling/--no-rolling`` override."""
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

        migrate = bool(self.deploy_config.migrate)
        maintenance = migrate and self.deploy_config.maintenance_mode
        backup_dir = self.bench_path / "backups" / f"deploy-{datetime.now(UTC).strftime('%Y%m%d%H%M%S')}"
        db_dump: Path | None = None

        do_rolling = (
            rolling_eligible(migrate, self.deploy_config.maintenance_mode_phases, rolling)
            and self._frappe_running()
        )
        if (rolling or do_rolling) and not self._frappe_running():
            self.output.warning(
                "Rolling swap requested but no running web to swap alongside; using recreate-swap.",
            )

        # 3. Backup
        if self.deploy_config.backups:
            self.output.change_head("Backing up DB + site config")
            db_dump = self._backup(backup_dir)

        # 4. Maintenance ON (only when migrating)
        if maintenance and self._frappe_running():
            self.output.change_head("Enabling maintenance mode")
            self._set_maintenance(1)

        # 5. Drain workers (old container)
        self._drain_workers()

        snaps = self._snapshot_compose()

        # 6/7a. Render image-mode compose pinned to the new tag (before migrate).
        self.output.change_head("Rendering image-mode compose")
        self.docker_ops.render_image_compose(new_tag)
        self._pin_workers(new_tag)

        # 6. Migrate in a one-shot new-image container.
        migrate_status = "skipped"
        if migrate:
            try:
                self._migrate(new_tag)
                migrate_status = "migrated"
            except DockerException as e:
                # Migrate failure: NO swap. Keep old tag + report. migrate is
                # transactional/resumable so default is keep-old (re-runnable).
                self._restore_compose(snaps)
                if self.deploy_config.restore_on_failure and db_dump:
                    self._restore_db(db_dump)
                if self._frappe_running():
                    with contextlib.suppress(Exception):
                        self._set_maintenance(0)
                raise DeployError(
                    f"Migration failed; kept old image ({old_tag or 'dev/mount'}). "
                    f"Compose reverted, no swap performed. Re-run deploy after fixing: {e}",
                ) from e

        # Switch hooks (pre-restart): host first, then the still-running old container.
        self._run_host_hook(self.deploy_config.host_before_restart, "host_before_restart", new_tag)
        self._run_container_hook(self.deploy_config.before_restart, "before_restart", new_tag)

        # 7b. Swap. Rolling (blue-green) when eligible -> zero dropped requests;
        # otherwise recreate-swap (the maintenance window covers the brief blip).
        if do_rolling:
            self.output.change_head("Rolling (blue-green) web swap")
            self._rolling_swap(new_tag, old_tag, snaps)
        else:
            # Recreate-swap. No ``--wait``: nginx emerg-exits on the frappe:80
            # upstream DNS if it wins the startup race, so we gate on the frappe
            # curl health check and then (re)start nginx once frappe resolves.
            self.output.change_head("Swapping to new image (recreate)")
            self.docker.compose.up(services=[], detach=True, pull="never", stream=False)
            self._up_workers()

        # Health gate (503 = maintenance page = server up; finalize clears it).
        self.output.change_head("Health-gating new containers")
        if not self._health_check():
            if self.deploy_config.rollback and old_tag:
                self.output.warning("New image unhealthy; rolling back to previous tag.")
                self.rollback(old_tag, _restore_db_dump=db_dump if self.deploy_config.restore_on_failure else None)
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
        self._resume_workers()
        self._install_new_apps()
        self._apply_config_merges()
        try:
            self._exec_frappe(f"{BENCH_BIN} --site {self.site} clear-cache")
        except Exception as e:
            self.output.warning(f"clear-cache failed (continuing): {e}")
        # Switch hooks (post-restart): new container first, then host.
        self._run_container_hook(self.deploy_config.after_restart, "after_restart", new_tag)
        self._run_host_hook(self.deploy_config.host_after_restart, "host_after_restart", new_tag)

        if maintenance:
            self._set_maintenance(0)

        # 9. Record.
        self._record(new_tag, migrate_status)
        self.output.print(f"Deployed {new_tag}", emoji_code=":rocket:")

    def rollback(self, previous_tag: str, _restore_db_dump: Path | None = None) -> None:
        """Re-pin the compose to ``previous_tag`` and recreate (no migrate)."""
        self._require_image_mode()
        self.output.change_head(f"Rolling back to {previous_tag}")

        self._fetch_image(previous_tag)

        self.docker_ops.render_image_compose(previous_tag)
        self._pin_workers(previous_tag)

        self.docker.compose.up(services=[], detach=True, pull="never", stream=False)
        self._up_workers()

        if not self._health_check():
            raise DeployError(
                f"Rollback to {previous_tag} failed health check; bench halted. Investigate the containers.",
            )
        self._ensure_nginx()

        self._resume_workers()
        try:
            self._exec_frappe(f"{BENCH_BIN} --site {self.site} set-config -g maintenance_mode 0")
        except Exception as e:
            self.output.warning(f"Could not clear maintenance mode (continuing): {e}")

        self._record(previous_tag, "rollback")
        self.output.print(f"Rolled back to {previous_tag}", emoji_code=":leftwards_arrow:")
