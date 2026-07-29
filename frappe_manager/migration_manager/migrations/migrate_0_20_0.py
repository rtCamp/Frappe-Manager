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

Real client IPs (bench nginx):

- places configs/nginx/conf/custom/real-ip.conf so bench nginx restores the
  visitor's address from X-Real-IP for traffic arriving from the fm frontend
  network, instead of logging and rate limiting everything as the proxy's IP
"""

import shutil

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
        # Real-ip conf applies to every bench, before the admin-tools early
        # returns below.
        self._place_realip_conf(bench)

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
