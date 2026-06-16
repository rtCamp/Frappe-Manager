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

import json
import shlex
from pathlib import Path
from typing import Any, cast

import tomlkit
from ruamel.yaml import YAML

from frappe_manager.docker.subprocess_output import SubprocessOutput
from frappe_manager.migration_manager.migration_base import MigrationBase
from frappe_manager.migration_manager.migration_helpers import MigrationBench
from frappe_manager.migration_manager.version import Version
from frappe_manager.output_manager.context_managers import spinner


class MigrationV0190(MigrationBase):
    version = Version("0.19.0")

    def bench_basic_backup(self, bench: MigrationBench):
        """
        Override parent to add additional backups for runtime rebuild.

        Backs up:
        - supervisor.conf and *.fm.supervisor.conf (regenerated during rebuild)
        - env/ directory (Python venv, will be recreated)
        """
        super().bench_basic_backup(bench)

        if self.migration_executor.skip_backup or bench.name in self.migration_executor.skip_backup_for:
            return

        supervisor_config_dir = bench.path / "workspace" / "frappe-bench" / "config"
        if supervisor_config_dir.exists():
            supervisor_conf = supervisor_config_dir / "supervisor.conf"
            if supervisor_conf.exists():
                self.backup_manager.backup(supervisor_conf, bench_name=bench.name)
                self.output.print("Backed up supervisor.conf")

            for conf_file in supervisor_config_dir.glob("*.fm.supervisor.conf"):
                self.backup_manager.backup(conf_file, bench_name=bench.name)
                self.output.print(f"Backed up {conf_file.name}")

        env_dir = bench.path / "workspace" / "frappe-bench" / "env"
        if env_dir.exists() and env_dir.is_dir():
            import shutil

            env_backup_path = bench.path / "workspace" / "frappe-bench" / "env.backup.migration"
            if env_backup_path.exists():
                shutil.rmtree(env_backup_path)
            shutil.move(str(env_dir), str(env_backup_path))
            self.output.print("Moved env/ to env.backup.migration")

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
                self.output.print("Removing new env/")
                shutil.rmtree(env_dir)

            self.output.print("Restoring env/ from env.backup.migration")
            shutil.move(str(env_backup_path), str(env_dir))

        # Restore .bashrc if it was backed up
        bashrc_backup = bench.path / "workspace" / "frappe-bench" / ".bashrc.migration.bak"
        bashrc_path = bench.path / "workspace" / "frappe-bench" / ".bashrc"
        if bashrc_backup.exists():
            if bashrc_path.exists():
                bashrc_path.unlink()
            shutil.move(str(bashrc_backup), str(bashrc_path))
            self.output.print("Restored .bashrc from backup")

        # Restore nginx default.conf if it was backed up
        nginx_default_conf = bench.path / "configs" / "nginx" / "conf" / "conf.d" / "default.conf"
        nginx_default_backup = bench.path / "configs" / "nginx" / "conf" / "conf.d" / "default.conf.migration.bak"
        if nginx_default_backup.exists():
            if nginx_default_conf.exists():
                nginx_default_conf.unlink()
            shutil.move(str(nginx_default_backup), str(nginx_default_conf))
            self.output.print("Restored nginx default.conf from backup")

    def migrate_bench(self, bench: MigrationBench):
        """Migrate bench from v0.18.0 to current version."""
        with spinner(self.output, f"Migrating bench configuration for {bench.name}"):  # type: ignore[arg-type]
            bench_config_path = bench.path / "bench_config.toml"
            if bench_config_path.exists():
                self._migrate_bench_config_toml(bench, bench_config_path)

            docker_compose_path = bench.path / "docker-compose.yml"
            if docker_compose_path.exists():
                self._migrate_docker_compose_yml(bench, docker_compose_path)
                self._pull_bench_images(bench)

            workers_compose_path = bench.path / "docker-compose.workers.yml"
            if workers_compose_path.exists():
                self._migrate_workers_compose_yml(bench, workers_compose_path)

            self._cleanup_admin_tools_nginx_config(bench)

            # Apply upload limit configuration across all locations
            # Resolve upload limit: prefer existing site_config.json max_file_size, then bench_config, then default
            upload_limit = self._resolve_upload_limit(bench)
            self._write_upload_limit_vhostd(bench, upload_limit)
            self._write_upload_limit_site_config(bench, upload_limit)
            self._write_upload_limit_nginx_conf(bench, upload_limit)

        self._rebuild_runtime_environment(bench)

        self.output.print(f"Successfully migrated {bench.name} to {self.version.version_string()}")

    def _migrate_bench_config_toml(self, bench: MigrationBench, config_path: Path):
        """Transform bench_config.toml: [ssl] → [[ssl_certificates]], add new fields."""
        self.output.print("Migrating bench_config.toml")

        content = config_path.read_text()
        doc = tomlkit.parse(content)

        self._transform_ssl_config(doc, bench.name)
        self._add_new_config_fields(doc)

        config_path.write_text(tomlkit.dumps(doc))
        self.output.print("Updated SSL configuration format")

    def _transform_ssl_config(self, doc: tomlkit.TOMLDocument, bench_name: str):
        """Transform [ssl] table to [[ssl_certificates]] array with proper field names."""
        if "ssl" not in doc:
            return

        old_ssl = doc["ssl"]

        if isinstance(old_ssl, list):
            return

        old_ssl_dict = cast("dict[str, Any]", old_ssl)

        ssl_type_value = old_ssl_dict.get("ssl_type", "letsencrypt")
        hsts_value = old_ssl_dict.get("hsts", "off")

        challenge_type_value = old_ssl_dict.get("preferred_challenge") or old_ssl_dict.get("challenge_type") or "http01"

        ssl_cert: dict[str, Any] = {
            "domain": str(bench_name),
            "ssl_type": ssl_type_value,
            "acme_client": "acme.sh",
            "hsts": hsts_value,
            "challenge_type": challenge_type_value,
        }

        self._move_dns_credentials(doc, old_ssl_dict)

        del doc["ssl"]
        doc["ssl_certificates"] = [ssl_cert]

    def _move_dns_credentials(self, doc: tomlkit.TOMLDocument, old_ssl_dict: dict[str, Any]):
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

        self.output.print("Migrated DNS credentials to dns_providers.cloudflare")

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
        """Update compose: v0.18.0 → current version images, SITENAME → SITE_MAPPINGS."""
        self.output.print("Migrating docker-compose.yml")

        yaml = YAML(typ="rt")
        yaml.preserve_quotes = True
        yaml.default_flow_style = False

        with open(compose_path) as f:
            compose_data = yaml.load(f)

        if not compose_data or "services" not in compose_data:
            return

        services = compose_data["services"]

        self._update_service_images(services)

        # Read config values from bench_config.toml
        bench_config_path = bench.path / "bench_config.toml"
        upload_limit = "50M"
        restart_policy = "unless-stopped"
        if bench_config_path.exists():
            config = tomlkit.parse(bench_config_path.read_text())
            upload_limit = config.get("upload_limit", "50M")
            restart_policy = config.get("restart_policy", "unless-stopped")

        self._transform_nginx_environment(services, upload_limit)
        self._add_restart_policy_to_services(services, restart_policy)

        # Update x-version to current version
        compose_data["x-version"] = self.version.version_string()

        with open(compose_path, "w") as f:
            yaml.dump(compose_data, f)

    def _update_service_images(self, services: dict[str, Any]):
        """Replace any existing version tag with runtime-determined version."""
        import re

        effective_tag = self._get_image_tag_for_migration()
        
        version_pattern = re.compile(r"(ghcr\.io/rtcamp/frappe-manager-[^:]+):v[0-9]+\.[0-9]+\.[0-9]+(?:\.dev[0-9]+)?")

        for service_name, service_config in services.items():
            if "image" not in service_config:
                continue

            old_image = service_config["image"]

            old_version_match = re.search(r":v([0-9]+\.[0-9]+\.[0-9]+(?:\.dev[0-9]+)?)", old_image)
            old_version = old_version_match.group(1) if old_version_match else "unknown"

            new_image = version_pattern.sub(rf"\1:{effective_tag}", old_image)

            if new_image != old_image:
                service_config["image"] = new_image
                self.output.print(f"Updated {service_name} image: v{old_version} → {effective_tag}")

    def _transform_nginx_environment(self, services: dict[str, Any], upload_limit: str):
        """Transform nginx SITENAME → SITE_MAPPINGS environment variable."""
        if "nginx" not in services or "environment" not in services["nginx"]:
            return

        nginx_env = services["nginx"]["environment"]

        if isinstance(nginx_env, dict):
            self._transform_nginx_env_dict(nginx_env, upload_limit)
        elif isinstance(nginx_env, list):
            services["nginx"]["environment"] = self._transform_nginx_env_list(nginx_env, upload_limit)

    def _transform_nginx_env_dict(self, nginx_env: dict[str, Any], upload_limit: str):
        """Transform dict format: {SITENAME: value} → {SITE_MAPPINGS: '{"value": "value}'}."""
        if "SITENAME" in nginx_env:
            sitename_value = nginx_env.pop("SITENAME")
            # Convert plain site name to JSON mapping expected by nginx entrypoint
            site_mapping = json.dumps({sitename_value: sitename_value})
            nginx_env["SITE_MAPPINGS"] = site_mapping
            self.output.print(f"Migrated SITENAME → SITE_MAPPINGS ({site_mapping})")

        if "HTTPS_METHOD" not in nginx_env:
            nginx_env["HTTPS_METHOD"] = "noredirect"
        if "CLIENT_MAX_BODY_SIZE" not in nginx_env:
            nginx_env["CLIENT_MAX_BODY_SIZE"] = upload_limit.lower()

    def _transform_nginx_env_list(self, nginx_env: list, upload_limit: str) -> list:
        """Transform list format: [SITENAME=value] → [SITE_MAPPINGS='{"value": "value}']."""
        new_env = []
        for env_var in nginx_env:
            if isinstance(env_var, str) and env_var.startswith("SITENAME="):
                sitename_value = env_var.split("=", 1)[1]
                # Convert plain site name to JSON mapping expected by nginx entrypoint
                site_mapping = json.dumps({sitename_value: sitename_value})
                new_env.append(f"SITE_MAPPINGS={site_mapping}")
                self.output.print(f"Migrated SITENAME → SITE_MAPPINGS ({site_mapping})")
            else:
                new_env.append(env_var)

        # Add HTTPS_METHOD and CLIENT_MAX_BODY_SIZE if not already present
        existing_keys = {env.split("=", 1)[0] for env in new_env if isinstance(env, str) and "=" in env}
        if "HTTPS_METHOD" not in existing_keys:
            new_env.append("HTTPS_METHOD=noredirect")
        if "CLIENT_MAX_BODY_SIZE" not in existing_keys:
            new_env.append(f"CLIENT_MAX_BODY_SIZE={upload_limit.lower()}")

        return new_env

    def _add_restart_policy_to_services(self, services: dict[str, Any], restart_policy: str):
        """Add restart policy to all services in compose file."""
        for service_name, service_config in services.items():
            if "restart" not in service_config:
                service_config["restart"] = restart_policy

    def _resolve_upload_limit(self, bench: MigrationBench) -> str:
        """Resolve upload limit, respecting existing site_config.json max_file_size.

        Priority:
        1. Existing max_file_size in site_config.json (converted back to string like "50M")
        2. upload_limit from bench_config.toml
        3. Default "50M"
        """
        # 1. Check site_config.json for existing max_file_size
        site_config_path = bench.path / "workspace" / "frappe-bench" / "sites" / "common_site_config.json"
        if site_config_path.exists():
            try:
                site_config = json.loads(site_config_path.read_text())
                max_file_size = site_config.get("max_file_size")
                if max_file_size:
                    # Convert bytes back to human-readable string
                    if max_file_size >= 1024 * 1024 * 1024 and max_file_size % (1024 * 1024 * 1024) == 0:
                        resolved = f"{max_file_size // (1024 * 1024 * 1024)}G"
                    elif max_file_size >= 1024 * 1024 and max_file_size % (1024 * 1024) == 0:
                        resolved = f"{max_file_size // (1024 * 1024)}M"
                    else:
                        # Round to nearest MB
                        resolved = f"{round(max_file_size / (1024 * 1024))}M"
                    self.output.print(f"Using existing site_config.json max_file_size: {resolved} ({max_file_size} bytes)")
                    return resolved
            except Exception:
                pass

        # 2. Fall back to bench_config.toml
        bench_config_path = bench.path / "bench_config.toml"
        if bench_config_path.exists():
            config = tomlkit.parse(bench_config_path.read_text())
            upload_limit = config.get("upload_limit")
            if upload_limit:
                self.output.print(f"Using bench_config.toml upload_limit: {upload_limit}")
                return upload_limit

        # 3. Default
        self.output.print("Using default upload_limit: 50M")
        return "50M"

    def _write_upload_limit_vhostd(self, bench: MigrationBench, upload_limit: str):
        """Write nginx-proxy vhost.d files for upload limit."""
        from frappe_manager.site_manager.modules.upload_limit_manager import UploadLimitManager

        # Global nginx-proxy vhostd directory
        vhostd_dir = bench.path.parent.parent / "services" / "nginx-proxy" / "vhostd"

        if not vhostd_dir.exists():
            self.output.print("Warning: nginx-proxy vhostd directory not found, skipping upload limit config")
            return

        upload_mgr = UploadLimitManager(vhostd_dir)
        domains = [bench.name]

        # Also include alias_domains if available
        bench_config_path = bench.path / "bench_config.toml"
        if bench_config_path.exists():
            config = tomlkit.parse(bench_config_path.read_text())
            alias_domains = config.get("alias_domains", [])
            if alias_domains:
                domains.extend(alias_domains)

        upload_mgr.set_upload_limit_for_domains(domains, upload_limit.lower())
        self.output.print(f"Set upload limit ({upload_limit}) for {len(domains)} domain(s)")

    def _write_upload_limit_site_config(self, bench: MigrationBench, upload_limit: str):
        """Update site_config.json max_file_size to match upload_limit (only if not already set)."""
        import re

        site_config_path = bench.path / "workspace" / "frappe-bench" / "sites" / "common_site_config.json"
        if not site_config_path.exists():
            return

        try:
            site_config = json.loads(site_config_path.read_text())
            # Respect previously configured max_file_size — only set if missing
            if "max_file_size" in site_config:
                self.output.print(f"site_config.json max_file_size already set ({site_config['max_file_size']}), skipping")
                return

            # Parse size to bytes (same logic as site.py _parse_size_to_bytes)
            match = re.match(r"^(\d+)([MG])$", upload_limit, re.IGNORECASE)
            if not match:
                return

            value = int(match.group(1))
            unit = match.group(2).upper()
            size_bytes = value * (1024 * 1024) if unit == "M" else value * (1024 * 1024 * 1024)

            site_config["max_file_size"] = size_bytes
            site_config_path.write_text(json.dumps(site_config, indent=4))
            self.output.print(f"Updated site_config.json (max_file_size: {size_bytes} bytes)")
        except Exception:
            self.output.print("Warning: Could not update site_config.json max_file_size")

    def _write_upload_limit_nginx_conf(self, bench: MigrationBench, upload_limit: str):
        """Create custom nginx config file for bench-level upload limit."""
        custom_conf_dir = bench.path / "configs" / "nginx" / "conf" / "custom"
        custom_conf_dir.mkdir(parents=True, exist_ok=True)

        upload_limit_conf = custom_conf_dir / "upload-limit.conf"
        upload_limit_conf.write_text(f"client_max_body_size {upload_limit.lower()};\n")
        self.output.print("Created custom nginx upload-limit.conf")

    def _migrate_workers_compose_yml(self, bench: MigrationBench, compose_path: Path):
        """Update worker compose: v0.18.0 → current version images."""
        self.output.print("Migrating docker-compose.workers.yml")

        yaml = YAML(typ="rt")
        yaml.preserve_quotes = True
        yaml.default_flow_style = False

        with open(compose_path) as f:
            compose_data = yaml.load(f)

        if not compose_data or "services" not in compose_data:
            return

        services = compose_data["services"]

        self._update_service_images(services)

        # Add restart policy from bench_config
        bench_config_path = bench.path / "bench_config.toml"
        restart_policy = "unless-stopped"
        if bench_config_path.exists():
            config = tomlkit.parse(bench_config_path.read_text())
            restart_policy = config.get("restart_policy", "unless-stopped")
        self._add_restart_policy_to_services(services, restart_policy)

        # Update x-version to current version
        compose_data["x-version"] = self.version.version_string()

        with open(compose_path, "w") as f:
            yaml.dump(compose_data, f)

    def _cleanup_admin_tools_nginx_config(self, bench: MigrationBench):
        admin_tools_config = bench.path / "configs" / "nginx" / "conf" / "custom" / "admin-tools.conf"
        htpasswd_file = bench.path / "configs" / "nginx" / "conf" / "http_auth" / f"{bench.name}-admin-tools.htpasswd"

        if admin_tools_config.exists():
            admin_tools_config.unlink()
            self.output.print("Cleaned up stale admin-tools nginx config")

        if htpasswd_file.exists():
            htpasswd_file.unlink()

    def _pull_bench_images(self, bench: MigrationBench):
        from frappe_manager.migration_manager.migration_exections import MigrationExceptionInBench
        
        self.output.print(f"Pulling updated images ({self.effective_image_tag})...", emoji_code="📦")
        
        result = bench.compose.pull(stream=False)
        
        if result.exit_code != 0:
            raise MigrationExceptionInBench(
                f"Failed to pull images for {bench.name}. Docker pull failed."
            )
        
        self.output.print("✓ Images ready", emoji_code="✅")

    def migrate_services(self):
        """
        Pull current version Docker images (system-level infrastructure).

        Images are shared resources across all benches - must be pulled
        at system level before any bench can use them.
        """
        from frappe_manager.utils.site import pull_docker_images

        self.logger.info(f"[migrate_services] Starting Docker image pull for {self.version.version_string()}")

        with spinner(self.output, f"Pulling Docker images for {self.version.version_string()}"):  # type: ignore[arg-type]
            success = pull_docker_images()

            if not success:
                error_msg = "Failed to pull one or more Docker images"
                self.logger.error(f"[migrate_services] {error_msg}")
                self.output.display_error(error_msg)
                raise Exception(error_msg)

        self.output.print(f"All {self.version.version_string()} images pulled successfully")
        self.logger.info("[migrate_services] Docker image pull completed successfully")

    def undo_services_migrate(self):
        """No global services rollback needed."""
        self.output.print(f"No services rollback needed for {self.version.version_string()}")

    def _rebuild_runtime_environment(self, bench: MigrationBench):
        """Rebuild Python/Node environment using uv/fnm (current version runtime system)."""
        self.logger.info(f"[_rebuild_runtime_environment] Starting runtime rebuild for {bench.name}")

        bench_config_path = bench.path / "bench_config.toml"

        python_version = None
        node_version = None

        config_doc = None
        if bench_config_path.exists():
            from frappe_manager.site_manager.bench_config import (
                parse_node_version_for_runtime,
                parse_python_version_for_runtime,
            )

            config_doc = tomlkit.parse(bench_config_path.read_text())
            raw_python_version = config_doc.get("python_version")
            raw_node_version = config_doc.get("node_version")

            python_version = parse_python_version_for_runtime(raw_python_version) if raw_python_version else None
            node_version = parse_node_version_for_runtime(raw_node_version) if raw_node_version else None

            self.logger.debug(
                f"[_rebuild_runtime_environment] From config (parsed): Python={python_version}, Node={node_version}",
            )

        if not python_version or not node_version:
            self.output.print(
                "No Python/Node versions in config, auto-detecting from container and Frappe requirements...",
            )
            self.logger.info("[_rebuild_runtime_environment] Auto-detecting versions...")
            python_version, node_version = self._auto_detect_runtime_versions(bench)
            self.logger.info(
                f"[_rebuild_runtime_environment] Auto-detected: Python={python_version}, Node={node_version}",
            )

            if config_doc and (python_version or node_version):
                if python_version:
                    config_doc["python_version"] = python_version
                if node_version:
                    config_doc["node_version"] = node_version
                bench_config_path.write_text(tomlkit.dumps(config_doc))
                self.output.print("Updated bench_config.toml with detected versions")

        with spinner(self.output, "Rebuilding runtime environment (pyenv/nvm → uv/fnm)"):  # type: ignore[arg-type]
            self._ensure_runtime_dirs(bench)

            self.output.print("Cleaning up old runtime directories...")
            self._cleanup_old_runtime_dirs(bench)

            if python_version:
                self.output.print(f"Setting up Python {python_version} with uv...")
                self._setup_python_with_uv(bench, python_version)

            if node_version:
                self.output.print(f"Setting up Node {node_version} with fnm...")
                self._setup_node_with_fnm(bench, node_version)

            self.output.print("Reinstalling apps and rebuilding assets...")
            self._reinstall_apps_and_rebuild(bench)

            self.output.print("Regenerating supervisor configuration...")
            self._regenerate_supervisor_config(bench)

            if bench.running or bench.workers_running:
                self.output.print("Recreating & restarting services (force-recreate)...")
                self._restart_services(bench)

        self.output.print("Runtime environment rebuilt successfully")
        self.logger.info(f"[_rebuild_runtime_environment] Completed successfully for {bench.name}")

    def _setup_python_with_uv(self, bench: MigrationBench, python_version: str):
        """Setup Python using uv python manager."""
        self.logger.debug(f"[_setup_python_with_uv] Starting Python {python_version} setup for {bench.name}")

        quoted_pkg = shlex.quote(f"cpython-{python_version}")

        setup_script = f"""
cd /workspace/frappe-bench
if [ -d env ]; then
    echo "Backing up old venv..."
    rm -rf env.bak 2>/dev/null || true
    mv env env.bak
fi

echo "Installing Python {python_version} via uv..."
export UV_PYTHON_INSTALL_DIR=/workspace/frappe-bench/.uv/python
uv python install {quoted_pkg}

echo "Detecting installed Python..."
PYTHON_DIR=$(ls -1d /workspace/frappe-bench/.uv/python/{quoted_pkg}* 2>/dev/null | sort -V | tail -1 || echo "")
if [ -z "$PYTHON_DIR" ]; then
    echo "Error: Could not find installed Python"
    exit 1
fi
PYTHON_BASENAME=$(basename "$PYTHON_DIR")

echo "Updating python-default symlink..."
cd /workspace/frappe-bench/.uv
rm -f python-default
ln -sf "python/$PYTHON_BASENAME" python-default

echo "Creating new venv with $PYTHON_BASENAME..."
cd /workspace/frappe-bench
uv venv env --python "$PYTHON_BASENAME" --seed --link-mode=copy

echo "Python environment setup complete"
echo "Verifying env directory..."
ls -la env/ || echo "ERROR: env directory not found!"
"""
        self.logger.debug("[_setup_python_with_uv] Executing docker compose run...")

        try:
            result = bench.compose.run(
                service="frappe",
                command=f"bash -c {shlex.quote(setup_script)}",
                rm=True,
                entrypoint="/exec-entrypoint.sh",
            )
        except Exception as e:
            error_str = str(e)
            if "network" in error_str.lower() and (
                "not found" in error_str.lower() or "could not be found" in error_str.lower()
            ):
                self.logger.error(f"[_setup_python_with_uv] Docker network error: {e}")
                raise Exception(
                    "Docker network not found. Global services may not be running. Try: fm services start",
                ) from e
            raise

        if not isinstance(result, SubprocessOutput):
            self.logger.error("[_setup_python_with_uv] Unexpected streaming output received")
            raise Exception("Unexpected streaming output received")

        self.logger.debug(f"[_setup_python_with_uv] Exit code: {result.exit_code}")
        self.logger.debug(f"[_setup_python_with_uv] Output: {result.combined}")

        if result.exit_code != 0:
            self.logger.error(f"[_setup_python_with_uv] Python setup failed with exit code {result.exit_code}")
            self.logger.error(f"[_setup_python_with_uv] Output: {result.combined}")
            raise Exception(f"Python setup failed with exit code {result.exit_code}")

        self.logger.debug("[_setup_python_with_uv] Python setup completed successfully")

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

