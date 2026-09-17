from unittest.mock import Mock, patch

import pytest

from frappe_manager.migration_manager.migration_executor import MINIMUM_SUPPORTED_VERSION, MigrationExecutor
from frappe_manager.migration_manager.version import Version


class TestMigrationExecutorVersionEnforcement:
    def test_no_migration_when_versions_equal(self, mock_fm_config):
        mock_fm_config.get_system_migration_version.return_value = Version("0.19.0")

        with (
            patch("frappe_manager.migration_manager.migration_executor.get_current_fm_version", return_value="0.19.0"),
            patch("frappe_manager.migration_manager.migration_executor.get_logger"),
        ):
            executor = MigrationExecutor(mock_fm_config)
            result = executor.execute()

            assert result is True
            assert len(executor.migrations) == 0

    def test_no_migration_when_current_version_lower(self, mock_fm_config):
        mock_fm_config.get_system_migration_version.return_value = Version("0.19.0")

        with (
            patch("frappe_manager.migration_manager.migration_executor.get_current_fm_version", return_value="0.18.0"),
            patch("frappe_manager.migration_manager.migration_executor.get_logger"),
        ):
            executor = MigrationExecutor(mock_fm_config)
            result = executor.execute()

            assert result is True
            assert len(executor.migrations) == 0

    def test_rerun_triggers_migration_when_versions_equal(self, mock_fm_config):
        """--rerun forces migration discovery even when prev_version == current_version."""
        mock_fm_config.get_system_migration_version.return_value = Version("0.19.0")
        mock_fm_config.export_to_toml = Mock(return_value=True)

        mock_migration = Mock()
        mock_migration.version = Version("0.19.0")
        mock_migration.up = Mock()
        mock_migration.down = Mock()
        mock_migration.set_migration_executor = Mock()
        mock_migration.get_rollback_version = Mock(return_value=Version("0.19.0"))

        with (
            patch("frappe_manager.migration_manager.migration_executor.get_current_fm_version", return_value="0.19.0"),
            patch("frappe_manager.migration_manager.migration_executor.get_logger"),
        ):
            mock_output = Mock()
            executor = MigrationExecutor(
                mock_fm_config,
                rerun=True,
                auto_proceed=True,
                on_failure="rollback",
                migrate_global_services=True,
                output_handler=mock_output,
            )

            with (
                patch.object(executor, "_check_benches_need_migration", return_value=False),
                patch.object(executor.discovery, "discover_migrations", return_value=[mock_migration]),
                patch.object(executor.orchestrator, "execute_migrations") as mock_execute,
                patch.object(executor.error_handler, "finalize_success"),
            ):
                result = executor.execute()

            assert result is True
            mock_execute.assert_called_once()

    def test_no_migration_without_rerun_when_versions_equal(self, mock_fm_config):
        """Without --rerun, no migration runs when versions are equal (regardless of migrate_global_services)."""
        mock_fm_config.get_system_migration_version.return_value = Version("0.19.0")

        with (
            patch("frappe_manager.migration_manager.migration_executor.get_current_fm_version", return_value="0.19.0"),
            patch("frappe_manager.migration_manager.migration_executor.get_logger"),
        ):
            executor = MigrationExecutor(mock_fm_config, migrate_global_services=True)
            result = executor.execute()

            assert result is True
            assert len(executor.migrations) == 0

    def test_rerun_defaults_to_false(self, mock_fm_config):
        """Constructor default for rerun must be False to preserve existing behaviour."""
        with (
            patch("frappe_manager.migration_manager.migration_executor.get_current_fm_version", return_value="0.19.0"),
            patch("frappe_manager.migration_manager.migration_executor.get_logger"),
        ):
            executor = MigrationExecutor(mock_fm_config)
            assert executor.rerun is False

    def test_blocks_migration_below_minimum_version(self, mock_fm_config):
        mock_fm_config.get_system_migration_version.return_value = Version("0.17.0")

        with (
            patch("frappe_manager.migration_manager.migration_executor.get_current_fm_version", return_value="0.19.0"),
            patch("frappe_manager.migration_manager.migration_executor.get_logger"),
        ):
            mock_output = Mock()
            executor = MigrationExecutor(mock_fm_config, migrate_global_services=True, output_handler=mock_output)
            result = executor.execute()

            assert result is False
            assert mock_output.display_error.call_count >= 3

            error_calls = [call[0][0] for call in mock_output.display_error.call_args_list]
            assert any("Cannot migrate from v0.17.0" in str(call) for call in error_calls)
            assert any("v0.18.0" in str(call) for call in error_calls)

    def test_allows_migration_from_minimum_version(self, mock_fm_config):
        mock_fm_config.get_system_migration_version.return_value = Version("0.18.0")

        with (
            patch("frappe_manager.migration_manager.migration_executor.get_current_fm_version", return_value="0.19.0"),
            patch("frappe_manager.migration_manager.migration_executor.get_logger"),
            patch("frappe_manager.migration_manager.migration_discovery.pkgutil.iter_modules", return_value=[]),
        ):
            mock_output = Mock()
            executor = MigrationExecutor(mock_fm_config, output_handler=mock_output)
            result = executor.execute()

            assert result is True

    def test_minimum_supported_version_is_0_18_0(self):
        assert Version("0.18.0") == MINIMUM_SUPPORTED_VERSION

    def test_migration_executor_sets_correct_versions(self, mock_fm_config):
        mock_fm_config.get_system_migration_version.return_value = Version("0.18.0")

        with (
            patch("frappe_manager.migration_manager.migration_executor.get_current_fm_version", return_value="0.19.0"),
            patch("frappe_manager.migration_manager.migration_executor.get_logger"),
        ):
            executor = MigrationExecutor(mock_fm_config)

            assert executor.prev_version == Version("0.18.0")
            assert executor.current_version == Version("0.19.0")
            assert executor.rollback_version == Version("0.18.0")


