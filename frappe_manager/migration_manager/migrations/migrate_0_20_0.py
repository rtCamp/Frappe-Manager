"""
Migration for v0.20.0.

Admin tools: Adminer 4 → 5 with the FM login plugin.

- adminer image: adminer:4 → adminer:5 (upstream revived; ships the pure-PHP
  redis driver used by the plugin)
- drops the ADMINER_DEFAULT_SERVER env — login targets are now discovered at
  request time by the plugin from the mounted sites directory
- adds read-only bind mounts: sites dir (live credentials) and configs/adminer
  (plugin dir mounted over the container's plugins-enabled)
- places configs/adminer/000-fm-login.php — one-click login cards for each
  site database and the bench redis instances, plus the stock manual form

Real client IPs + JSON access logs (bench nginx):

- places configs/nginx/conf/custom/real-ip.conf so bench nginx restores the
  visitor's address from X-Real-IP for traffic arriving from the fm frontend
  network, instead of logging and rate limiting everything as the proxy's IP
- deletes the generated configs/nginx/conf/conf.d/default.conf so the nginx
  entrypoint re-renders it from the new image template, which logs JSON in the
  same format as the global proxy

HTTP basic auth (bench nginx):

- moves the old top-level admin_tools_username / admin_tools_password keys in
  bench_config.toml into the new [auth] table (web = false, tools = true), the
  single credential pair that now drives both auth surfaces
- drops the renamed configs/nginx/conf/http_auth/<bench>-admin-tools.htpasswd;
  the new <bench>.htpasswd is written on the next start

Global database engine:

- moves global-db from mariadb:10.6, which reached end of life on 2026-07-06, to
  the tag frappe's own CI tests against, and lets the image entrypoint upgrade the
  system tables via MARIADB_AUTO_UPGRADE
"""


import gzip
import shutil
from collections.abc import MutableMapping
from pathlib import Path

import tomlkit
from ruamel.yaml import YAML

from frappe_manager import GLOBAL_DB_IMAGE
from frappe_manager.migration_manager.migration_base import MigrationBase
from frappe_manager.migration_manager.migration_helpers import MigrationBench
from frappe_manager.migration_manager.version import Version
from frappe_manager.output_manager.context_managers import spinner
from frappe_manager.services_manager.database_service_manager import DatabaseServerServiceInfo, MariaDBManager
from frappe_manager.utils.helpers import get_template_path

# Dropped from the engine command list: it was only ever needed on MariaDB
# 10.6.1 to 10.6.5, where innodb_read_only_compressed defaulted to ON and frappe's
# COMPRESSED core tables became read-only. The engine defaults it off again from
# 10.6.6 onward, so on any tag fm now pins it is a no-op.
STALE_ENGINE_FLAG = "--skip-innodb-read-only-compressed"

# Scratch path INSIDE the engine container, not on the host: the dump is written
# there and copied out with `compose cp`, which needs no extra bind mount and
# leaves nothing behind once the container is recreated.
CONTAINER_TMP = Path("/tmp")  # noqa: S108

ADMINER_VOLUMES = [
    "./workspace/frappe-bench/sites:/fm-sites:ro",
    "./configs/adminer:/var/www/html/plugins-enabled:ro",
]


def rewrite_global_db_service(engine: MutableMapping, image: str = GLOBAL_DB_IMAGE) -> None:
    """Point a global-db compose service at ``image``, in place.

    Pure and idempotent so the compose surgery can be reasoned about (and tested)
    without Docker: applying it twice is the same as applying it once.

    - the stale compressed-tables flag goes, since the engine defaults it off from
      10.6.6 onward
    - MARIADB_AUTO_UPGRADE is added, but never overwritten: an operator who set it
      to 0 deliberately keeps that choice
    - every other key is left exactly as found
    """
    engine["image"] = image

    command = engine.get("command")
    if command and STALE_ENGINE_FLAG in command:
        command.remove(STALE_ENGINE_FLAG)

    environment = engine.get("environment")
    if environment is None:
        # Without an environment mapping there is nowhere to put the auto-upgrade
        # switch, and the engine would boot on the new version with the previous
        # one's system tables. Create it rather than silently skip.
        environment = {}
        engine["environment"] = environment
    environment.setdefault("MARIADB_AUTO_UPGRADE", 1)


