"""
Defends ``MigrationErrorHandler._halt()``'s backup report.

``_halt()`` used to list ``backup_manager.backup_dir`` for every migration in the undo
stack merely because the manager object existed. Under --skip-db-backup /
--skip-config-backup, ``BackupManager.backup()`` returns before creating that
directory or recording anything in ``self.backups``, so the halt message named a path
that holds nothing. Only a manager whose ``.backups`` list is non-empty actually wrote
something; that is the one signal checked here.
"""

from unittest.mock import MagicMock

from frappe_manager.migration_manager.migration_error_handler import MigrationErrorHandler


class FakeBackupManager:
    def __init__(self, backup_dir, backups):
        self.backup_dir = backup_dir
        self.backups = backups


class FakeMigration:
    def __init__(self, backup_manager=None):
        self.backup_manager = backup_manager


def make_handler(undo_stack):
    executor = MagicMock()
    executor.undo_stack = undo_stack
    executor.output = MagicMock()
    return MigrationErrorHandler(executor), executor


def backup_report_lines(executor):
    return [
        call.args[0]
        for call in executor.output.print.call_args_list
        if call.args and "Backups taken" in call.args[0]
    ]


class TestHaltOnlyReportsBackupsThatWereActuallyTaken:
    def test_skipped_backup_manager_is_not_reported(self, tmp_path):
        """skip_file_backups left .backups empty: the dir must not be named."""
        empty_dir = tmp_path / "1.0.0"
        migration = FakeMigration(FakeBackupManager(empty_dir, backups=[]))
        handler, executor = make_handler([migration])

        handler._halt()

        assert backup_report_lines(executor) == []

    def test_manager_with_a_real_backup_is_reported(self, tmp_path):
        """A manager that recorded at least one backup must still be named."""
        real_dir = tmp_path / "1.0.0"
        migration = FakeMigration(FakeBackupManager(real_dir, backups=["taken"]))
        handler, executor = make_handler([migration])

        handler._halt()

        lines = backup_report_lines(executor)
        assert len(lines) == 1
        assert str(real_dir) in lines[0]

    def test_mixed_undo_stack_names_only_the_manager_that_has_backups(self, tmp_path):
        skipped_dir = tmp_path / "skipped"
        real_dir = tmp_path / "real"
        skipped = FakeMigration(FakeBackupManager(skipped_dir, backups=[]))
        real = FakeMigration(FakeBackupManager(real_dir, backups=["taken"]))
        handler, executor = make_handler([skipped, real])

        handler._halt()

        lines = backup_report_lines(executor)
        assert len(lines) == 1
        assert str(real_dir) in lines[0]
        assert str(skipped_dir) not in lines[0]

    def test_no_managers_hold_backups_omits_the_backups_line_entirely(self, tmp_path):
        migration = FakeMigration(None)
        handler, executor = make_handler([migration])

        handler._halt()

        assert backup_report_lines(executor) == []
