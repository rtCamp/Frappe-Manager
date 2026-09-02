from abc import ABC
from pathlib import Path

from frappe_manager import CLI_DIR
from frappe_manager.logger import get_logger
from frappe_manager.migration_manager.backup_manager import BackupManager
from frappe_manager.migration_manager.bench_migration_state import get_bench_migration_version
from frappe_manager.migration_manager.migration_constants import DOCKER_COMPOSE_DOWN_TIMEOUT_SECONDS
from frappe_manager.migration_manager.migration_exceptions import (
    MigrationExceptionInBench,
)
from frappe_manager.migration_manager.migration_helpers import (
    MigrationBench,
    MigrationBenches,
    MigrationServicesManager,
)
from frappe_manager.migration_manager.version import Version
from frappe_manager.output_manager import OutputHandler
from frappe_manager.output_manager.rich_output import RichOutputHandler
from frappe_manager.services_manager.database_service_manager import DatabaseServerServiceInfo, MariaDBManager
from frappe_manager.site_manager.modules import db_tls
from frappe_manager.site_manager.modules.compose_shape import container_transit_path
from frappe_manager.utils.helpers import capture_and_format_exception


class MigrationBase(ABC):
    version: Version = Version("0.0.0")
    benches_dir: Path = CLI_DIR / "sites"
    skip: bool = False
    migration_executor = None

    def __init__(self, output_handler: OutputHandler | None = None):
        self.output = output_handler or RichOutputHandler()
        self.logger = get_logger(component="migration")

        from frappe_manager.utils.helpers import get_current_fm_version, get_docker_image_tag

        self._current_fm_version = get_current_fm_version()
        self.is_dev_environment = self._detect_dev_environment()
        self.effective_image_tag = get_docker_image_tag()

    def _detect_dev_environment(self) -> bool:
        from packaging.version import Version as PV

        parsed = PV(self._current_fm_version)
        return parsed.is_devrelease or parsed.is_prerelease

    def _get_image_tag_for_migration(self) -> str:
        if self.is_dev_environment:
            tag = self.effective_image_tag
            self.logger.info(f"Dev environment detected: using image tag {tag}")
            return tag
        tag = self.version.version_string()
        self.logger.info(f"Stable environment: using image tag {tag}")
        return tag

    def init(self):
        self.backup_manager = BackupManager(name=str(self.version), benches_dir=self.benches_dir)
        self.benches_manager = MigrationBenches(self.benches_dir)
        self.services_manager: MigrationServicesManager = MigrationServicesManager(services_path=CLI_DIR / "services")

    def set_migration_executor(self, migration_executor):
        self.migration_executor = migration_executor
        if hasattr(migration_executor, "output"):
            self.output = migration_executor.output

    def get_rollback_version(self):
        return self.version

    def up(self):
        if self.skip:
            return True

        self.output.print(f"[bold][fm.info]Migration for v{self.version!s}[/fm.info][/bold]", emoji_code=":package:")
        self.logger.info(f"v{self.version!s}: Started")
        self.logger.info("-" * 40)

        self.init()

        if self.migration_executor and self.migration_executor.fm_infrastructure_needs_migration:
            self.services_basic_backup()
            self.migrate_services()

        self.migrate_benches()

        self.logger.info("-" * 40)

    def down(self):
        self.output.change_head(f"Working on v{self.version!s} rollback")
        self.logger.info("-" * 40)

        # undo each bench
        for bench_name, bench_data in self.migration_executor.migrate_benches.items():
            if not bench_data["exception"]:
                self.undo_bench_migrate(bench_data["object"])

        for backup in self.backup_manager.backups:
            self.backup_manager.restore(backup, force=True)
            # self.output.print(f'Restored {backup.bench}'s {backup.src.name}.')

        # Clean up newly created files that didn't exist before migration
        self.backup_manager.cleanup_new_files()

        self.undo_services_migrate()

        self.output.print(f"[bold]v{self.version!s}[/bold] rollback successful")
        self.logger.info("-" * 40)

    def services_basic_backup(self):
        if not self.services_manager.compose_file_manager.exists():
            raise MigrationExceptionInBench(
                f"Services compose at {self.services_manager.compose_file_manager} not found.",
            )
        self.backup_manager.backup(self.services_manager.compose_file_manager.compose_path)

    def migrate_services(self):
        pass

    def undo_services_migrate(self):
        pass

    def migrate_benches(self):
        main_error = False

        all_benches = self.benches_manager.get_all_benches()

        # migrate each bench
        for bench_name, bench_path in all_benches.items():
            is_infrastructure_only_migration = self.migration_executor.target_benches is None
            if is_infrastructure_only_migration:
                continue

            is_bench_not_targeted = bench_name not in self.migration_executor.target_benches
            if is_bench_not_targeted:
                continue

            if bench_name in self.migration_executor.exclude_benches:
                self.output.print(f"Skipping {bench_name} (--exclude-bench)", emoji_code="")
                continue

            bench = MigrationBench(name=bench_name, path=bench_path.parent, output=self.output)

            bench_version = get_bench_migration_version(bench.path)

            # Check if bench is already at or above this migration version.
            # --rerun overrides this so the migration runs regardless.
            if not self.migration_executor.rerun and bench_version >= self.version:
                self.output.print(
                    f"Bench {bench_name} already at v{bench_version}, skipping migration to v{self.version}",
                )
                continue

            if bench.name in self.migration_executor.migrate_benches.keys():
                bench_info = self.migration_executor.migrate_benches[bench.name]
                if bench_info["exception"]:
                    self.output.print(f"Skipping migration for failed bench [fm.info]{bench.name}[/fm.info]")
                    main_error = True
                    continue

            self.migration_executor.set_bench_data(bench, migration_version=self.version)
            try:
                self.bench_basic_backup(bench)
                self.migrate_bench(bench)
            except Exception as e:
                traceback_str = capture_and_format_exception()
                self.logger.error(f"{bench.name} [ EXCEPTION TRACEBACK ]:\n {traceback_str}")
                self.output.update_live()
                main_error = True
                self.migration_executor.set_bench_data(bench, e, self.version)

                # restore all backup files
                for backup in self.backup_manager.backups:
                    if backup.bench == bench.name:
                        self.backup_manager.restore(backup, force=True)

                self.undo_bench_migrate(bench)
                self.logger.info(f"Undo successfull for bench: {bench.name}")

                try:
                    output = bench.docker.compose.down(
                        remove_orphans=True,
                        volumes=False,
                        timeout=DOCKER_COMPOSE_DOWN_TIMEOUT_SECONDS,
                        stream=True,
                    )
                except Exception:
                    pass

        if main_error:
            raise MigrationExceptionInBench("")

    def bench_basic_backup(self, bench: MigrationBench):
        self.output.print(f"Migrating bench [bold][fm.info]{bench.name}[/fm.info][/bold]")

        if self.migration_executor.skip_backup:
            self.output.warning(f"Skipping backup for {bench.name}")
            return

        if bench.name in self.migration_executor.skip_backup_for:
            self.output.warning(f"Skipping backup for {bench.name}")
            return

        bench_config_path = bench.path / "bench_config.toml"
        if bench_config_path.exists():
            self.backup_manager.backup(bench_config_path, bench_name=bench.name)

        self.backup_manager.backup(bench.path / "docker-compose.yml", bench_name=bench.name)

        bench_common_site_config = bench.path / "workspace" / "frappe-bench" / "sites" / "common_site_config.json"
        self.backup_manager.backup(bench_common_site_config, bench_name=bench.name)

        # Every recorded site, not just one named after the bench. A migration that backed up one
        # site left every other site of a multi-site bench with no way back, and on a bench whose
        # site is not named after it (`shop` serving `shop.localhost`) it read
        # `sites/shop/site_config.json`, found nothing, and `DatabaseServerServiceInfo` raised a
        # ValidationError with no `name` or `user` to build from: the migration aborted before
        # backing up anything at all. `raise_exception=False` never covered that, because it only
        # guards the password check further down.
        for site in bench.site_names:
            site_config = bench.path / "workspace" / "frappe-bench" / "sites" / site / "site_config.json"
            if not site_config.is_file():
                self.output.warning(
                    f"{bench.name}: no site_config.json for recorded site '{site}', skipping its backup. "
                    f"Expected it at {site_config}."
                )
                continue
            self.backup_manager.backup(site_config, bench_name=bench.name)

            site_db_info = DatabaseServerServiceInfo.import_from_bench(
                bench_path=bench.path,
                site_name=site,
                raise_exception=False,
            )

            self.bench_db_backup(
                bench=bench,
                db_info=site_db_info,
                bench_docker=bench.docker,
                bench_compose_file=bench.compose_file_manager,
                backup_manager=self.backup_manager,
                site=site,
            )

    def migrate_bench(self, bench: MigrationBench):
        pass

    def undo_bench_migrate(self, bench: MigrationBench):
        pass

    def _resolve_database_name(
        self, bench: MigrationBench, db_info: DatabaseServerServiceInfo, site: str | None = None
    ) -> str | None:
        site = site or bench.name
        if db_info.name:
            return db_info.name

        bench_config_path = bench.path / "bench_config.toml"
        if bench_config_path.exists():
            try:
                import tomlkit

                with open(bench_config_path) as f:
                    bench_config = tomlkit.parse(f.read())
                    # `db_name` is a bench-level key from before the schema became the site's, so it
                    # only answers for the site named after the bench. Handing it to a sibling site
                    # would dump the FIRST site's schema under the second site's name.
                    if site == bench.name:
                        db_name = bench_config.get("db_name")
                        if db_name:
                            return db_name
            except Exception as e:
                self.output.warning(f"Failed to read db_name from bench_config.toml: {e}")

        return None

    def _resolve_mysql_home(self, bench: MigrationBench, site: str | None = None) -> str | None:
        """``MYSQL_HOME`` for one site's dump when its database is external.

        Read straight out of ``bench_config.toml`` rather than through the model, so it works
        against whatever config version is already on disk. Without it the dump connects in
        plaintext, because the `mariadb` client never reads ``db_ssl_*`` from ``site_config.json``,
        and an enforcing server refuses it with 3159. None for a site on the global-db container.

        Both shapes are accepted because a migration meets either: ``[database."<site>"]`` at the
        top level before the sites table lands, and ``[sites."<site>".database]`` after. Reading
        only one of them would silently drop TLS on a re-run, which is exactly when the top-level
        table has already moved.
        """
        site = site or bench.name
        bench_config_path = bench.path / "bench_config.toml"
        if not bench_config_path.exists():
            return None

        try:
            import tomlkit

            doc = tomlkit.parse(bench_config_path.read_text())
        except Exception as e:
            self.output.warning(f"Failed to read database table from bench_config.toml: {e}")
            return None

        external = site in (doc.get("database") or {}) or bool(
            ((doc.get("sites") or {}).get(site) or {}).get("database")
        )
        return db_tls.site_mysql_home(site) if external else None

    def bench_db_backup(
        self,
        bench: MigrationBench,
        db_info: DatabaseServerServiceInfo,
        bench_docker,
        bench_compose_file,
        backup_manager: BackupManager,
        site: str | None = None,
    ):
        """Dump one SITE's schema. `site` defaults to the bench's name, which is what it is on every
        pre-decoupling bench and what every caller meant before a bench could hold several."""
        site = site or bench.name
        self.output.change_head(f"Taking {site} db backup")

        db_name = self._resolve_database_name(bench, db_info, site)

        if not db_name:
            self.output.warning(
                f"Could not determine database name for {site}.\n"
                f"Checked: site_config.json, db_info, bench_config.toml.",
            )

            skip_backup_prompt = [
                f"Database backup will be skipped for site '{site}'.",
                "Do you want to continue migration without database backup?",
            ]

            user_choice = self.output.prompt_ask(
                prompt="\n".join(skip_backup_prompt),
                choices=["yes", "no"],
                required_flag="--skip-all-backup or --skip-backup-for <bench>",
            )

            if user_choice == "no":
                self.output.display_error(f"User chose to abort migration for {bench.name}")
                raise MigrationExceptionInBench(
                    f"Migration aborted for {bench.name}: Unable to determine the database name for "
                    f"site '{site}'. User declined to skip database backup.",
                )

            self.output.warning(f"Skipping database backup for site '{site}'")
            return

        mariadb_manager = MariaDBManager(
            db_info,
            bench_compose_file,
            bench_docker,
            run_on_compose_service="frappe",
            mysql_home=self._resolve_mysql_home(bench, site),
        )

        from datetime import datetime

        current_datetime = datetime.now()
        formatted_date = current_datetime.strftime("%d-%m-%Y--%H-%M-%S")

        # Keyed by SITE, not by bench: two sites of one bench dump within the same second, and a
        # bench-keyed name made the second overwrite the first, so a "successful" multi-site backup
        # left one dump holding one schema.
        db_sql_file_name = f"db-{site}-{formatted_date}.sql"

        # Handed across the container boundary through the one directory BOTH runtimes mount.
        # This used to be `/workspace/.cache`, which only exists on the host when the whole
        # workspace is bind-mounted, i.e. mount runtime. On an image bench the dump was written
        # into the container's own filesystem, the host then looked for a file that was never
        # there, and the migration aborted reporting a DB export failure.
        container_db_sql_file_path, transit_rel = container_transit_path(db_sql_file_name)
        host_db_sql_file_path: Path = bench.path / "workspace" / transit_rel

        backup_gz_file_backup_data_path: Path = (
            bench.path / backup_manager.bench_backup_dir / self.version.version / f"{db_sql_file_name}.gz"
        )

        mariadb_manager.db_export(db_name, container_db_sql_file_path)

        if not host_db_sql_file_path.exists():
            # The export reported success but nothing reached the host, which means the transit
            # path is not shared with this container. Say that, rather than letting the gzip below
            # fail with a FileNotFoundError that names neither the bench nor the cause.
            raise MigrationExceptionInBench(
                f"{bench.name}: the database dump for {site} did not reach the host at "
                f"{host_db_sql_file_path}. The export ran, so this is a mount problem, not a "
                f"database one: the frappe container is not sharing {transit_rel.parent}.",
            )

        import gzip
        import shutil

        # Compress the file using gzip
        with open(host_db_sql_file_path, "rb") as f_in:
            with gzip.open(backup_gz_file_backup_data_path, "wb") as f_out:
                shutil.copyfileobj(f_in, f_out)

        # Transit only: the dump must not be left sitting in the log directory.
        host_db_sql_file_path.unlink()

        self.output.print(f"[fm.info]{site}[/fm.info] db backup completed successfully.")
