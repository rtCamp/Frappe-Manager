"""`fm reset` must ask before it destroys a site.

`Bench.reset` drops the site's schema and runs `bench reinstall --yes`: every doctype row,
every uploaded file and every customisation is gone, and there is no undo. Until now the
command had no confirmation and no `--yes`, so one mistyped bench name that happened to
resolve wiped a site with nothing between the Enter key and `reinstall`. Every other
destructive command in this CLI gates first (`fm delete`, `fm ssl remove`, the deploy
restore), so these tests pin `fm reset` to the same shape:

* the confirmation names the bench and says what is lost,
* "no" (and the bare-Enter default, which IS "no") leaves the bench untouched and exits 0,
* `--yes` is the scripted bypass and asks nothing,
* with no terminal and no `--yes` the command REFUSES. It must not hang waiting for an
  answer nobody can give, and it must not decide to proceed on its own.

The prompt is the real `prompt_ask` of the real global handler wherever the answer matters,
so the non-interactive refusal under test is the one production takes.
"""

import inspect
from importlib import import_module
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
import typer

from frappe_manager.exceptions import NonInteractiveError
from frappe_manager.output_manager import get_global_output_handler

# `frappe_manager.commands` re-exports the `reset` FUNCTION under the same name, shadowing
# the module, so the module has to be imported explicitly to patch its globals.
reset_cmd = import_module("frappe_manager.commands.reset")

BENCH = "mybench.localhost"


class _NoSpinner:
    """`with spinner(...)` without a Live region: the reset body is what is under test."""

    def __init__(self, *args, **kwargs):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def _run(*, yes=False, admin_pass=None, answer=None, interactive=True):
    """Call the real `reset` body. Returns what it did and what it asked."""
    handler = get_global_output_handler()
    ctx = MagicMock()
    ctx.obj = {"services": MagicMock(), "verbose": False}

    bench = MagicMock(name="Bench")
    bench.name = BENCH

    previous = handler.is_interactive()
    handler.set_interactive_mode(not interactive)

    prompt_patch = (
        patch.object(handler, "prompt_ask", return_value=answer)
        if answer is not None
        else patch.object(handler, "prompt_ask", wraps=handler.prompt_ask)
    )

    try:
        with (
            patch.object(reset_cmd, "Bench") as bench_cls,
            patch.object(reset_cmd, "check_bench_migration_required"),
            patch.object(reset_cmd, "spinner", _NoSpinner),
            patch.object(handler, "print"),
            patch.object(handler, "warning") as warning,
            prompt_patch as prompt_ask,
        ):
            bench_cls.get_object.return_value = bench
            raised = None
            try:
                reset_cmd.reset(ctx, benchname=BENCH, yes=yes, admin_pass=admin_pass)
            except (typer.Exit, NonInteractiveError) as exc:
                raised = exc
    finally:
        handler.set_interactive_mode(not previous)

    return SimpleNamespace(raised=raised, bench=bench, prompt=prompt_ask, warning=warning)


# ------------------------------------------------------------------ the refusal


def test_declining_the_confirmation_resets_nothing():
    result = _run(answer="no")
    result.bench.reset.assert_not_called()


def test_declining_is_not_an_error():
    """A cancelled destroy is the operator changing their mind, not a failure."""
    result = _run(answer="no")
    assert isinstance(result.raised, typer.Exit)
    assert result.raised.exit_code == 0


def test_an_unrecognised_answer_is_not_taken_as_consent():
    """Only the literal "yes" proceeds: an empty or garbled answer must not destroy."""
    for answer in ("", "y", "YES", "maybe"):
        result = _run(answer=answer)
        assert result.bench.reset.assert_not_called() is None, answer


def test_the_confirmation_default_is_no():
    """A bare Enter on the prompt keeps the site: the destructive answer is never the default."""
    result = _run(answer="no")
    assert result.prompt.call_args.kwargs["default"] == "no"


# ------------------------------------------------------------------- consenting


def test_confirming_resets_the_bench_with_the_given_password():
    result = _run(answer="yes", admin_pass="s3cret")
    result.bench.reset.assert_called_once_with("s3cret")


