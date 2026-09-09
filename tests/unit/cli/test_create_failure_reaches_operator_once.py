"""End-to-end proof for `_handle_creation_failure`'s two closed loose ends, through the REAL
`cli_entrypoint` (main.py) rather than by reasoning about it.

Before this fix, `_handle_creation_failure` printed the exception itself
(`display_error("Error Occured: ...")`) AND, once its three raising branches were added, let the
same exception reach `cli_entrypoint`'s own `except` block, which printed it again -- the operator
saw the same text twice. This module drives a synthetic create failure through the exact code path
`fm create` uses (`BenchOrchestrator.create_bench` -> `cli_entrypoint`'s `except`) and asserts on
what actually lands on stderr, not on which functions were called.

It also proves the fourth exit -- a failure before the bench directory exists -- now fails the
command instead of returning 0, and that touching `main.py`'s handler contract left an unrelated
command's error path alone.
"""

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from frappe_manager.exceptions import FrappeManagerException
from frappe_manager.main import cli_entrypoint
from frappe_manager.output_manager.globals import get_global_output_handler
from frappe_manager.site_manager.modules.bench_orchestrator import BenchOrchestrator


@pytest.fixture(autouse=True)
def _contain_entrypoint_side_effects():
    """Same containment `test_root_refusal.py` uses: silence the atexit cleanup registration so
    it does not fire during interpreter shutdown against a handler this test has torn down."""
    with patch("frappe_manager.main.atexit.register"):
        yield


def _bench_that_never_made_it_to_disk() -> MagicMock:
    """A bench whose directory does not exist -- the only reachable shape of the fourth exit:
    `bench.path.mkdir()` is the first substantive statement of phase 1, so a failure with
    `bench.exists` still False can only be `mkdir()` itself failing."""
    bench = MagicMock()
    bench.name = "testbench"
    bench.site_name = "testbench"
    bench.path = Path("/tmp/fm-repro-bench-dir-does-not-exist")
    bench.exists = False
    bench.bench_config = MagicMock()
    bench.bench_config.runtime = "mount"
    bench.bench_config.seed_image = None
    return bench


def _typer_app_that_fails_before_the_bench_directory_exists(message: str):
    """Stands in for `frappe_manager.commands.app`: a create whose very first phase dies."""

    def _app(*_args, **_kwargs):
        bench = _bench_that_never_made_it_to_disk()
        orchestrator = BenchOrchestrator(bench, output_handler=get_global_output_handler())
        orchestrator._phase1_prepare_structure = MagicMock(side_effect=RuntimeError(message))
        orchestrator.create_bench(bench_only=True)

    return _app


def test_a_failure_before_the_bench_directory_exists_exits_non_zero(capsys: pytest.CaptureFixture[str]):
    """Item A: the fourth exit used to `return` here, `_run_creation`'s `except` swallowed it,
    and `fm create` exited 0 for a bench that was never built."""
    message = "mkdir exploded, unique-marker-9c3d1a"

    with (
        patch("frappe_manager.commands.app", side_effect=_typer_app_that_fails_before_the_bench_directory_exists(message)),
        pytest.raises(SystemExit) as exit_info,
    ):
        cli_entrypoint()

    assert exit_info.value.code != 0


def test_the_exception_text_reaches_the_operator_exactly_once(capsys: pytest.CaptureFixture[str]):
    """The regression this fix closes: before it, this same run printed the exception text once
    from `_handle_creation_failure` and a second time from `cli_entrypoint`."""
    message = "mkdir exploded, unique-marker-4f7e2b"

    with (
        patch("frappe_manager.commands.app", side_effect=_typer_app_that_fails_before_the_bench_directory_exists(message)),
        pytest.raises(SystemExit),
    ):
        cli_entrypoint()

    err = capsys.readouterr().err
    assert err.count(message) == 1, f"exception text must appear exactly once; full output:\n{err}"


def test_the_fm_log_guidance_still_appears(capsys: pytest.CaptureFixture[str]):
    """The create-specific "check the logs" advice is not a restatement of the exception, so it
    must survive dropping the in-method exception print."""
    message = "mkdir exploded, unique-marker-77aa01"

    with (
        patch("frappe_manager.commands.app", side_effect=_typer_app_that_fails_before_the_bench_directory_exists(message)),
        pytest.raises(SystemExit),
    ):
        cli_entrypoint()

    err = capsys.readouterr().err
    assert "check the logs" in err.lower()


def test_the_traceback_never_reaches_the_terminal(capsys: pytest.CaptureFixture[str]):
    """The traceback belongs in fm.log; a non-verbose run must not dump it to the terminal."""
    message = "mkdir exploded, unique-marker-1e908c"

    with (
        patch("frappe_manager.commands.app", side_effect=_typer_app_that_fails_before_the_bench_directory_exists(message)),
        pytest.raises(SystemExit),
    ):
        cli_entrypoint()

    err = capsys.readouterr().err
    assert "Traceback (most recent call last)" not in err


def test_an_unrelated_commands_error_output_is_unchanged(capsys: pytest.CaptureFixture[str]):
    """The regression risk of touching `main.py`: a command whose failure never goes through
    `_handle_creation_failure` must still get exactly one, unaltered, print of its error."""

    class _UnrelatedCommandError(FrappeManagerException):
        pass

    message = "unrelated command exploded, unique-marker-b81f4e"

    def _app(*_args, **_kwargs):
        raise _UnrelatedCommandError(message)

    with (
        patch("frappe_manager.commands.app", side_effect=_app),
        pytest.raises(SystemExit) as exit_info,
    ):
        cli_entrypoint()

    assert exit_info.value.code != 0
    err = capsys.readouterr().err
    assert err.count(message) == 1
    assert "Error Occurred" in err


def test_a_second_unrelated_command_error_path_is_also_unchanged(capsys: pytest.CaptureFixture[str]):
    """A different exception shape (a bare, non-`FrappeManagerException` crash) exercises
    `cli_entrypoint`'s OTHER except arm, the same one the create failure above now also reaches."""
    message = "unrelated crash, unique-marker-2d6c9a"

    def _app(*_args, **_kwargs):
        raise ValueError(message)

    with (
        patch("frappe_manager.commands.app", side_effect=_app),
        pytest.raises(SystemExit) as exit_info,
    ):
        cli_entrypoint()

    assert exit_info.value.code != 0
    err = capsys.readouterr().err
    assert err.count(message) == 1
    assert "Unexpected Error" in err
