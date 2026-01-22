"""
Migration for v0.19.0 - Rolling migration window start.

v0.19.0 supports migrations from v0.18.0 and later only.
Users on older versions must upgrade to v0.18.0 first.

BREAKING CHANGES:
- SSL: [ssl] table → [[ssl_certificates]] array, preferred_challenge → challenge_type
- Docker: SITENAME → SITE_MAPPINGS env var, v0.18.0 → v0.19.0 images
- Config: alias_domains, upload_limit, restart_policy, use_uv fields added
- Runtime: pyenv/nvm → uv/fnm, certbot → acme.sh
"""

from frappe_manager.migration_manager.migration_base import MigrationBase
from frappe_manager.migration_manager.version import Version
from frappe_manager.migration_manager.migration_helpers import MigrationBench
from frappe_manager.display_manager.DisplayManager import richprint
from frappe_manager.docker.subprocess_output import SubprocessOutput
from pathlib import Path
import tomlkit
from typing import Any, Dict, cast
from ruamel.yaml import YAML
import shlex


class MigrationV0190(MigrationBase):
    version = Version("0.19.0")

    def migrate_bench(self, bench: MigrationBench):
        """Migrate bench from v0.18.0 to v0.19.0."""
        richprint.change_head(f"Migrating bench configuration for {bench.name}")

        bench_config_path = bench.path / "bench_config.toml"
        if bench_config_path.exists():
            self._migrate_bench_config_toml(bench, bench_config_path)

        docker_compose_path = bench.path / "docker-compose.yml"
        if docker_compose_path.exists():
            self._migrate_docker_compose_yml(bench, docker_compose_path)

        workers_compose_path = bench.path / "docker-compose.workers.yml"
        if workers_compose_path.exists():
            self._migrate_workers_compose_yml(bench, workers_compose_path)

        self._rebuild_runtime_environment(bench)

        richprint.print(f"[green]✓[/green] Successfully migrated {bench.name}")

    def _migrate_bench_config_toml(self, bench: MigrationBench, config_path: Path):
        """Transform bench_config.toml: [ssl] → [[ssl_certificates]], add new fields."""
        richprint.print(f"  • Migrating bench_config.toml")

        content = config_path.read_text()
        doc = tomlkit.parse(content)

        self._transform_ssl_config(doc, bench.name)
        self._add_new_config_fields(doc)

        config_path.write_text(tomlkit.dumps(doc))
        richprint.print(f"    [green]✓[/green] Updated SSL configuration format")

    def _transform_ssl_config(self, doc: tomlkit.TOMLDocument, bench_name: str):
        """Transform [ssl] table to [[ssl_certificates]] array with proper field names."""
        if "ssl" not in doc:
            return

        old_ssl = doc["ssl"]

        if isinstance(old_ssl, list):
            return

        old_ssl_dict = cast(Dict[str, Any], old_ssl)

        ssl_type_value = old_ssl_dict.get("ssl_type", "letsencrypt")
        hsts_value = old_ssl_dict.get("hsts", "off")

        challenge_type_value = old_ssl_dict.get("preferred_challenge") or old_ssl_dict.get("challenge_type") or "http01"

        ssl_cert: Dict[str, Any] = {
            "domain": str(bench_name),
            "ssl_type": ssl_type_value,
            "acme_client": "acme.sh",
            "hsts": hsts_value,
            "challenge_type": challenge_type_value,
        }

        self._move_dns_credentials(doc, old_ssl_dict)

        del doc["ssl"]
        doc["ssl_certificates"] = [ssl_cert]

    def _move_dns_credentials(self, doc: tomlkit.TOMLDocument, old_ssl_dict: Dict[str, Any]):
        """Move api_token/api_key from ssl to dns_providers.cloudflare if present."""
        api_token = old_ssl_dict.get("api_token")
        api_key = old_ssl_dict.get("api_key")

        if not (api_token or api_key):
            return

        dns_providers_table = tomlkit.table()
        cloudflare_table = tomlkit.table()

        if api_token:
            cloudflare_table["api_token"] = api_token
        if api_key:
            cloudflare_table["api_key"] = api_key

        dns_providers_table["cloudflare"] = cloudflare_table
        doc["dns_providers"] = dns_providers_table

        richprint.print(f"    [green]✓[/green] Migrated DNS credentials to dns_providers.cloudflare")

    def _add_new_config_fields(self, doc: tomlkit.TOMLDocument):
        """Add alias_domains, upload_limit, restart_policy, use_uv if missing."""
        if "alias_domains" not in doc:
            doc["alias_domains"] = []

        if "upload_limit" not in doc:
            doc["upload_limit"] = "50M"

        if "restart_policy" not in doc:
            env_type = doc.get("environment_type", "prod")
            doc["restart_policy"] = "unless-stopped" if env_type == "prod" else "no"

        if "use_uv" not in doc:
            doc["use_uv"] = True

    def _migrate_docker_compose_yml(self, bench: MigrationBench, compose_path: Path):
        """Update compose: v0.18.0 → v0.19.0 images, SITENAME → SITE_MAPPINGS."""
        richprint.print(f"  • Migrating docker-compose.yml")

        yaml = YAML(typ="rt")
        yaml.preserve_quotes = True
        yaml.default_flow_style = False

        with open(compose_path, 'r') as f:
            compose_data = yaml.load(f)

        if not compose_data or "services" not in compose_data:
            return

        services = compose_data["services"]

        self._update_service_images(services)
        self._transform_nginx_environment(services)

        with open(compose_path, 'w') as f:
            yaml.dump(compose_data, f)

    def _update_service_images(self, services: Dict[str, Any]):
        """Replace v0.18.0 image tags with v0.19.0."""
        for service_name, service_config in services.items():
            if "image" not in service_config:
                continue

            old_image = service_config["image"]
            if "v0.18.0" in old_image:
                service_config["image"] = old_image.replace("v0.18.0", "v0.19.0")
                richprint.print(f"    [green]✓[/green] Updated {service_name} image to v0.19.0")

    def _transform_nginx_environment(self, services: Dict[str, Any]):
        """Transform nginx SITENAME → SITE_MAPPINGS environment variable."""
        if "nginx" not in services or "environment" not in services["nginx"]:
            return

        nginx_env = services["nginx"]["environment"]

        if isinstance(nginx_env, dict):
            self._transform_nginx_env_dict(nginx_env)
        elif isinstance(nginx_env, list):
            services["nginx"]["environment"] = self._transform_nginx_env_list(nginx_env)

    def _transform_nginx_env_dict(self, nginx_env: Dict[str, Any]):
        """Transform dict format: {SITENAME: value} → {SITE_MAPPINGS: value}."""
        if "SITENAME" in nginx_env:
            nginx_env["SITE_MAPPINGS"] = nginx_env.pop("SITENAME")
            richprint.print(f"    [green]✓[/green] Migrated SITENAME → SITE_MAPPINGS")

    def _transform_nginx_env_list(self, nginx_env: list) -> list:
        """Transform list format: [SITENAME=value] → [SITE_MAPPINGS=value]."""
        new_env = []
        for env_var in nginx_env:
            if isinstance(env_var, str) and env_var.startswith("SITENAME="):
                sitename_value = env_var.split("=", 1)[1]
                new_env.append(f"SITE_MAPPINGS={sitename_value}")
                richprint.print(f"    [green]✓[/green] Migrated SITENAME → SITE_MAPPINGS")
            else:
                new_env.append(env_var)
        return new_env

    def _migrate_workers_compose_yml(self, bench: MigrationBench, compose_path: Path):
        """Update worker compose: v0.18.0 → v0.19.0 images."""
        richprint.print(f"  • Migrating docker-compose.workers.yml")

        yaml = YAML(typ="rt")
        yaml.preserve_quotes = True
        yaml.default_flow_style = False

        with open(compose_path, 'r') as f:
            compose_data = yaml.load(f)

        if not compose_data or "services" not in compose_data:
            return

        self._update_service_images(compose_data["services"])

        with open(compose_path, 'w') as f:
            yaml.dump(compose_data, f)

    def undo_bench_migrate(self, bench: MigrationBench):
        """Rollback handled by BackupManager.restore()."""
        richprint.change_head(f"Rolling back migration for {bench.name}")
        richprint.print(f"[yellow]↻[/yellow] Rollback complete for {bench.name}")

    def migrate_services(self):
        """No global services changes in v0.19.0."""
        richprint.print("No global services migration needed for v0.19.0")

    def undo_services_migrate(self):
        """No global services rollback needed."""
        richprint.print("No services rollback needed for v0.19.0")

    def _rebuild_runtime_environment(self, bench: MigrationBench):
        """
        Rebuild runtime environment after pyenv/nvm → uv/fnm migration.

        Uses docker compose run to execute commands safely without requiring
        running services. Performs the same operations as fm update --python/--node.
        """
        richprint.change_head(f"Rebuilding runtime environment (pyenv/nvm → uv/fnm)")

        bench_config_path = bench.path / "bench_config.toml"
        if not bench_config_path.exists():
            richprint.print("  • No bench_config.toml found, skipping runtime rebuild")
            return

        config_content = bench_config_path.read_text()
        config_doc = tomlkit.parse(config_content)

        python_version = config_doc.get("python_version")
        node_version = config_doc.get("node_version")

        if not python_version and not node_version:
            richprint.print("  • No Python/Node versions configured, skipping runtime rebuild")
            return

        try:
            if python_version:
                richprint.print(f"  • Setting up Python {python_version} with uv...")
                self._setup_python_with_uv(bench, python_version)

            if node_version:
                richprint.print(f"  • Setting up Node {node_version} with fnm...")
                self._setup_node_with_fnm(bench, node_version)

            richprint.print(f"  • Reinstalling apps and rebuilding assets...")
            self._reinstall_apps_and_rebuild(bench)

            richprint.print(f"  • Regenerating supervisor configuration...")
            self._regenerate_supervisor_config(bench)

            if bench.compose_project.running:
                richprint.print(f"  • Restarting services...")
                self._restart_services(bench)

            richprint.print(f"[green]✓[/green] Runtime environment rebuilt successfully")

        except Exception as e:
            richprint.error(f"Failed to rebuild runtime environment: {e}")
            richprint.warning("Runtime may need manual rebuild using: fm update --python X --node Y")

    def _setup_python_with_uv(self, bench: MigrationBench, python_version: str):
        """Setup Python using uv python manager."""
        setup_script = f"""
cd /workspace/frappe-bench
if [ -d env ]; then
    echo "Backing up old venv..."
    rm -rf env.bak 2>/dev/null || true
    mv env env.bak
fi

echo "Installing Python {python_version} via uv..."
uv python install cpython-{python_version}

echo "Detecting installed Python..."
PYTHON_DIR=$(ls -1d /workspace/.uv/python/cpython-{python_version}* 2>/dev/null | sort -V | tail -1 || echo "")
if [ -z "$PYTHON_DIR" ]; then
    echo "Error: Could not find installed Python"
    exit 1
fi
PYTHON_BASENAME=$(basename "$PYTHON_DIR")

echo "Updating python-default symlink..."
cd /workspace/.uv
rm -f python-default
ln -sf "python/$PYTHON_BASENAME" python-default

echo "Creating new venv with $PYTHON_BASENAME..."
cd /workspace/frappe-bench
uv venv env --python "$PYTHON_BASENAME" --seed --link-mode=copy

echo "Python environment setup complete"
"""
        result = bench.compose_project.compose.run(
            service="frappe",
            command=f"bash -c {shlex.quote(setup_script)}",
            rm=True,
            user="frappe",
            stream=False,
        )

        # Type narrowing: when stream=False, result is SubprocessOutput
        if not isinstance(result, SubprocessOutput):
            raise Exception("Unexpected streaming output received")

        if result.exit_code != 0:
            raise Exception(f"Python setup failed with exit code {result.exit_code}")

    def _setup_node_with_fnm(self, bench: MigrationBench, node_version: str):
        """Setup Node using fnm node manager."""
        setup_script = f"""
echo "Checking if Node {node_version} is installed..."
if fnm list | grep -q "v{node_version}"; then
    echo "Node {node_version} already installed"
else
    echo "Installing Node {node_version} via fnm..."
    fnm install {node_version}
fi

echo "Setting Node {node_version} as default..."
fnm default {node_version}

echo "Ensuring yarn is installed..."
if [ ! -f "/workspace/.fnm/node-versions/v{node_version}/installation/bin/yarn" ]; then
    npm install -g yarn
fi

echo "Node environment setup complete"
"""
        result = bench.compose_project.compose.run(
            service="frappe",
            command=f"bash -c {shlex.quote(setup_script)}",
            rm=True,
            user="frappe",
            stream=False,
        )

        if not isinstance(result, SubprocessOutput):
            raise Exception("Unexpected streaming output received")

        if result.exit_code != 0:
            raise Exception(f"Node setup failed with exit code {result.exit_code}")

    def _reinstall_apps_and_rebuild(self, bench: MigrationBench):
        """Reinstall apps into new venv and rebuild static assets."""
        apps_txt_path = bench.path / "workspace" / "frappe-bench" / "sites" / "apps.txt"

        if not apps_txt_path.exists():
            richprint.warning("    No apps.txt found, skipping app reinstallation")
            return

        installed_apps = [line.strip() for line in apps_txt_path.read_text().splitlines() if line.strip()]

        if not installed_apps:
            richprint.warning("    No apps found in apps.txt")
            return

        reinstall_script = """
cd /workspace/frappe-bench

echo "Reinstalling apps into new venv..."
while IFS= read -r app; do
    if [ -d "apps/$app" ]; then
        echo "Installing $app..."
        uv pip install --python env/bin/python --no-cache-dir -e "apps/$app" || \
        ./env/bin/pip install --no-cache-dir -e "apps/$app"
    fi
done < sites/apps.txt

echo "Installing Node dependencies..."
bench setup requirements --node

echo "Building static assets..."
bench build

echo "Apps reinstalled and assets built successfully"
"""
        result = bench.compose_project.compose.run(
            service="frappe",
            command=f"bash -c {shlex.quote(reinstall_script)}",
            rm=True,
            user="frappe",
            stream=False,
        )

        if not isinstance(result, SubprocessOutput):
            raise Exception("Unexpected streaming output received")

        if result.exit_code != 0:
            raise Exception(f"App reinstallation failed with exit code {result.exit_code}")

    def _regenerate_supervisor_config(self, bench: MigrationBench):
        """Regenerate supervisor configuration with updated paths."""
        setup_script = """
cd /workspace/frappe-bench
bench setup supervisor --skip-redis --skip-supervisord --yes --user frappe
echo "Supervisor configuration regenerated"
"""
        result = bench.compose_project.compose.run(
            service="frappe",
            command=f"bash -c {shlex.quote(setup_script)}",
            rm=True,
            user="frappe",
            stream=False,
        )

        if not isinstance(result, SubprocessOutput):
            raise Exception("Unexpected streaming output received")

        if result.exit_code != 0:
            raise Exception(f"Supervisor setup failed with exit code {result.exit_code}")

    def _restart_services(self, bench: MigrationBench):
        """Restart running services to pick up new runtime."""
        try:
            bench.compose_project.compose.restart(services=["frappe", "socketio"], timeout=10, stream=False)

            if bench.workers_compose_project.running:
                bench.workers_compose_project.compose.restart(services=["schedule"], timeout=10, stream=False)
        except Exception as e:
            richprint.warning(f"Service restart failed: {e}")
            richprint.warning("Please restart services manually: fm restart {bench.name}")