class TestMigrationExecutorMigrationDiscovery:
    def test_discovers_migration_in_version_range(self, mock_fm_config):
        mock_fm_config.get_system_migration_version.return_value = Version("0.18.0")
        mock_fm_config.export_to_toml = Mock(return_value=True)

        with (
            patch("frappe_manager.migration_manager.migration_executor.get_current_fm_version", return_value="0.19.0"),
            patch("frappe_manager.migration_manager.migration_executor.get_logger"),
        ):
            mock_output = Mock()
            executor = MigrationExecutor(mock_fm_config, output_handler=mock_output)
            result = executor.execute()

            assert executor.prev_version == Version("0.18.0")
            assert executor.current_version == Version("0.19.0")
            assert result is True

    def test_skips_migration_outside_version_range(self, mock_fm_config):
        mock_fm_config.get_system_migration_version.return_value = Version("0.18.0")

        mock_migration_class = Mock()
        mock_migration_class.version = Version("0.20.0")
        mock_migration_class.up = Mock()
        mock_migration_class.down = Mock()
        mock_migration_class.set_migration_executor = Mock()
        mock_migration_instance = Mock()
        mock_migration_instance.version = Version("0.20.0")
        mock_migration_class.return_value = mock_migration_instance

        mock_module = Mock()
        mock_module.Migrate_0_20_0 = mock_migration_class

        with (
            patch("frappe_manager.migration_manager.migration_executor.get_current_fm_version", return_value="0.19.0"),
            patch("frappe_manager.migration_manager.migration_executor.get_logger"),
            patch(
                "frappe_manager.migration_manager.migration_discovery.pkgutil.iter_modules",
                return_value=[(None, "migrate_0_20_0", None)],
            ),
            patch(
                "frappe_manager.migration_manager.migration_discovery.importlib.import_module",
                return_value=mock_module,
            ),
        ):
            mock_output = Mock()
            executor = MigrationExecutor(mock_fm_config, output_handler=mock_output)
            executor.execute()

            assert len(executor.migrations) == 0

    def test_skips_version_0_0_0_migrations(self, mock_fm_config):
        mock_fm_config.get_system_migration_version.return_value = Version("0.18.0")

        mock_migration_class = Mock()
        mock_migration_class.version = Version("0.0.0")
        mock_migration_class.up = Mock()
        mock_migration_class.down = Mock()
        mock_migration_class.set_migration_executor = Mock()

        mock_module = Mock()
        mock_module.Migrate_0_0_0 = mock_migration_class

        with (
            patch("frappe_manager.migration_manager.migration_executor.get_current_fm_version", return_value="0.19.0"),
            patch("frappe_manager.migration_manager.migration_executor.get_logger"),
            patch(
                "frappe_manager.migration_manager.migration_discovery.pkgutil.iter_modules",
                return_value=[(None, "migrate_0_0_0", None)],
            ),
            patch(
                "frappe_manager.migration_manager.migration_discovery.importlib.import_module",
                return_value=mock_module,
            ),
        ):
            mock_output = Mock()
            executor = MigrationExecutor(mock_fm_config, output_handler=mock_output)
            executor.execute()

            assert len(executor.migrations) == 0


