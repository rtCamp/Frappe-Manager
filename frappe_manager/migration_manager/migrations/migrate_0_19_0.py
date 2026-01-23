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

    def bench_basic_backup(self, bench: MigrationBench):
        """
        Override parent to add additional backups for runtime rebuild.
        
        Backs up:
        - bench_config.toml (modified with detected versions)
        - supervisor.conf and *.fm.supervisor.conf (regenerated during rebuild)
        - env/ directory (Python venv, will be recreated)
        """
        super().bench_basic_backup(bench)
        
        bench_config_path = bench.path / "bench_config.toml"
        if bench_config_path.exists():
            self.backup_manager.backup(bench_config_path, bench_name=bench.name)
            richprint.print(f"  • Backed up bench_config.toml")
        
        supervisor_config_dir = bench.path / "workspace" / "frappe-bench" / "config"
        if supervisor_config_dir.exists():
            supervisor_conf = supervisor_config_dir / "supervisor.conf"
            if supervisor_conf.exists():
                self.backup_manager.backup(supervisor_conf, bench_name=bench.name)
                richprint.print(f"  • Backed up supervisor.conf")
            
            for conf_file in supervisor_config_dir.glob("*.fm.supervisor.conf"):
                self.backup_manager.backup(conf_file, bench_name=bench.name)
                richprint.print(f"  • Backed up {conf_file.name}")
        
        env_dir = bench.path / "workspace" / "frappe-bench" / "env"
        if env_dir.exists() and env_dir.is_dir():
            import shutil
            env_backup_path = bench.path / "workspace" / "frappe-bench" / "env.backup.migration"
            if env_backup_path.exists():
                shutil.rmtree(env_backup_path)
            shutil.move(str(env_dir), str(env_backup_path))
            richprint.print(f"  • Moved env/ to env.backup.migration")

    def undo_bench_migrate(self, bench: MigrationBench):
        """
        Rollback bench changes on migration failure.
        
        Restores env/ by moving env.backup.migration back to env/.
        Much faster than copying since env/ can be hundreds of MB.
        """
        import shutil
        env_dir = bench.path / "workspace" / "frappe-bench" / "env"
        env_backup_path = bench.path / "workspace" / "frappe-bench" / "env.backup.migration"
        
        if env_backup_path.exists():
            if env_dir.exists():
                richprint.print(f"  • Removing new env/")
                shutil.rmtree(env_dir)
            
            richprint.print(f"  • Restoring env/ from env.backup.migration")
            shutil.move(str(env_backup_path), str(env_dir))

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

    def migrate_services(self):
        """
        Pull v0.19.0 Docker images (system-level infrastructure).
        
        Images are shared resources across all benches - must be pulled
        at system level before any bench can use them.
        """
        richprint.change_head("Pulling Docker images for v0.19.0")
        
        from frappe_manager.utils.site import pull_docker_images
        
        self.logger.info("[migrate_services] Starting Docker image pull for v0.19.0")
        
        try:
            success = pull_docker_images()
            
            if not success:
                error_msg = "Failed to pull one or more Docker images"
                self.logger.error(f"[migrate_services] {error_msg}")
                richprint.error(error_msg)
                raise Exception(error_msg)
            
            richprint.print("[green]✓[/green] All v0.19.0 images pulled successfully")
            self.logger.info("[migrate_services] Docker image pull completed successfully")
            
        except Exception as e:
            self.logger.error(f"[migrate_services] Image pull failed: {e}", exc_info=True)
            raise Exception(f"Failed to pull Docker images: {e}") from e

    def undo_services_migrate(self):
        """No global services rollback needed."""
        richprint.print("No services rollback needed for v0.19.0")

    def _rebuild_runtime_environment(self, bench: MigrationBench):
        """Rebuild Python/Node environment using uv/fnm (v0.19.0 runtime system)."""
        richprint.change_head(f"Rebuilding runtime environment (pyenv/nvm → uv/fnm)")
        
        self.logger.info(f"[_rebuild_runtime_environment] Starting runtime rebuild for {bench.name}")

        bench_config_path = bench.path / "bench_config.toml"
        
        python_version = None
        node_version = None
        
        config_doc = None
        if bench_config_path.exists():
            config_doc = tomlkit.parse(bench_config_path.read_text())
            python_version = config_doc.get("python_version")
            node_version = config_doc.get("node_version")
            self.logger.debug(f"[_rebuild_runtime_environment] From config: Python={python_version}, Node={node_version}")
        
        if not python_version or not node_version:
            richprint.print(f"  • No Python/Node versions in config, auto-detecting from container and Frappe requirements...")
            self.logger.info(f"[_rebuild_runtime_environment] Auto-detecting versions...")
            python_version, node_version = self._auto_detect_runtime_versions(bench)
            self.logger.info(f"[_rebuild_runtime_environment] Auto-detected: Python={python_version}, Node={node_version}")
            
            if config_doc and (python_version or node_version):
                if python_version:
                    config_doc["python_version"] = python_version
                if node_version:
                    config_doc["node_version"] = node_version
                bench_config_path.write_text(tomlkit.dumps(config_doc))
                richprint.print(f"  • Updated bench_config.toml with detected versions")

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
            self.logger.info(f"[_rebuild_runtime_environment] Completed successfully for {bench.name}")

        except Exception as e:
            self.logger.error(f"[_rebuild_runtime_environment] Failed for {bench.name}: {e}", exc_info=True)
            richprint.error(f"Failed to rebuild runtime environment: {e}")
            richprint.warning("Runtime rebuild is required for v0.19.0 migration")
            raise Exception(f"Runtime environment rebuild failed: {e}") from e

    def _setup_python_with_uv(self, bench: MigrationBench, python_version: str):
        """Setup Python using uv python manager."""
        self.logger.debug(f"[_setup_python_with_uv] Starting Python {python_version} setup for {bench.name}")
        
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
echo "Verifying env directory..."
ls -la env/ || echo "ERROR: env directory not found!"
"""
        self.logger.debug(f"[_setup_python_with_uv] Executing docker compose run...")
        
        result = bench.compose_project.compose.run(
            service="frappe",
            command=f"-c {shlex.quote(setup_script)}",
            rm=True,
            user="frappe",
            entrypoint="bash",
            stream=False,
        )

        if not isinstance(result, SubprocessOutput):
            self.logger.error(f"[_setup_python_with_uv] Unexpected streaming output received")
            raise Exception("Unexpected streaming output received")

        self.logger.debug(f"[_setup_python_with_uv] Exit code: {result.exit_code}")
        self.logger.debug(f"[_setup_python_with_uv] Output: {result.combined}")

        if result.exit_code != 0:
            self.logger.error(f"[_setup_python_with_uv] Python setup failed with exit code {result.exit_code}")
            self.logger.error(f"[_setup_python_with_uv] Output: {result.combined}")
            raise Exception(f"Python setup failed with exit code {result.exit_code}")
        
        self.logger.debug(f"[_setup_python_with_uv] Python setup completed successfully")

    def _setup_node_with_fnm(self, bench: MigrationBench, node_version: str):
        """Setup Node using fnm node manager."""
        self.logger.debug(f"[_setup_node_with_fnm] Starting Node {node_version} setup for {bench.name}")
        
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
        self.logger.debug(f"[_setup_node_with_fnm] Executing docker compose run...")
        
        result = bench.compose_project.compose.run(
            service="frappe",
            command=f"-c {shlex.quote(setup_script)}",
            rm=True,
            user="frappe",
            entrypoint="bash",
            stream=False,
        )

        if not isinstance(result, SubprocessOutput):
            self.logger.error(f"[_setup_node_with_fnm] Unexpected streaming output received")
            raise Exception("Unexpected streaming output received")

        self.logger.debug(f"[_setup_node_with_fnm] Exit code: {result.exit_code}")
        self.logger.debug(f"[_setup_node_with_fnm] Output: {result.combined}")

        if result.exit_code != 0:
            self.logger.error(f"[_setup_node_with_fnm] Node setup failed with exit code {result.exit_code}")
            self.logger.error(f"[_setup_node_with_fnm] Output: {result.combined}")
            raise Exception(f"Node setup failed with exit code {result.exit_code}")
        
        self.logger.debug(f"[_setup_node_with_fnm] Node setup completed successfully")

    def _reinstall_apps_and_rebuild(self, bench: MigrationBench):
        """Reinstall apps into new venv and rebuild static assets."""
        self.logger.debug(f"[_reinstall_apps_and_rebuild] Starting for {bench.name}")
        
        apps_txt_path = bench.path / "workspace" / "frappe-bench" / "sites" / "apps.txt"

        if not apps_txt_path.exists():
            self.logger.warning(f"[_reinstall_apps_and_rebuild] No apps.txt found at {apps_txt_path}")
            richprint.warning("    No apps.txt found, skipping app reinstallation")
            return

        installed_apps = [line.strip() for line in apps_txt_path.read_text().splitlines() if line.strip()]

        if not installed_apps:
            self.logger.warning(f"[_reinstall_apps_and_rebuild] No apps in apps.txt")
            richprint.warning("    No apps found in apps.txt")
            return

        self.logger.debug(f"[_reinstall_apps_and_rebuild] Found apps: {installed_apps}")

        reinstall_script = """