class MigrationV0200(MigrationBase):
    version = Version("0.20.0")

    def migrate_bench(self, bench: MigrationBench):
        # Bench nginx config applies to every bench, before the admin-tools
        # early returns below.
        self._place_realip_conf(bench)
        self._refresh_nginx_default_conf(bench)
        self._move_admin_tools_credentials(bench)

        compose_path = bench.path / "docker-compose.admin-tools.yml"
        if not compose_path.exists():
            return

        self.backup_manager.backup(compose_path, bench_name=bench.name)

        yaml = YAML()
        yaml.preserve_quotes = True
        compose_data = yaml.load(compose_path.read_text())

        adminer = (compose_data.get("services") or {}).get("adminer")
        if adminer is None:
            return

        adminer["image"] = "adminer:5"

        environment = adminer.get("environment")
        if environment is not None:
            environment.pop("ADMINER_DEFAULT_SERVER", None)
            if not environment:
                del adminer["environment"]

        adminer["volumes"] = ADMINER_VOLUMES

        # Update x-version to current version (plain semver — no ``v`` prefix)
        compose_data["x-version"] = str(self.version)

        with compose_path.open("w") as f:
            yaml.dump(compose_data, f)

        self._place_adminer_plugin(bench)
        self.output.print(f"Updated admin tools (Adminer 5 + login plugin) for {bench.name}")

    def _place_adminer_plugin(self, bench: MigrationBench):
        adminer_config_dir = bench.path / "configs" / "adminer"
        adminer_config_dir.mkdir(parents=True, exist_ok=True)
        plugin_template = get_template_path("adminer/000-fm-login.php")
        (adminer_config_dir / "000-fm-login.php").write_bytes(plugin_template.read_bytes())

    def _place_realip_conf(self, bench: MigrationBench):
        from frappe_manager import CLI_SERVICES_DIRECTORY
        from frappe_manager.site_manager.modules.realip import build_bench_realip_conf

        subnet = None
        try:
            yaml = YAML()
            data = yaml.load((CLI_SERVICES_DIRECTORY / "docker-compose.yml").read_text())
            ipam = ((data.get("networks") or {}).get("global-frontend-network") or {}).get("ipam") or {}
            subnet = (ipam.get("config") or [{}])[0].get("subnet")
        except Exception:
            subnet = None
        if not subnet:
            try:
                from frappe_manager.utils.network import detect_running_network

                info = detect_running_network()
                subnet = info.get("subnet_cidr") if info else None
            except Exception:
                subnet = None
        if not subnet:
            return
        conf_dir = bench.path / "configs" / "nginx" / "conf" / "custom"
        conf_dir.mkdir(parents=True, exist_ok=True)
        (conf_dir / "real-ip.conf").write_text(build_bench_realip_conf(str(subnet)))
        self.output.print(f"Placed bench nginx real-ip conf for {bench.name}")

    def _refresh_nginx_default_conf(self, bench: MigrationBench):
        """Drop the generated default.conf so the entrypoint re-renders it from
        the new image template (JSON access log). Regenerating it is routine in
        fm (see bench_orchestrator), and every host-side addition lives in
        conf.d/ or custom/ instead of in this file."""
        default_conf = bench.path / "configs" / "nginx" / "conf" / "conf.d" / "default.conf"
        if not default_conf.exists():
            return
        self.backup_manager.backup(default_conf, bench_name=bench.name)
        default_conf.unlink()
        self.output.print(f"Removed generated nginx default.conf for {bench.name} (re-rendered on start)")

    def _move_admin_tools_credentials(self, bench: MigrationBench):
        """Move the old top-level admin tools credentials into the [auth] table.

        The per-bench htpasswd file was renamed to <bench>.htpasswd, so the
        admin-tools one is dropped here; Bench.ensure_fm_nginx_confs() writes
        the new one on the next start or compose regeneration.
        """
        old_htpasswd = bench.path / "configs" / "nginx" / "conf" / "http_auth" / f"{bench.name}-admin-tools.htpasswd"
        if old_htpasswd.exists():
            old_htpasswd.unlink()

        config_path = bench.path / "bench_config.toml"
        if not config_path.exists():
            return

        doc = tomlkit.parse(config_path.read_text())
        if "admin_tools_username" not in doc and "admin_tools_password" not in doc:
            return

        old_user = doc.get("admin_tools_username")
        old_password = doc.get("admin_tools_password")

        if "admin_tools_username" in doc:
            del doc["admin_tools_username"]
        if "admin_tools_password" in doc:
            del doc["admin_tools_password"]

        # An [auth] table already present wins: it is the newer format.
        if "auth" not in doc:
            auth = tomlkit.table()
            auth["user"] = str(old_user) if old_user else "admin"
            if old_password:
                auth["password"] = str(old_password)
            auth["web"] = False
            auth["tools"] = True
            doc["auth"] = auth

        config_path.write_text(tomlkit.dumps(doc))
        self.output.print(f"Moved admin tools credentials into [auth] for {bench.name}")

    def migrate_services(self):
        self._upgrade_global_db_engine()

    def _upgrade_global_db_engine(self):
        """Move global-db onto the engine tag frappe tests against.

        New installs get it straight from the services template. Existing ones are
        only ever moved here, deliberately and once, because an InnoDB datadir
        upgrade cannot be undone: a downgrade needs the dump this step takes first.
        A direct 10.6 to 11.x jump is supported for a single node (the one-major-at-
        a-time rule applies to rolling Galera upgrades), and the engine's own
        MARIADB_AUTO_UPGRADE handles the system tables on first boot.
        """
        compose_file_manager = self.services_manager.compose_file_manager

        if not compose_file_manager.exists():
            self.logger.debug("[_upgrade_global_db_engine] services compose not found, skipping")
            return

        services = compose_file_manager.yml.get("services") or {}
        engine = services.get("global-db")

        if not engine or "image" not in engine:
            self.logger.debug("[_upgrade_global_db_engine] no global-db image to upgrade, skipping")
            return

        current_image = str(engine["image"])

        if current_image == GLOBAL_DB_IMAGE:
            self.logger.debug(f"[_upgrade_global_db_engine] already on {GLOBAL_DB_IMAGE}")
            return

        self.output.print(f"Upgrading global database engine: {current_image} -> {GLOBAL_DB_IMAGE}")

        database_manager = MariaDBManager(
            DatabaseServerServiceInfo.import_from_compose_file("global-db", compose_file_manager),
            compose_file_manager,
            self.services_manager.docker,
            output_handler=self.output,
        )

        dump_path = self._dump_whole_engine(database_manager)

        # A version change is only safe from a clean shutdown; crash recovery across
        # engine versions is not supported. compose stop sends SIGTERM, which is what
        # the server treats as a graceful shutdown request.
        with spinner(self.output, "Stopping global-db for the engine upgrade"):  # type: ignore[arg-type]
            self.services_manager.compose.stop(services=["global-db"], timeout=120)

        rewrite_global_db_service(engine)

        compose_file_manager.write_to_file()

        with spinner(self.output, f"Starting global-db on {GLOBAL_DB_IMAGE}"):  # type: ignore[arg-type]
            self.services_manager.compose.up(
                services=["global-db"],
                force_recreate=True,
                detach=True,
                pull="missing",
            )
            database_manager.wait_till_db_start()

        self.output.print(f"Global database engine is now {GLOBAL_DB_IMAGE}")
        self.output.print(f"Pre-upgrade dump of every database kept at {dump_path}")
        self.output.warning(
            f"{GLOBAL_DB_IMAGE} is the engine frappe v16 tests against. Benches still on frappe v15 will print a "
            "MariaDB version warning when creating or restoring a site, because v15 is tested on 10.6 and warns from "
            "10.9 up. Nothing else changes for them, and a v15 bench that needs an older engine can be pointed at its "
            "own database server instead of the shared one.",
        )

    def _dump_whole_engine(self, database_manager: MariaDBManager) -> Path:
        """Logical backup of the entire server, taken while the old engine still runs.

        This is the rollback path: the datadir upgrade is one way, so without this
        there is no route back to the previous engine.
        """
        # Same timestamp the migration's other backups use, so one run's artifacts
        # group together instead of drifting by a second.
        dump_name = f"global-db-all-databases-{self.backup_manager.migration_timestamp}.sql"

        container_dump_path = CONTAINER_TMP / dump_name
        host_dump_path = self.backup_manager.backup_dir / dump_name

        with spinner(self.output, "Backing up every database before the engine upgrade"):  # type: ignore[arg-type]
            database_manager.db_export_all(container_dump_path)
            self.services_manager.compose.cp(
                f"global-db:{container_dump_path}",
                str(host_dump_path),
                stream=False,
            )

            compressed_dump_path = host_dump_path.with_suffix(".sql.gz")
            with host_dump_path.open("rb") as plain, gzip.open(compressed_dump_path, "wb") as compressed:
                shutil.copyfileobj(plain, compressed)
            host_dump_path.unlink()

        return compressed_dump_path

    def undo_services_migrate(self):
        """Put the compose file back; the datadir stays on the newer engine.

        A restored compose alone would point an older engine at a datadir it cannot
        read, so this only rewinds the file and tells the operator where the dump is.
        Rolling the data back is a deliberate restore, not something to do implicitly
        during a rollback.
        """
        compose_path = self.services_manager.compose_file_manager.compose_path

        for backup in self.backup_manager.backups:
            if backup.src == compose_path:
                self.backup_manager.restore(backup, force=True)
                self.output.print("Restored the services compose file")
                break

        self.output.warning(
            "The global database datadir was upgraded in place and is NOT rolled back. To return to the previous "
            f"engine, restore the dump in {self.backup_manager.backup_dir} into a fresh datadir.",
        )

    def undo_bench_migrate(self, bench: MigrationBench):
        compose_path = bench.path / "docker-compose.admin-tools.yml"

        for backup in self.backup_manager.backups:
            if backup.src == compose_path:
                self.backup_manager.restore(backup, force=True)
                self.output.print(f"Restored admin tools compose for {bench.name}")
                break

        adminer_config_dir = bench.path / "configs" / "adminer"
        if adminer_config_dir.exists():
            shutil.rmtree(adminer_config_dir)