class TestMigrationExecutorUserPrompt:
    @staticmethod
    def _run_with_answer(mock_fm_config, answer):
        """Drive execute() to the "Do you want to proceed?" prompt with a canned answer.

        Every boundary that could reach real input, real docker or the real services
        directory is closed here: ``prompt_ask`` is an explicit Mock, and
        ``ServicesManager`` (imported inside MigrationOrchestrator._ensure_global_services_running)
        is replaced by a stub reporting an already-running installation.
        """
        mock_fm_config.get_system_migration_version.return_value = Version("0.18.0")

        mock_migration = Mock()
        mock_migration.version = Version("0.19.0")
        mock_migration.up = Mock()
        mock_migration.get_rollback_version = Mock(return_value=Version("0.19.0"))

        mock_output = Mock()
        mock_output.prompt_ask = Mock(return_value=answer)

        with (
            patch("frappe_manager.migration_manager.migration_executor.get_current_fm_version", return_value="0.19.0"),
            patch("frappe_manager.migration_manager.migration_executor.get_logger"),
            patch("frappe_manager.services_manager.services.ServicesManager") as mock_services_cls,
        ):
            services = mock_services_cls.return_value
            services.path.exists.return_value = True
            services.compose_file_manager.get_services_list.return_value = ["global-db"]
            services.is_service_running.return_value = True

            executor = MigrationExecutor(mock_fm_config, migrate_global_services=True, output_handler=mock_output)

            with (
                patch.object(executor.discovery, "discover_migrations", return_value=[mock_migration]),
                patch.object(executor, "_check_benches_need_migration", return_value=False),
            ):
                result = executor.execute()

        return result, mock_output, mock_migration, services

    @pytest.mark.timeout(15)
    def test_prompts_user_when_migrations_pending(self, mock_fm_config):
        """The proceed/abort prompt is asked once, and each answer picks its branch.

        Regression guard: the "yes" branch continues into
        MigrationOrchestrator.execute_migrations(), which builds a real ServicesManager
        and could shell out to docker (or block) on the way. That boundary is stubbed,
        so this test can never wait on real input or a real daemon.
        """
        yes_result, yes_output, yes_migration, yes_services = self._run_with_answer(mock_fm_config, "yes")

        # What the prompt asked
        yes_output.prompt_ask.assert_called_once()
        kwargs = yes_output.prompt_ask.call_args[1]
        assert kwargs["prompt"] == "Do you want to proceed?"
        assert [choice["value"] for choice in kwargs["choices"]] == ["yes", "no"]
        assert kwargs["required_flag"] == "--yes"
        assert kwargs["default"] == "no"  # a bare Enter aborts, everywhere

        # "yes" runs the migration and reports success
        assert yes_result is True
        yes_migration.up.assert_called_once_with()
        assert not any("Migration aborted" in str(call) for call in yes_output.print.call_args_list)

        # The pre-migration services check ran against the stub and, seeing everything
        # already running, did not ask for anything to be started.
        yes_services.is_service_running.assert_called_once_with("global-db")
        yes_services.start_service.assert_not_called()
        yes_services.entrypoint_checks.assert_not_called()

        # "no" aborts before touching the migration and reports failure
        no_result, no_output, no_migration, _ = self._run_with_answer(mock_fm_config, "no")

        no_output.prompt_ask.assert_called_once()
        assert no_result is False
        no_migration.up.assert_not_called()
        assert any("Migration aborted" in str(call) for call in no_output.print.call_args_list)

    @pytest.mark.timeout(15)
    def test_a_failing_migration_is_actually_rolled_back(self, mock_fm_config):
        """The undo stack must reach the error handler: the executor's copy used to be
        synced only on the SUCCESS path, so a failed migration's down() never ran and
        "Rollback complete." was printed with every backup unrestored and the
        half-migrated state left in place (found live by the v0.21.0 rename cutover,
        whose unrolled-back failure leaves every bench on the host dark)."""
        mock_fm_config.get_system_migration_version.return_value = Version("0.18.0")

        failing = Mock()
        failing.version = Version("0.19.0")
        failing.up = Mock(side_effect=RuntimeError("boom"))
        failing.get_rollback_version = Mock(return_value=Version("0.19.0"))

        mock_output = Mock()
        mock_output.prompt_ask = Mock(return_value="yes")

        with (
            patch(
                "frappe_manager.migration_manager.migration_executor.get_current_fm_version", return_value="0.19.0"
            ),
            patch("frappe_manager.migration_manager.migration_executor.get_logger"),
            patch("frappe_manager.services_manager.services.ServicesManager") as mock_services_cls,
        ):
            services = mock_services_cls.return_value
            services.path.exists.return_value = True
            services.is_service_running.return_value = True

            executor = MigrationExecutor(mock_fm_config, migrate_global_services=True, output_handler=mock_output)

            with (
                patch.object(executor.discovery, "discover_migrations", return_value=[failing]),
                patch.object(executor, "_check_benches_need_migration", return_value=False),
            ):
                result = executor.execute()

        assert result is False
        failing.down.assert_called_once_with()

    @pytest.mark.timeout(15)
    def test_ctrl_c_mid_migration_rolls_back_and_still_exits_as_interrupted(self, mock_fm_config):
        """KeyboardInterrupt is a BaseException, so the Exception handler never saw it:
        Ctrl+C walked away from a half-migrated host with no rollback and no record --
        pressed, of course, at exactly the moment a cutover looks hung. It now routes
        through the same --on-failure machinery and then re-raises, so the process still
        dies as interrupted."""
        mock_fm_config.get_system_migration_version.return_value = Version("0.18.0")

        interrupted = Mock()
        interrupted.version = Version("0.19.0")
        interrupted.up = Mock(side_effect=KeyboardInterrupt)
        interrupted.get_rollback_version = Mock(return_value=Version("0.19.0"))

        mock_output = Mock()
        mock_output.prompt_ask = Mock(return_value="yes")

        with (
            patch(
                "frappe_manager.migration_manager.migration_executor.get_current_fm_version", return_value="0.19.0"
            ),
            patch("frappe_manager.migration_manager.migration_executor.get_logger"),
            patch("frappe_manager.services_manager.services.ServicesManager") as mock_services_cls,
        ):
            services = mock_services_cls.return_value
            services.path.exists.return_value = True
            services.is_service_running.return_value = True

            executor = MigrationExecutor(
                mock_fm_config, migrate_global_services=True, on_failure="rollback", output_handler=mock_output
            )

            with (
                patch.object(executor.discovery, "discover_migrations", return_value=[interrupted]),
                patch.object(executor, "_check_benches_need_migration", return_value=False),
                pytest.raises(KeyboardInterrupt),
            ):
                executor.execute()

        interrupted.down.assert_called_once_with()

    def test_aborts_and_reverts_when_user_says_no(self, mock_fm_config):
        mock_fm_config.get_system_migration_version.return_value = Version("0.18.0")

        mock_migration = Mock()
        mock_migration.version = Version("0.19.0")

        with (
            patch("frappe_manager.migration_manager.migration_executor.get_current_fm_version", return_value="0.19.0"),
            patch("frappe_manager.migration_manager.migration_executor.get_logger"),
        ):
            mock_output = Mock()
            executor = MigrationExecutor(mock_fm_config, migrate_global_services=True, output_handler=mock_output)

            mock_output.prompt_ask.return_value = "no"

            with (
                patch.object(executor.discovery, "discover_migrations", return_value=[mock_migration]),
                patch.object(executor, "_check_benches_need_migration", return_value=False),
            ):
                result = executor.execute()

            assert result is False

            print_calls = [str(call) for call in mock_output.print.call_args_list]
            assert any("Migration aborted" in call for call in print_calls)