cd /workspace/frappe-bench

echo "Reinstalling apps into new venv..."
while IFS= read -r app; do
    if [ -d "apps/$app" ]; then
        echo "Installing $app..."
        uv pip install --python env/bin/python --no-cache-dir -e "apps/$app" || \
        ./env/bin/python install --no-cache-dir -e "apps/$app"
    fi
done < sites/apps.txt

echo "Installing Node dependencies..."
bench setup requirements --node

echo "Building static assets..."
bench build

echo "Apps reinstalled and assets built successfully"
"""
        self.logger.debug(f"[_reinstall_apps_and_rebuild] Executing docker compose run...")
        
        result = bench.compose_project.compose.run(
            service="frappe",
            command=f"-c {shlex.quote(reinstall_script)}",
            rm=True,
            user="frappe",
            entrypoint="bash",
            stream=False,
        )

        if not isinstance(result, SubprocessOutput):
            self.logger.error(f"[_reinstall_apps_and_rebuild] Unexpected streaming output received")
            raise Exception("Unexpected streaming output received")

        self.logger.debug(f"[_reinstall_apps_and_rebuild] Exit code: {result.exit_code}")
        output_str = " ".join(result.combined)
        self.logger.debug(f"[_reinstall_apps_and_rebuild] Output length: {len(output_str)} chars")

        if result.exit_code != 0:
            self.logger.error(f"[_reinstall_apps_and_rebuild] Failed with exit code {result.exit_code}")
            self.logger.error(f"[_reinstall_apps_and_rebuild] Full output: {output_str}")
            raise Exception(f"App reinstallation failed with exit code {result.exit_code}")
        
        self.logger.debug(f"[_reinstall_apps_and_rebuild] Completed successfully")

    def _regenerate_supervisor_config(self, bench: MigrationBench):
        """Regenerate supervisor configuration with updated paths."""
        setup_script = """
