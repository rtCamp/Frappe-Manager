"""
Migration orchestration and execution flow.

Handles the main migration loop, progress tracking, error handling coordination,
and service management during migrations.
"""

from typing import TYPE_CHECKING

from frappe_manager.logger import bind, get_logger
from frappe_manager.migration_manager.migration_exections import MigrationExceptionInBench
from frappe_manager.utils.helpers import capture_and_format_exception

if TYPE_CHECKING:
    from frappe_manager.migration_manager.migration_base import MigrationBase
    from frappe_manager.migration_manager.migration_executor import MigrationExecutor


class MigrationOrchestrator:
    """
    Orchestrates migration execution flow.

    Responsibilities:
    - Run migration loop through all migrations
    - Track undo stack for rollbacks
    - Handle MigrationExceptionInBench (partial failures)
    - Coordinate with services manager
    - Update rollback version tracking
    """

    def __init__(self, executor: "MigrationExecutor"):
        self.executor = executor
        self.logger = get_logger(component="migration")
        self.undo_stack: list[MigrationBase] = []
        self.exception_in_bench_occurred = False

    def execute_migrations(self) -> bool:
        """
        Execute all migrations in sequence.

        Returns:
            bool: True if all migrations succeeded, False if any failed

        Raises:
            MigrationExceptionInBench: If any bench migration failed
            Exception: If any system migration failed
        """
        self._ensure_global_services_running()

        prev_migration: MigrationBase | None = None

        for migration in self.executor.migrations:
            self.executor.output.update_head(f"Running migration introduced in v{migration.version}")
            self.logger.info(f"[{migration.version}] : Migration starting")

            try:
                self.undo_stack.append(migration)
                with bind(operation=f"migrate-{migration.version}"):
                    migration.up()

                prev_migration = migration
                if not self.exception_in_bench_occurred:
                    self.executor.rollback_version = prev_migration.get_rollback_version()

            except MigrationExceptionInBench as e:
                captured_output = capture_and_format_exception(traceback_max_frames=0)
                self.logger.error(f"[{migration.version}] : Migration Failed\n{captured_output}")

                # If this isn't the last migration, mark exception but continue
                if migration.version < self.executor.migrations[-1].version:
                    self.exception_in_bench_occurred = True
                    continue
                raise e

            except Exception as e:
                captured_output = capture_and_format_exception()
                self.logger.error(f"[{migration.version}] : Migration Failed\n{captured_output}")
                raise e

        # If any bench exceptions occurred during migration loop, raise now
        if self.exception_in_bench_occurred:
            raise MigrationExceptionInBench("")

        return True

    def rollback_migrations(self):
        """
        Rollback all migrations in undo stack that are newer than rollback_version.

        Iterates through undo stack in reverse order and calls down() on each migration.
        """
        for migration in reversed(self.undo_stack):
            if migration.version > self.executor.rollback_version:
                self.executor.output.change_head(f"Rolling back migration introduced in v{migration.version}")
                self.logger.info(f"[{migration.version}] : Rollback starting")
                try:
                    migration.down()
                except Exception as e:
                    self.logger.error(f"[{migration.version}] : Rollback Failed\n{e}")
                    raise e

    def _ensure_global_services_running(self):
        """
        Ensure global services (MariaDB/PostgreSQL) are running before migration.

        Creates services if not initialized, starts them if stopped.
        Logs warnings if services check fails but continues migration.

        Only genuine service/docker/compose/filesystem failures are tolerated (warn and
        continue). Programming errors (AttributeError, TypeError, ImportError, KeyError,
        ...) propagate instead of being misreported as "could not verify/start global
        services", which would hide a real bug behind a "fm services start" suggestion.
        """
        from ruamel.yaml import YAMLError

        from frappe_manager.docker.compose_exceptions import (
            ComposeFileException,
            ComposeSecretNotFoundError,
            ComposeServiceNotFound,
        )
        from frappe_manager.docker.docker_exceptions import DockerException
        from frappe_manager.output_manager.context_managers import temporary_stop
        from frappe_manager.output_manager.silent_output import SilentOutputHandler
        from frappe_manager.services_manager.services import ServicesManager
        from frappe_manager.services_manager.services_exceptions import (
            DatabaseServiceException,
            ServicesComposeNotExist,
            ServicesException,
        )

        # Failures that mean "the services stack itself is unhealthy, unreachable or
        # unreadable". These are operator-actionable, so they warn and let migration continue.
        tolerated_service_failures = (
            DockerException,  # every compose call (pull/up/ps): daemon down, image missing, port clash
            ServicesException,  # base of ServicesNotCreated, raised by entrypoint_checks/create
            ServicesComposeNotExist,  # services dir present but its docker-compose.yml is gone
            DatabaseServiceException,  # DatabaseServicePasswordNotFound while wiring MariaDBManager
            ComposeFileException,  # services compose is empty or has no services: section
            ComposeServiceNotFound,  # compose file lacks global-nginx-proxy (init -> ProxyStoragePaths)
            ComposeSecretNotFoundError,  # db_root_password secret entry absent from the compose file
            YAMLError,  # corrupt/truncated services docker-compose.yml
            OSError,  # permissions, missing dirs, unreadable template, unwritable fm_headers.conf
        )

        try:
            services_manager = ServicesManager(output_handler=SilentOutputHandler())

            if not services_manager.path.exists():
                with temporary_stop(self.executor.output):
                    self.executor.output.print(
                        "Global services not initialized. Creating...",
                        emoji_code=":construction:",
                    )
                    services_manager.init()
                    services_manager.entrypoint_checks(start=True)
                    self.executor.output.print("Global services started successfully", emoji_code=":white_check_mark:")
                return

            services_manager.init()

            services_list = services_manager.compose_file_manager.get_services_list()
            all_running = all(services_manager.is_service_running(svc) for svc in services_list)

            if not all_running:
                with temporary_stop(self.executor.output):
                    self.executor.output.print(
                        "Global services not running. Starting them now...",
                        emoji_code=":construction:",
                    )
                    services_manager.start_service()
                    self.executor.output.print("Global services started successfully", emoji_code=":white_check_mark:")
        except tolerated_service_failures as e:
            self.logger.error(f"Failed to ensure global services are running: {e}\n{capture_and_format_exception()}")
            with temporary_stop(self.executor.output):
                self.executor.output.print(
                    "Warning: Could not verify/start global services. "
                    "Migration may fail if services are not running. "
                    "Try manually: fm services start",
                    emoji_code=":warning:",
                )