class TestUnknownVersionRefusal:
    @pytest.mark.timeout(15)
    def test_an_unknown_host_ledger_refuses_instead_of_running_everything(self, mock_fm_config):
        """0.0.0 (fm could not read a version) used to bypass the minimum-version check as
        the fresh-install sentinel and let discovery select EVERY migration. It now refuses
        before discovery runs."""
        mock_fm_config.get_system_migration_version.return_value = Version("0.0.0")
        mock_output = Mock()

        with (
            patch("frappe_manager.migration_manager.migration_executor.get_current_fm_version", return_value="0.21.0"),
            patch("frappe_manager.migration_manager.migration_executor.get_logger"),
        ):
            executor = MigrationExecutor(mock_fm_config, migrate_global_services=True, output_handler=mock_output)

            with patch.object(executor.discovery, "discover_migrations") as discover:
                result = executor.execute()

        assert result is False
        discover.assert_not_called()
        messages = " ".join(str(call.args[0]) for call in mock_output.display_error.call_args_list)
        assert "will not guess" in messages


class TestExecutorIsTheSoleLedgerStamper:
    """P2: finalize_success/rollback are the ONLY writers of the services-tier ledger, and
    only when the services tier was actually part of the run -- a bench-only `fm migrate`
    must never move the host's `[migration_state].migrated_to`."""

    @staticmethod
    def _run(mock_fm_config, *, migrate_global_services, up=None):
        mock_fm_config.get_system_migration_version.return_value = Version("0.18.0")

        migration = Mock()
        migration.version = Version("0.19.0")
        migration.up = up or Mock()
        migration.get_rollback_version = Mock(return_value=Version("0.19.0"))

        mock_output = Mock()
        mock_output.prompt_ask = Mock(return_value="yes")

        with (
            patch("frappe_manager.migration_manager.migration_executor.get_current_fm_version", return_value="0.19.0"),
            patch("frappe_manager.migration_manager.migration_executor.get_logger"),
            patch("frappe_manager.services_manager.services.ServicesManager") as mock_services_cls,
        ):
            services = mock_services_cls.return_value
            services.path.exists.return_value = True
            services.is_service_running.return_value = True

            executor = MigrationExecutor(
                mock_fm_config,
                migrate_global_services=migrate_global_services,
                auto_proceed=True,
                on_failure="rollback",
                output_handler=mock_output,
            )

            with (
                patch.object(executor.discovery, "discover_migrations", return_value=[migration]),
                patch.object(executor, "_check_benches_need_migration", return_value=not migrate_global_services),
            ):
                result = executor.execute()

        return result

    @pytest.mark.timeout(15)
    def test_a_successful_services_run_stamps_the_ledger_to_current(self, mock_fm_config):
        result = self._run(mock_fm_config, migrate_global_services=True)

        assert result is True
        mock_fm_config.set_system_migration_version.assert_called_once_with(Version("0.19.0"))

    @pytest.mark.timeout(15)
    def test_a_failed_services_run_rewinds_the_ledger_it_reads(self, mock_fm_config):
        """Rollback used to rewind only the retired top-level `version` field, so the key the
        gates actually read stayed at the failed target."""
        result = self._run(mock_fm_config, migrate_global_services=True, up=Mock(side_effect=RuntimeError("boom")))

        assert result is False
        mock_fm_config.set_system_migration_version.assert_called_once_with(Version("0.18.0"))

    @pytest.mark.timeout(15)
    def test_a_bench_only_run_never_touches_the_host_ledger(self, mock_fm_config):
        """finalize_success used to bump the host version on EVERY successful run, including
        bench-only migrations that never ran the services tier."""
        result = self._run(mock_fm_config, migrate_global_services=False)

        assert result is True
        mock_fm_config.set_system_migration_version.assert_not_called()