cd /workspace/frappe-bench
bench setup supervisor --skip-redis --skip-supervisord --yes --user frappe
echo "Supervisor configuration regenerated"
"""
        result = bench.compose_project.compose.run(
            service="frappe",
            command=f"-c {shlex.quote(setup_script)}",
            rm=True,
            user="frappe",
            entrypoint="bash",
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

    def _auto_detect_runtime_versions(self, bench: MigrationBench) -> tuple[str | None, str | None]:
        """
        Auto-detect Python/Node versions using multi-source strategy.
        
        Priority:
        1. Current container runtime (what's actually installed)
        2. Frappe pyproject.toml/package.json requirements
        3. Validate compatibility and choose best version
        
        Returns:
            (python_version, node_version) - versions to use for rebuild
        """
        from frappe_manager.site_manager.bench_config import (
            extract_python_version_requirement,
            extract_node_version_requirement,
        )
        
        current_python = None
        current_node = None
        
        try:
            result = bench.compose_project.compose.run(
                service="frappe",
                command="-c '/workspace/frappe-bench/env/bin/python --version 2>&1'",
                rm=True,
                user="frappe",
                entrypoint="bash",
                stream=False,
            )
            if isinstance(result, SubprocessOutput) and result.exit_code == 0:
                import re
                output = " ".join(result.combined)
                match = re.search(r"Python (\d+)\.(\d+)\.(\d+)", output)
                if match:
                    major, minor = match.group(1), match.group(2)
                    current_python = f"{major}.{minor}"
                    richprint.print(f"    • Detected current Python: {current_python}")
        except Exception as e:
            self.logger.debug(f"Could not detect current Python (expected during migration): {e}")
        
        try:
            result = bench.compose_project.compose.run(
                service="frappe",
                command="node --version",
                rm=True,
                user="frappe",
                entrypoint="bash",
                stream=False,
            )
            if isinstance(result, SubprocessOutput) and result.exit_code == 0:
                import re
                output = " ".join(result.combined)
                match = re.search(r"v(\d+)\.\d+\.\d+", output)
                if match:
                    current_node = match.group(1)
                    richprint.print(f"    • Detected current Node: {current_node}")
        except Exception as e:
            self.logger.debug(f"Could not detect current Node (expected during migration): {e}")
        
        frappe_app_path = bench.path / "workspace" / "frappe-bench" / "apps" / "frappe"
        
        frappe_python_req = None
        frappe_node_req = None
        
        if frappe_app_path.exists():
            frappe_python_req = extract_python_version_requirement(frappe_app_path)
            frappe_node_req = extract_node_version_requirement(frappe_app_path)
            
            if frappe_python_req:
                richprint.print(f"    • Frappe requires Python: {frappe_python_req}")
            if frappe_node_req:
                richprint.print(f"    • Frappe requires Node: {frappe_node_req}")
        
        final_python = self._choose_best_python_version(current_python, frappe_python_req)
        final_node = self._choose_best_node_version(current_node, frappe_node_req)
        
        return final_python, final_node

    def _choose_best_python_version(
        self, 
        current: str | None, 
        frappe_requirement: str | None
    ) -> str | None:
        """
        Choose best Python version based on current and Frappe requirements.
        
        Strategy:
        1. If Frappe requires specific version → check if current satisfies
        2. If current version satisfies Frappe requirement → keep current
        3. If current version too old → upgrade to Frappe minimum
        4. If no Frappe requirement → keep current (if valid) or default to 3.11
        """
        from frappe_manager.site_manager.bench_config import parse_python_version_for_runtime
        import re
        
        frappe_min_version = None
        if frappe_requirement:
            frappe_min_version = parse_python_version_for_runtime(frappe_requirement)
            if frappe_min_version:
                richprint.print(f"    • Frappe minimum Python: {frappe_min_version}")
        
        if current:
            current_tuple = tuple(map(int, current.split('.')))
            
            if frappe_requirement and frappe_min_version:
                match_min = re.search(r">=(\d+)\.(\d+)", frappe_requirement)
                match_max = re.search(r"<(\d+)\.(\d+)", frappe_requirement)
                
                if match_min:
                    min_ver = (int(match_min.group(1)), int(match_min.group(2)))
                    
                    if match_max:
                        max_ver = (int(match_max.group(1)), int(match_max.group(2)))
                        
                        if min_ver <= current_tuple < max_ver:
                            richprint.print(f"    ✓ Current Python {current} satisfies Frappe requirement")
                            return current
                        else:
                            richprint.print(f"    ✗ Current Python {current} doesn't satisfy requirement, upgrading to {frappe_min_version}")
                            return frappe_min_version
                    else:
                        if current_tuple >= min_ver:
                            richprint.print(f"    ✓ Current Python {current} satisfies Frappe requirement")
                            return current
                        else:
                            richprint.print(f"    ✗ Current Python {current} doesn't satisfy requirement, upgrading to {frappe_min_version}")
                            return frappe_min_version
            
            if current_tuple >= (3, 10):
                richprint.print(f"    ✓ Keeping current Python {current}")
                return current
        
        if frappe_min_version:
            richprint.print(f"    → Using Frappe minimum Python: {frappe_min_version}")
            return frappe_min_version
        
        richprint.print(f"    → Using safe default Python: 3.11")
        return "3.11"

    def _choose_best_node_version(
        self, 
        current: str | None, 
        frappe_requirement: str | None
    ) -> str | None:
        """
        Choose best Node version based on current and Frappe requirements.
        
        Strategy similar to Python version selection.
        """
        from frappe_manager.site_manager.bench_config import parse_node_version_for_runtime
        import re
        
        frappe_min_version = None
        if frappe_requirement:
            frappe_min_version = parse_node_version_for_runtime(frappe_requirement)
            if frappe_min_version:
                richprint.print(f"    • Frappe minimum Node: {frappe_min_version}")
        
        if current:
            current_major = int(current)
            
            if frappe_min_version:
                frappe_major = int(frappe_min_version)
                
                if current_major >= frappe_major:
                    richprint.print(f"    ✓ Current Node {current} satisfies Frappe requirement")
                    return current
                else:
                    richprint.print(f"    ✗ Current Node {current} doesn't satisfy requirement, upgrading to {frappe_min_version}")
                    return frappe_min_version
            
            if current_major >= 18:
                richprint.print(f"    ✓ Keeping current Node {current}")
                return current
        
        if frappe_min_version:
            richprint.print(f"    → Using Frappe minimum Node: {frappe_min_version}")
            return frappe_min_version
        
        richprint.print(f"    → Using safe default Node: 18")
        return "18"
