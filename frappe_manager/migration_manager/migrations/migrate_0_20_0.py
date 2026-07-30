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
"""

import shutil

import tomlkit
from ruamel.yaml import YAML

from frappe_manager.migration_manager.migration_base import MigrationBase
from frappe_manager.migration_manager.migration_helpers import MigrationBench
from frappe_manager.migration_manager.version import Version
from frappe_manager.utils.helpers import get_template_path

ADMINER_VOLUMES = [
    "./workspace/frappe-bench/sites:/fm-sites:ro",
    "./configs/adminer:/var/www/html/plugins-enabled:ro",
]


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