class TestMigrationNeverPrunesOnlyHints:
    """Cleanup is command-triggered only: a successful migration prints a size-aware hint
    naming `fm services prune` and deletes NOTHING; a failed run does not even hint, its
    backups are the rollback."""

    @staticmethod
    def _host_sessions(count):
        """Session dirs under the (suite-isolated) host backups root."""
        import os
        import time

        from frappe_manager.migration_manager import backup_manager

        root = backup_manager.CLI_MIGARATIONS_DIR / "migrations"
        root.mkdir(parents=True, exist_ok=True)
        now = time.time()
        for i in range(count):
            d = root / f"session-{i}"
            d.mkdir()
            os.utime(d, (now - (count - i) * 100, now - (count - i) * 100))
        return root

    @staticmethod
    def _run(mock_fm_config, *, up=None):
        """A services-tier run like TestExecutorIsTheSoleLedgerStamper._run, but returning
        the output handler so the hint text itself can be asserted."""
        mock_fm_config.get_system_migration_version.return_value = Version("0.18.0")

        migration = Mock()
        migration.version = Version("0.19.0")
        migration.up = up or Mock()
        migration.get_rollback_version = Mock(return_value=Version("0.19.0"))

        mock_output = Mock()
        mock_output.prompt_ask = Mock(return_value="yes")

        with (
            patch("frappe_manager.migration_manager.migration_executor.get_current_fm_version", return_value="0.19.0"),
            patch("frappe_manager.migration_manager.migration_executor.get_logger"),
            patch("frappe_manager.services_manager.services.ServicesManager") as mock_services_cls,
        ):
            services = mock_services_cls.return_value
            services.path.exists.return_value = True
            services.is_service_running.return_value = True

            executor = MigrationExecutor(
                mock_fm_config,
                migrate_global_services=True,
                auto_proceed=True,
                on_failure="rollback",
                output_handler=mock_output,
            )

            with (
                patch.object(executor.discovery, "discover_migrations", return_value=[migration]),
                patch.object(executor, "_check_benches_need_migration", return_value=False),
            ):
                result = executor.execute()

        return result, mock_output

    @pytest.mark.timeout(15)
    def test_a_successful_run_hints_at_overflow_and_deletes_nothing(self, mock_fm_config):
        mock_fm_config.prune.keep_backup_sessions = 3
        root = self._host_sessions(5)

        result, output = self._run(mock_fm_config)

        assert result is True
        assert len(list(root.iterdir())) == 5  # nothing deleted, ever
        prints = " ".join(str(call) for call in output.print.call_args_list)
        assert "fm services prune" in prints
        assert "2 backup session(s) beyond the configured keep of 3" in prints

    @pytest.mark.timeout(15)
    def test_within_retention_there_is_no_hint(self, mock_fm_config):
        mock_fm_config.prune.keep_backup_sessions = 3
        self._host_sessions(2)

        result, output = self._run(mock_fm_config)

        assert result is True
        prints = " ".join(str(call) for call in output.print.call_args_list)
        assert "prune" not in prints

    @pytest.mark.timeout(15)
    def test_a_failed_run_neither_hints_nor_deletes(self, mock_fm_config):
        mock_fm_config.prune.keep_backup_sessions = 3
        root = self._host_sessions(5)

        result, output = self._run(mock_fm_config, up=Mock(side_effect=RuntimeError("boom")))

        assert result is False
        assert len(list(root.iterdir())) == 5
        prints = " ".join(str(call) for call in output.print.call_args_list)
        assert "fm services prune" not in prints