def test_yes_skips_the_confirmation_entirely():
    result = _run(yes=True)
    result.prompt.assert_not_called()
    result.bench.reset.assert_called_once_with(None)


# ------------------------------------------------------- what the operator reads


def test_the_confirmation_names_the_bench_it_would_destroy():
    result = _run(answer="no")
    assert BENCH in result.prompt.call_args.kwargs["prompt"]


def test_the_operator_is_told_what_is_lost_before_being_asked():
    """Naming the bench is not enough: `reset` sounds recoverable and is not."""
    result = _run(answer="no")
    warned = " ".join(str(c.args) for c in result.warning.call_args_list)
    assert BENCH in warned
    assert "drops its database" in warned
    assert "no undo" in warned


# --------------------------------------------------------------- no terminal


def test_no_terminal_and_no_yes_refuses_instead_of_resetting():
    """The real prompt_ask: with --non-interactive (or no TTY) it raises rather than
    returning a default, so the bench survives a scripted invocation that forgot --yes."""
    result = _run(interactive=False)
    assert isinstance(result.raised, NonInteractiveError)
    result.bench.reset.assert_not_called()


def test_the_refusal_names_the_flag_that_unblocks_it():
    """A refusal an operator cannot act on is just a wall. NonInteractiveError folds its
    suggestions into the message, which is what the CLI prints."""
    result = _run(interactive=False)
    assert "--yes" in str(result.raised)


def test_yes_still_works_without_a_terminal():
    """--yes is what makes the scripted case possible; it must not need a TTY."""
    result = _run(yes=True, interactive=False)
    assert result.raised is None
    result.bench.reset.assert_called_once_with(None)


# ------------------------------------------------------------------ flag surface


def test_both_spellings_of_the_bypass_parse(tmp_path):
    """`fm delete` takes `--yes` and `-y`; reset accepts the same two spellings, so a
    script written for one command does not silently fail on the other."""
    from typer.testing import CliRunner

    benches = tmp_path / "sites"
    (benches / BENCH).mkdir(parents=True)

    app = typer.Typer()
    app.command("reset")(reset_cmd.reset)
    runner = CliRunner()

    for flag in ("--yes", "-y"):
        bench = MagicMock(name="Bench")
        bench.name = BENCH
        with (
            patch.object(reset_cmd, "Bench") as bench_cls,
            patch.object(reset_cmd, "check_bench_migration_required"),
            patch.object(reset_cmd, "spinner", _NoSpinner),
            patch("frappe_manager.utils.callbacks.CLI_BENCHES_DIRECTORY", benches),
        ):
            bench_cls.get_object.return_value = bench
            result = runner.invoke(app, [BENCH, flag], obj={"services": MagicMock(), "verbose": False})
        assert result.exit_code == 0, (flag, result.output)
        bench.reset.assert_called_once_with(None)


def test_the_admin_pass_help_names_every_place_the_password_is_read_from():
    """`Bench.reset` consults site_config.json, THEN common_site_config.json, and only then
    prompts. The help used to skip the middle one, so an operator who set the password
    globally was told fm would ask."""
    option = inspect.signature(reset_cmd.reset).parameters["admin_pass"].annotation.__metadata__[0]
    assert "site_config.json" in option.help
    assert "common_site_config.json" in option.help


@pytest.mark.parametrize("yes", [True, False])
def test_the_migration_gate_runs_before_anything_else(yes):
    """A bench that needs `fm migrate` must not be reset by either path."""
    handler = get_global_output_handler()
    ctx = MagicMock()
    ctx.obj = {"services": MagicMock(), "verbose": False}
    with (
        patch.object(reset_cmd, "Bench") as bench_cls,
        patch.object(reset_cmd, "check_bench_migration_required", side_effect=typer.Exit(0)),
        patch.object(handler, "print"),
        pytest.raises(typer.Exit),
    ):
        reset_cmd.reset(ctx, benchname=BENCH, yes=yes)
    bench_cls.get_object.assert_not_called()
