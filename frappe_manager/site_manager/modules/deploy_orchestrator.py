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
import shutil
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
    DeploymentMode,
    DeployState,
    DeployStateEntry,
)

BENCH_BIN = "/opt/user/.bin/bench"
FRAPPE_SERVICE = "frappe"
# fmx is installed as a uv tool; a bare `python` is not on the exec PATH.
FMX_PYTHON = "/opt/uv-tools/fmx/bin/python"


class DeployError(Exception):
    """Raised when an image deploy cannot proceed or fails."""


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
        if self.config.deployment_mode != DeploymentMode.image:
            raise DeployError(
                f"Bench '{self.site}' is not in image deployment mode "
                f"(deployment_mode={self.config.deployment_mode.value}). Set deployment_mode = 'image'.",
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

    def _image_present(self, tag: str) -> bool:
        repo, _, tagpart = tag.rpartition(":")
        try:
            for img in self.docker.images():
                if img.get("Repository") == repo and img.get("Tag") == tagpart:
                    return True
        except Exception:
            return False
        return False

    def _fetch_image(self, tag: str) -> None:
        """Ensure ``tag`` (+ its derived nginx tag) is present on the target daemon.

        registry mode: ``docker login`` (when creds set) then ``docker pull`` any
        missing tags — so a remote daemon (via ``DOCKER_HOST``) gets the image.
        save_load mode: the images must be transported+loaded beforehand; a
        missing tag is a hard error here. local/absent registry: pull if missing.
        """
        from frappe_manager.site_manager.modules.bake import BakeManager
        from frappe_manager.site_manager.modules.transport import registry_login

        nginx_tag = BakeManager.nginx_image_tag(tag)
        missing = [t for t in (tag, nginx_tag) if not self._image_present(t)]
        if not missing:
            return

        registry = getattr(self.config, "registry", None)
        distribution = registry.distribution if registry else "registry"
        if distribution == "save_load":
            raise DeployError(
                f"Image(s) {', '.join(missing)} not present and distribution='save_load'; "
                "transport the image(s) (docker save/load) to this daemon before switching.",
            )

        registry_login(self.docker, registry, output=self.output)
        for t in missing:
            self.output.print(f"Fetching {t} from registry")
            try:
                self.docker.pull(t, stream=False)
            except DockerException as e:
                # The nginx image is optional (absent when the bench has no assets).
                if t == nginx_tag:
                    self.output.warning(f"Could not pull nginx image {t} (continuing): {e}")
                    continue
                raise DeployError(f"Failed to fetch image {t} from registry: {e}") from e

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
        info = self._worker_services()
        if not info:
            return
        cfm, _dc, svcs = info
        repo, _, tagpart = deploy_tag.rpartition(":")
        cfm.set_all_images({svc: {"name": repo, "tag": tagpart} for svc in svcs})
        _apply_image_mounts(cfm, self.site, svcs)
        cfm.write_to_file()

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

    def deploy(self, new_tag: str) -> None:
        """Run the full recreate-swap deploy to ``new_tag``."""
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

        # 7b. Recreate-swap. No ``--wait``: nginx emerg-exits on the frappe:80
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
        self._ensure_nginx()
        self.output.print("New containers are healthy")

        # 8. Finalize.
        self.output.change_head("Finalizing (resume workers, clear cache, maintenance off)")
        self._resume_workers()
        try:
            self._exec_frappe(f"{BENCH_BIN} --site {self.site} clear-cache")
        except Exception as e:
            self.output.warning(f"clear-cache failed (continuing): {e}")
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
