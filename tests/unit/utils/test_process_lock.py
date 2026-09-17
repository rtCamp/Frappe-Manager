"""The host/bench locks: fm processes stop being blind to each other.

flock contention is between PROCESSES: two grips inside one process never contend, so an
in-process "second holder" would make every test below pass vacuously. The conflicting
holder is therefore a real subprocess running plain fcntl (no fm import, so it starts in
milliseconds), held open until the test releases it.

Defended decisions:
* shared+shared coexist (daily life unchanged); exclusive is alone in both directions
* a refusal names the holder written into the lock file
* the bench decorator locks the bench acted on, no-ops with no bench (standalone bake),
  and bench A's lock never touches bench B
* the executor takes the exclusive host grip and RELEASES it on completion, because the
  command that triggered an inline gate migration takes its own shared grip afterwards
"""

import subprocess
import sys
from pathlib import Path
from unittest.mock import MagicMock, Mock, patch

import pytest
import typer

from frappe_manager.utils import process_lock
from frappe_manager.utils.process_lock import acquire, bench_lock, read_holder

_HOLDER_CODE = """
import fcntl, sys
handle = open(sys.argv[1], "a")
flags = fcntl.LOCK_EX if sys.argv[2] == "exclusive" else fcntl.LOCK_SH
fcntl.flock(handle, flags)
print("held", flush=True)
sys.stdin.readline()
"""


class _Holder:
    """A real second process gripping ``path`` until released."""

    def __init__(self, path: Path, exclusive: bool):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.proc = subprocess.Popen(
            [sys.executable, "-c", _HOLDER_CODE, str(path), "exclusive" if exclusive else "shared"],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            text=True,
        )
        assert self.proc.stdout is not None
        assert self.proc.stdout.readline().strip() == "held"

    def release(self):
        if self.proc.stdin is not None:
            self.proc.stdin.close()
        self.proc.wait(timeout=10)


@pytest.fixture
def lock_path(tmp_path):
    path = tmp_path / "migration.lock"
    path.touch()
    return path


class TestGripSemantics:
    def test_shared_grips_coexist(self, lock_path):
        holder = _Holder(lock_path, exclusive=False)
        try:
            handle = acquire(lock_path, exclusive=False)
            assert handle is not None
            handle.close()
        finally:
            holder.release()

    def test_exclusive_is_refused_while_a_shared_grip_exists(self, lock_path):
        holder = _Holder(lock_path, exclusive=False)
        try:
            assert acquire(lock_path, exclusive=True) is None
        finally:
            holder.release()

    def test_everything_is_refused_while_an_exclusive_grip_exists(self, lock_path):
        holder = _Holder(lock_path, exclusive=True)
        try:
            assert acquire(lock_path, exclusive=False) is None
            assert acquire(lock_path, exclusive=True) is None
        finally:
            holder.release()

    def test_a_released_grip_frees_the_lock_instantly(self, lock_path):
        holder = _Holder(lock_path, exclusive=True)
        holder.release()
        handle = acquire(lock_path, exclusive=True)
        assert handle is not None
        handle.close()

    def test_the_holder_is_recorded_and_readable(self, lock_path):
        handle = acquire(lock_path, exclusive=True, holder="migration")
        assert handle is not None
        assert read_holder(lock_path).startswith("migration (pid ")
        handle.close()


class TestBenchLockDecorator:
    def _decorated(self, calls, **deco_kwargs):
        @bench_lock(**deco_kwargs)
        def command(ctx, benchname=None, address=None):
            calls.append(benchname or address)
            return "ran"

        return command

    def test_the_command_runs_and_the_grip_is_released_afterwards(self, tmp_path):
        calls = []
        command = self._decorated(calls, operation="switch")

        assert command(MagicMock(), benchname="shop") == "ran"

        assert calls == ["shop"]
        # released: an exclusive grip on the same bench succeeds immediately
        handle = acquire(process_lock.bench_lock_path("shop"), exclusive=True)
        assert handle is not None
        handle.close()

    def test_a_busy_bench_is_refused_naming_the_holder(self, tmp_path):
        holder = _Holder(process_lock.bench_lock_path("shop"), exclusive=True)
        process_lock.bench_lock_path("shop").write_text("bake (pid 4242)")
        calls = []
        command = self._decorated(calls, operation="switch")

        try:
            with pytest.raises(typer.Exit):
                command(MagicMock(), benchname="shop")
        finally:
            holder.release()

        assert calls == []

    def test_bench_a_never_fences_bench_b(self, tmp_path):
        holder = _Holder(process_lock.bench_lock_path("a.localhost"), exclusive=True)
        calls = []
        command = self._decorated(calls, operation="restart")

        try:
            assert command(MagicMock(), benchname="b.localhost") == "ran"
        finally:
            holder.release()

    def test_no_bench_means_no_bench_lock(self, tmp_path):
        """A standalone bake acts on no bench; there is nothing to lock and nothing that
        could refuse it."""
        calls = []
        command = self._decorated(calls, shared=True, operation="bake")

        assert command(MagicMock(), benchname=None) == "ran"

    def test_a_shared_holder_does_not_fence_another_shared_holder(self, tmp_path):
        """Two bakes on one bench may overlap; only mutators are alone."""
        holder = _Holder(process_lock.bench_lock_path("shop"), exclusive=False)
        calls = []
        command = self._decorated(calls, shared=True, operation="bake")

        try:
            assert command(MagicMock(), benchname="shop") == "ran"
        finally:
            holder.release()


class TestExecutorHostGrip:
    @pytest.fixture
    def mock_fm_config(self):
        return MagicMock()

    def _executor(self, mock_fm_config, mock_output):
        from frappe_manager.migration_manager.migration_executor import MigrationExecutor
        from frappe_manager.migration_manager.version import Version

        mock_fm_config.version = Version("0.18.0")
        with (
            patch("frappe_manager.migration_manager.migration_executor.get_current_fm_version", return_value="0.19.0"),
            patch("frappe_manager.migration_manager.migration_executor.get_logger"),
        ):
            return MigrationExecutor(mock_fm_config, output_handler=mock_output)

    def test_a_busy_host_refuses_the_migration_before_anything_runs(self, mock_fm_config):
        output = Mock()
        executor = self._executor(mock_fm_config, output)
        holder = _Holder(process_lock.migration_lock_path(), exclusive=False)
        process_lock.migration_lock_path().write_text("bake (pid 7)")

        try:
            with patch.object(executor, "_execute") as run:
                assert executor.execute() is False
        finally:
            holder.release()

        run.assert_not_called()
        assert "bake (pid 7)" in output.display_error.call_args.args[0]

    def test_the_exclusive_grip_is_released_after_the_run(self, mock_fm_config):
        """The command behind an inline gate migration takes its own SHARED grip next; a
        grip that rode to process exit would refuse the very command that migrated."""
        executor = self._executor(mock_fm_config, Mock())

        with patch.object(executor, "_execute", return_value=True):
            assert executor.execute() is True

        handle = acquire(process_lock.migration_lock_path(), exclusive=True)
        assert handle is not None
        handle.close()
