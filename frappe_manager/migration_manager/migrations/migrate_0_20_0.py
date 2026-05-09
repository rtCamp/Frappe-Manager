"""
Migration for v0.20.0 — Supervisor and system-level log rotation.

CHANGES:
- supervisor.conf: adds stdout_logfile_maxbytes / stdout_logfile_backups /
  stderr_logfile_maxbytes / stderr_logfile_backups (10 MB, 5 backups) to
  every [program:…] section for web, schedule, workers, node-socketio.
- Generates ~/frappe/logrotate.conf covering bench app logs, per-site nginx
  logs, global nginx-proxy logs, global MariaDB logs, and the FM CLI log.
"""

import configparser
import io
import json
import multiprocessing
import os
import platform
from pathlib import Path

from jinja2 import Template

from frappe_manager import CLI_DIR
from frappe_manager.migration_manager.migration_base import MigrationBase
from frappe_manager.migration_manager.migration_helpers import MigrationBench
from frappe_manager.migration_manager.version import Version
from frappe_manager.output_manager.context_managers import spinner
from frappe_manager.utils.helpers import get_template_path


class MigrationV0200(MigrationBase):
    version = Version("0.20.0")

    # ── bench_basic_backup ────────────────────────────────────────────────────

    def bench_basic_backup(self, bench: MigrationBench):
        """Back up existing supervisor config files before regenerating them."""
        super().bench_basic_backup(bench)

        if self.migration_executor.skip_backup or bench.name in self.migration_executor.skip_backup_for:
            return

        config_dir = bench.path / "workspace" / "frappe-bench" / "config"
        if not config_dir.exists():
            return

        supervisor_conf = config_dir / "supervisor.conf"
        if supervisor_conf.exists():
            self.backup_manager.backup(supervisor_conf, bench_name=bench.name)
            self.output.print(f"Backed up {supervisor_conf.name}")

        for conf_file in config_dir.glob("*.fm.supervisor.conf"):
            self.backup_manager.backup(conf_file, bench_name=bench.name)
            self.output.print(f"Backed up {conf_file.name}")

    # ── migrate_bench ─────────────────────────────────────────────────────────

    def migrate_bench(self, bench: MigrationBench):
        """Regenerate per-service supervisor config files with log-rotation settings."""
        with spinner(self.output, f"Updating supervisor log rotation for {bench.name}"):  # type: ignore[arg-type]
            self._regenerate_supervisor_configs(bench)

        self.output.print(f"Successfully migrated {bench.name} to {self.version.version_string()}")

    def undo_bench_migrate(self, bench: MigrationBench):
        """Nothing extra to undo; base class restores backed-up files."""
        pass

    # ── migrate_services (system-level) ──────────────────────────────────────

    def migrate_services(self):
        """Write ~/frappe/logrotate.conf from the logrotate template."""
        with spinner(self.output, "Generating logrotate configuration"):  # type: ignore[arg-type]
            self._write_logrotate_conf()

    def undo_services_migrate(self):
        """No services rollback needed for this migration."""
        self.output.print(f"No services rollback needed for {self.version.version_string()}")

    # ── private helpers ───────────────────────────────────────────────────────

    def _regenerate_supervisor_configs(self, bench: MigrationBench):
        """
        Re-render supervisor.conf.tmpl for the bench and write individual
        *.fm.supervisor.conf files so supervisord picks up log-rotation limits.

        Mirrors the logic in MigrationV0190._regenerate_supervisor_config but
        does not rebuild the Python/Node environment — only supervisor config.
        """
        frappe_bench_dir = bench.path / "workspace" / "frappe-bench"
        common_site_config_path = frappe_bench_dir / "sites" / "common_site_config.json"

        site_config: dict = {}
        if common_site_config_path.exists():
            try:
                site_config = json.loads(common_site_config_path.read_text())
            except Exception:
                self.logger.warning(
                    f"[MigrationV0200] Could not parse common_site_config.json for {bench.name}"
                )

        cpu_count = multiprocessing.cpu_count()
        gunicorn_workers = site_config.get("gunicorn_workers", (cpu_count * 2) + 1)
        max_requests = site_config.get("gunicorn_max_requests", 1000)

        # Detect fnm node binary path (fallback to static path used by FM containers)
        node_binary = "/workspace/frappe-bench/.fnm/aliases/default/bin/node"

        context = {
            "bench_dir": "/workspace/frappe-bench",
            "sites_dir": "/workspace/frappe-bench/sites",
            "user": "frappe",
            "http_timeout": site_config.get("http_timeout", 120),
            "node": node_binary,
            "webserver_port": site_config.get("webserver_port", 80),
            "gunicorn_workers": gunicorn_workers,
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

        # Parse rendered INI and split into individual service files
        config = configparser.ConfigParser(allow_no_value=True, strict=False, interpolation=None)
        config.read_string(rendered)

        config_dir = frappe_bench_dir / "config"
        config_dir.mkdir(parents=True, exist_ok=True)

        written: list[str] = []
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
            written.append(file_name)

        self.logger.info(
            f"[MigrationV0200] Wrote supervisor configs for {bench.name}: {written}"
        )

    def _write_logrotate_conf(self):
        """
        Render logrotate.tmpl and write the result to ~/frappe/logrotate.conf.

        On Linux also prints instructions for installing to /etc/logrotate.d/.
        """
        dest = CLI_DIR / "logrotate.conf"
        template_path = get_template_path("logrotate.tmpl")
        rendered = Template(template_path.read_text()).render(
            frappe_dir=str(CLI_DIR),
            user=self._current_username(),
            output_path=str(dest),
        )

        CLI_DIR.mkdir(parents=True, exist_ok=True)
        dest.write_text(rendered)

        self.output.print(f"Logrotate config written to [blue]{dest}[/blue]")
        self.logger.info(f"[MigrationV0200] logrotate.conf written to {dest}")

        if platform.system() == "Linux":
            self.output.print(
                "Run [bold]fm self logrotate --install[/bold] to install it "
                "system-wide at /etc/logrotate.d/frappe-manager"
            )

    @staticmethod
    def _current_username() -> str:
        try:
            import pwd
            return pwd.getpwuid(os.getuid()).pw_name
        except Exception:
            return os.environ.get("USER", "root")