echo "Verifying yarn is available (auto-enabled by FNM_COREPACK_ENABLED)..."
if yarn --version >/dev/null 2>&1; then
    echo "Yarn is available"
else
    echo "WARNING: Yarn not available after Node installation - corepack may have failed"
fi

echo "Node environment setup complete"
"""
        self.logger.debug("[_setup_node_with_fnm] Executing docker compose run...")

        result = bench.compose.run(
            service="frappe",
            command=f"bash -c {shlex.quote(setup_script)}",
            rm=True,
            entrypoint="/exec-entrypoint.sh",
        )

        if not isinstance(result, SubprocessOutput):
            self.logger.error("[_setup_node_with_fnm] Unexpected streaming output received")
            raise Exception("Unexpected streaming output received")

        self.logger.debug(f"[_setup_node_with_fnm] Exit code: {result.exit_code}")
        self.logger.debug(f"[_setup_node_with_fnm] Output: {result.combined}")

        if result.exit_code != 0:
            self.logger.error(f"[_setup_node_with_fnm] Node setup failed with exit code {result.exit_code}")
            self.logger.error(f"[_setup_node_with_fnm] Output: {result.combined}")
            raise Exception(f"Node setup failed with exit code {result.exit_code}")

        self.logger.debug("[_setup_node_with_fnm] Node setup completed successfully")

    def _ensure_runtime_dirs(self, bench: MigrationBench):
        """Ensure runtime directories exist on host.

        Ownership is fixed inside the container by exec-entrypoint.sh which
        runs as root before gosu switches to the frappe user.
        """
        frappe_bench_dir = bench.path / "workspace" / "frappe-bench"
        (frappe_bench_dir / ".uv").mkdir(parents=True, exist_ok=True)
        (frappe_bench_dir / ".fnm").mkdir(parents=True, exist_ok=True)

    def _cleanup_old_runtime_dirs(self, bench: MigrationBench):
        """Remove old pyenv and nvm directories to prevent path conflicts."""
        self.logger.debug(f"[_cleanup_old_runtime_dirs] Cleaning up old runtime directories for {bench.name}")

        cleanup_script = """