class TestDryRun:
    @pytest.mark.timeout(15)
    def test_dry_run_shows_the_plan_and_executes_nothing(self, mock_fm_config):
        """--dry-run is the scriptable plan viewer: the preamble prints, then a clean exit --
        no prompt (the -n path without --yes is a refusal by design), no orchestration, no
        ledger stamp."""
        mock_fm_config.get_system_migration_version.return_value = Version("0.18.0")

        migration = Mock()
        migration.version = Version("0.19.0")
        migration.get_rollback_version = Mock(return_value=Version("0.19.0"))

        mock_output = Mock()

        with (
            patch("frappe_manager.migration_manager.migration_executor.get_current_fm_version", return_value="0.19.0"),
            patch("frappe_manager.migration_manager.migration_executor.get_logger"),
        ):
            executor = MigrationExecutor(
                mock_fm_config, migrate_global_services=True, dry_run=True, output_handler=mock_output
            )

            with (
                patch.object(executor.discovery, "discover_migrations", return_value=[migration]),
                patch.object(executor, "_check_benches_need_migration", return_value=False),
                patch.object(executor.orchestrator, "execute_migrations") as orchestrate,
            ):
                result = executor.execute()

        assert result is True
        orchestrate.assert_not_called()
        mock_output.prompt_ask.assert_not_called()
        migration.up.assert_not_called()
        mock_fm_config.set_system_migration_version.assert_not_called()
        prints = " ".join(str(call) for call in mock_output.print.call_args_list)
        assert "Migration versions" in prints  # the plan preamble was shown
        assert "Dry run: nothing migrated." in prints