echo "Removing old runtime directories..."
mv /workspace/.bashrc /workspace/.bashrc.migration.bak
rm -rf /workspace/.pyenv 2>/dev/null || true
rm -rf /workspace/.nvm 2>/dev/null || true
echo "Old runtime directories cleaned up"
"""
        self.logger.debug("[_cleanup_old_runtime_dirs] Executing docker compose run...")

        result = bench.compose.run(
            service="frappe",
            command=f"bash -c {shlex.quote(cleanup_script)}",
            rm=True,
            entrypoint="/exec-entrypoint.sh",
        )

        if not isinstance(result, SubprocessOutput):
            self.logger.error("[_cleanup_old_runtime_dirs] Unexpected streaming output received")
            raise Exception("Unexpected streaming output received")

        self.logger.debug(f"[_cleanup_old_runtime_dirs] Exit code: {result.exit_code}")

        if result.exit_code != 0:
            self.logger.warning("[_cleanup_old_runtime_dirs] Cleanup had non-zero exit, but continuing...")
        else:
            self.logger.debug("[_cleanup_old_runtime_dirs] Cleanup completed successfully")

    def _reinstall_apps_and_rebuild(self, bench: MigrationBench):
        """Reinstall apps into new venv and rebuild static assets."""
        self.logger.debug(f"[_reinstall_apps_and_rebuild] Starting for {bench.name}")

        apps_txt_path = bench.path / "workspace" / "frappe-bench" / "sites" / "apps.txt"

        if not apps_txt_path.exists():
            self.logger.warning(f"[_reinstall_apps_and_rebuild] No apps.txt found at {apps_txt_path}")
            self.output.warning("No apps.txt found, skipping app reinstallation")
            return

        installed_apps = [line.strip() for line in apps_txt_path.read_text().splitlines() if line.strip()]

        if not installed_apps:
            self.logger.warning("[_reinstall_apps_and_rebuild] No apps in apps.txt")
            self.output.warning("No apps found in apps.txt")
            return

        self.logger.debug(f"[_reinstall_apps_and_rebuild] Found apps: {installed_apps}")

        reinstall_script = """
set -x
cd /workspace/frappe-bench

# Source bashrc to load fnm environment (node/yarn in PATH)
source /etc/bash.bashrc

echo "Reinstalling apps into new venv..."
for app in $(ls -1 apps); do
    if [ -d "apps/$app" ]; then
        echo "Installing $app..."
        uv pip install --python env/bin/python --no-cache-dir -e "apps/$app" || \
        ./env/bin/pip install --no-cache-dir -e "apps/$app"
    fi
done

echo "Installing Node dependencies..."
bench setup requirements --node

echo "Building static assets..."
bench build

echo "Apps reinstalled and assets built successfully"
"""
        self.logger.debug("[_reinstall_apps_and_rebuild] Executing docker compose run...")

        result = bench.compose.run(
            service="frappe",
            command=f"bash -c {shlex.quote(reinstall_script)}",
            rm=True,
            entrypoint="/exec-entrypoint.sh",
        )

        if not isinstance(result, SubprocessOutput):
            self.logger.error("[_reinstall_apps_and_rebuild] Unexpected streaming output received")
            raise Exception("Unexpected streaming output received")

        self.logger.debug(f"[_reinstall_apps_and_rebuild] Exit code: {result.exit_code}")
        output_str = " ".join(result.combined)
        self.logger.debug(f"[_reinstall_apps_and_rebuild] Output length: {len(output_str)} chars")

        if result.exit_code != 0:
            self.logger.error(f"[_reinstall_apps_and_rebuild] Failed with exit code {result.exit_code}")
            self.logger.error(f"[_reinstall_apps_and_rebuild] Full output: {output_str}")
            raise Exception(f"App reinstallation failed with exit code {result.exit_code}")

        self.logger.debug("[_reinstall_apps_and_rebuild] Completed successfully")

    def _regenerate_supervisor_config(self, bench: MigrationBench):
        """Regenerate supervisor configuration using FM's own template (no bench CLI)."""
        import configparser
        import io
        import json
        import multiprocessing

        from jinja2 import Template

        from frappe_manager.utils.helpers import get_template_path

        # Render the supervisor config in memory using FM's own template.
        # This avoids `bench setup supervisor` entirely — the FM template already
        # has correct paths (0.0.0.0, .fnm node binary) so no post-processing needed.
        frappe_bench_dir = bench.path / "workspace" / "frappe-bench"
        common_site_config_path = frappe_bench_dir / "sites" / "common_site_config.json"

        site_config: dict = {}
        if common_site_config_path.exists():
            try:
                site_config = json.loads(common_site_config_path.read_text())
            except Exception:
                pass

        cpu_count = multiprocessing.cpu_count()
        gunicorn_workers = site_config.get("gunicorn_workers", (cpu_count * 2) + 1)
        gunicorn_threads = site_config.get("gunicorn_threads", max(2, min(cpu_count, 4)))
        max_requests = site_config.get("gunicorn_max_requests", 1000)

        context = {
            "bench_dir": "/workspace/frappe-bench",
            "sites_dir": "/workspace/frappe-bench/sites",
            "user": "frappe",
            "use_rq": True,
            "http_timeout": site_config.get("http_timeout", 120),
            "node": "/workspace/frappe-bench/.fnm/aliases/default/bin/node",
            "webserver_port": site_config.get("webserver_port", 80),
            "gunicorn_workers": gunicorn_workers,
            "gunicorn_threads": gunicorn_threads,
            "gunicorn_max_requests": max_requests,
            "gunicorn_max_requests_jitter": int(max_requests * 0.1),
            "bench_name": "frappe-bench",
            "background_workers": site_config.get("background_workers") or 1,
            "bench_cmd": "/opt/user/.bin/bench",
            "workers": site_config.get("workers", {}),
            "multi_queue_consumption": True,
            "supervisor_startretries": 10,
        }

        template_path = get_template_path("supervisor.conf.tmpl")
        rendered = Template(template_path.read_text()).render(**context)

        config = configparser.ConfigParser(allow_no_value=True, strict=False, interpolation=None)
        config.read_string(rendered)

        config_dir = frappe_bench_dir / "config"
        config_dir.mkdir(parents=True, exist_ok=True)

        for section in config.sections():
            if section.startswith("group:"):
                continue

            section_config = configparser.ConfigParser(allow_no_value=True, strict=False, interpolation=None)
            section_config.add_section(section)
            for key, value in config.items(section):
                section_config.set(section, key, value)

            delimiter = "-node-" if "-node-" in section else "-frappe-"
            file_name_prefix = section.split(delimiter)[-1]
            file_name = (
                file_name_prefix + ".workers.fm.supervisor.conf"
                if "worker" in section
                else file_name_prefix + ".fm.supervisor.conf"
            )

            buf = io.StringIO()
            section_config.write(buf)
            (config_dir / file_name).write_text(buf.getvalue())

        # Generate fm-web-server.sh script (required by new supervisor config)
        self._generate_fm_web_server_script(config_dir, context)

        self.logger.debug(f"[_regenerate_supervisor_config] Done for {bench.name}")

    def _generate_fm_web_server_script(self, config_dir: Path, context: dict):
        """Generate fm-web-server.sh script required by the new supervisor config."""
        from jinja2 import Template

        from frappe_manager.utils.helpers import get_template_path

        gunicorn_args = (
            f"--bind 0.0.0.0:{context['webserver_port']}"
            f" --workers {context['gunicorn_workers']}"
            f" --threads {context.get('gunicorn_threads', 1)}"
            f" --max-requests {context['gunicorn_max_requests']}"
            f" --max-requests-jitter {context['gunicorn_max_requests_jitter']}"
            f" -t {context['http_timeout']}"
            f" --graceful-timeout 30"
            f" frappe.app:application --preload"
        )

        template_path = get_template_path("fm-web-server.sh.tmpl")
        script = Template(template_path.read_text()).render(
            bench_dir=context["bench_dir"],
            gunicorn_args=gunicorn_args,
            bench_name=context["bench_name"],
        )

        wrapper_path = config_dir / "fm-web-server.sh"
        wrapper_path.write_text(script)
        wrapper_path.chmod(0o755)
        self.output.print("Generated fm-web-server.sh")

    def _restart_services(self, bench: MigrationBench):
        try:
            # Delete stale nginx default.conf so entrypoint regenerates with new SITE_MAPPINGS
            nginx_default_conf = bench.path / "configs" / "nginx" / "conf" / "conf.d" / "default.conf"
            if nginx_default_conf.exists():
                # Backup before deletion
                backup_path = bench.path / "configs" / "nginx" / "conf" / "conf.d" / "default.conf.migration.bak"
                import shutil
                shutil.copy2(str(nginx_default_conf), str(backup_path))
                nginx_default_conf.unlink()
                self.output.print("Backed up and removed stale nginx default.conf for regeneration")

            bench.compose.up(services=["frappe", "socketio", "schedule", "nginx"], force_recreate=True, detach=True)

            if bench.workers_running:
                bench.workers_docker.compose.up(force_recreate=True, detach=True)

        except Exception as e:
            self.output.warning(f"Service restart (force-recreate) failed: {e}")
            self.output.warning(f"Please restart services manually: fm restart {bench.name}")

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
            extract_node_version_requirement,
            extract_python_version_requirement,
        )

        current_python = None
        current_node = None

        try:
            result = bench.compose.run(
                service="frappe",
                command="bash -c '/workspace/frappe-bench/env/bin/python --version 2>&1'",
                rm=True,
                entrypoint="/exec-entrypoint.sh",
            )
            if isinstance(result, SubprocessOutput) and result.exit_code == 0:
                import re

                output = " ".join(result.combined)
                match = re.search(r"Python (\d+)\.(\d+)\.(\d+)", output)
                if match:
                    major, minor = match.group(1), match.group(2)
                    current_python = f"{major}.{minor}"
                    self.output.print(f"Detected current Python: {current_python}")
        except Exception as e:
            self.logger.debug(f"Could not detect current Python (expected during migration): {e}")

        try:
            result = bench.compose.run(
                service="frappe",
                command="bash -c 'node --version'",
                rm=True,
                entrypoint="/exec-entrypoint.sh",
            )
            if isinstance(result, SubprocessOutput) and result.exit_code == 0:
                import re

                output = " ".join(result.combined)
                match = re.search(r"v(\d+)\.\d+\.\d+", output)
                if match:
                    current_node = match.group(1)
                    self.output.print(f"Detected current Node: {current_node}")
        except Exception as e:
            self.logger.debug(f"Could not detect current Node (expected during migration): {e}")

        frappe_app_path = bench.path / "workspace" / "frappe-bench" / "apps" / "frappe"

        frappe_python_req = None
        frappe_node_req = None

        if frappe_app_path.exists():
            frappe_python_req = extract_python_version_requirement(frappe_app_path)
            frappe_node_req = extract_node_version_requirement(frappe_app_path)

            if frappe_python_req:
                self.output.print(f"Frappe requires Python: {frappe_python_req}")
            if frappe_node_req:
                self.output.print(f"Frappe requires Node: {frappe_node_req}")

        final_python = self._choose_best_python_version(current_python, frappe_python_req)
        final_node = self._choose_best_node_version(current_node, frappe_node_req)

        return final_python, final_node

    def _choose_best_python_version(self, current: str | None, frappe_requirement: str | None) -> str | None:
        """
        Choose best Python version based on current and Frappe requirements.

        Strategy:
        1. If Frappe requires specific version → check if current satisfies
        2. If current version satisfies Frappe requirement → keep current
        3. If current version too old → upgrade to Frappe minimum
        4. If no Frappe requirement → keep current (if valid) or default to 3.11
        """
        import re

        from frappe_manager.site_manager.bench_config import parse_python_version_for_runtime

        frappe_min_version = None
        if frappe_requirement:
            frappe_min_version = parse_python_version_for_runtime(frappe_requirement)
            if frappe_min_version:
                self.output.print(f"Frappe minimum Python: {frappe_min_version}")

        if current:
            current_tuple = tuple(map(int, current.split(".")))

            if frappe_requirement and frappe_min_version:
                match_min = re.search(r">=(\d+)\.(\d+)", frappe_requirement)
                match_max = re.search(r"<(\d+)\.(\d+)", frappe_requirement)

                if match_min:
                    min_ver = (int(match_min.group(1)), int(match_min.group(2)))

                    if match_max:
                        max_ver = (int(match_max.group(1)), int(match_max.group(2)))

                        if min_ver <= current_tuple < max_ver:
                            self.output.print(f"Current Python {current} satisfies Frappe requirement")
                            return current
                        self.output.print(
                            f"Current Python {current} doesn't satisfy requirement, upgrading to {frappe_min_version}",
                        )
                        return frappe_min_version
                    if current_tuple >= min_ver:
                        self.output.print(f"Current Python {current} satisfies Frappe requirement")
                        return current
                    self.output.print(
                        f"Current Python {current} doesn't satisfy requirement, upgrading to {frappe_min_version}",
                    )
                    return frappe_min_version

            if current_tuple >= (3, 10):
                self.output.print(f"Keeping current Python {current}")
                return current

        if frappe_min_version:
            self.output.print(f"Using Frappe minimum Python: {frappe_min_version}")
            return frappe_min_version

        self.output.print("Using safe default Python: 3.11")
        return "3.11"

    def _choose_best_node_version(self, current: str | None, frappe_requirement: str | None) -> str | None:
        """
        Choose best Node version based on current and Frappe requirements.

        Strategy similar to Python version selection.
        """

        from frappe_manager.site_manager.bench_config import parse_node_version_for_runtime

        frappe_min_version = None
        if frappe_requirement:
            frappe_min_version = parse_node_version_for_runtime(frappe_requirement)
            if frappe_min_version:
                self.output.print(f"Frappe minimum Node: {frappe_min_version}")

        if current:
            current_major = int(current)

            if frappe_min_version:
                frappe_major = int(frappe_min_version)

                if current_major >= frappe_major:
                    self.output.print(f"Current Node {current} satisfies Frappe requirement")
                    return current
                self.output.print(
                    f"Current Node {current} doesn't satisfy requirement, upgrading to {frappe_min_version}",
                )
                return frappe_min_version

            if current_major >= 18:
                self.output.print(f"Keeping current Node {current}")
                return current

        if frappe_min_version:
            self.output.print(f"Using Frappe minimum Node: {frappe_min_version}")
            return frappe_min_version

        self.output.print("Using safe default Node: 18")
        return "18"
