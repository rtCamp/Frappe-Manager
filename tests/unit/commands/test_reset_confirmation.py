"""`fm reset` must ask before it destroys a site, and must talk about the SITE it destroys.

`Bench.reset` drops the site's schema and runs `bench reinstall --yes`: every doctype row,
every uploaded file and every customisation is gone, and there is no undo. Until now the
command had no confirmation and no `--yes`, so one mistyped bench name that happened to
resolve wiped a site with nothing between the Enter key and `reinstall`. Every other
destructive command in this CLI gates first (`fm delete`, `fm ssl remove`, the deploy
restore), so these tests pin `fm reset` to the same shape:

* the confirmation names the site and says what is lost,
* "no" (and the bare-Enter default, which IS "no") leaves the site untouched and exits 0,
* `--yes` is the scripted bypass and asks nothing,
* with no terminal and no `--yes` the command REFUSES. It must not hang waiting for an
  answer nobody can give, and it must not decide to proceed on its own.

The prompt is the real `prompt_ask` of the real global handler wherever the answer matters,
so the non-interactive refusal under test is the one production takes.

A bench now serves N sites, which turns two cosmetic details into wrong behaviour and both
are pinned here:

* Every message names the site the address picked, never the bench. A bench called `shop`
  serving `a.example.com` and `b.example.com` used to be warned about as 'shop' while one
  schema was about to be dropped, which describes a blast radius three times the real one.
* The external-database refusal is per SITE. `reinstall` drops and recreates a schema, so
  fm declines for the same reason `fm delete` declines to drop it: the schema is not fm's.
  On a mixed bench the global-db site still resets and only the external one is refused,
  and the refusal carries the site, the schema and the host so the operator can act.

`fm reset` deliberately gains NO new confirmation for the multi-site case. It destroys
exactly the site its address names, so the address is the acknowledgement; only `fm delete`,
which can take out sites the address does not name, asks for a typed name.
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

# A bench whose sites are not named after it, which is the shape that makes the bench name
# and the site name different strings.
MIXED_BENCH = "shop"
GLOBAL_DB_SITE = "a.example.com"
EXTERNAL_SITE = "b.example.com"
THIRD_SITE = "c.example.com"
EXTERNAL_SCHEMA = "_8f3a1c9d2b"
EXTERNAL_HOST = "db.internal.example.net"

# Read only when the command falls back to the bench's own site. Every test that names a site
# asserts this string does NOT reach the operator: on a real multi-site bench `site_name`
# raises rather than returning anything, so a fallback that fires is a crash, not a typo.
UNRESOLVED = "site-name-must-not-be-read"

MIXED_DATABASES = {EXTERNAL_SITE: SimpleNamespace(name=EXTERNAL_SCHEMA, host=EXTERNAL_HOST, port=3306)}


class _NoSpinner:
    """`with spinner(...)` without a Live region: the reset body is what is under test."""

    def __init__(self, *args, **kwargs):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def _bench(*, name=BENCH, own_site=BENCH, databases=None):
    """The bench `Bench.get_object` hands back.

    `external_database_config` is keyed on the site argument, because that keying IS the thing under
    test: a mock that ignores it would pass whether the command asked about the right site or not.
    """
    bench = MagicMock(name="Bench")
    # bench, site and domain used to be one string; a mock that sets only `name` hands a
    # MagicMock to any caller that correctly asks for the site or the domain.
    bench.name = name
    bench.site_name = own_site
    bench.primary_domain = own_site
    bench.domains = [own_site]
    bench.external_database_config.side_effect = lambda site=None: (databases or {}).get(site)
    return bench


def _run(
    *,
    yes=False,
    admin_pass=None,
    answer=None,
    interactive=True,
    bench_name=BENCH,
    own_site=BENCH,
    named_site=None,
    databases=None,
):
    """Call the real `reset` body. Returns what it did, what it asked, and in what order.

    `named_site` is what `bench_site_callback` leaves on `ctx.obj` for a `BENCH/SITE` address.
    """
    handler = get_global_output_handler()
    ctx = MagicMock()
    ctx.obj = {"services": MagicMock(), "verbose": False}
    if named_site is not None:
        ctx.obj["site"] = named_site

    bench = _bench(name=bench_name, own_site=own_site, databases=databases)

    events: list[str] = []

    real_prompt_ask = handler.prompt_ask

    def _warning(*args, **kwargs):
        events.append("warning")

    def _display_error(*args, **kwargs):
        events.append("refusal")

    def _prompt(*args, **kwargs):
        events.append("prompt")
        return answer if answer is not None else real_prompt_ask(*args, **kwargs)

    def _reset(*args, **kwargs):
        events.append("reset")

    bench.reset.side_effect = _reset

    previous = handler.is_interactive()
    handler.set_interactive_mode(not interactive)

    try:
        with (
            patch.object(reset_cmd, "Bench") as bench_cls,
            patch.object(reset_cmd, "check_bench_migration_required"),
            patch.object(reset_cmd, "spinner", _NoSpinner),
            patch.object(handler, "print"),
            patch.object(handler, "warning", side_effect=_warning) as warning,
            patch.object(handler, "display_error", side_effect=_display_error) as display_error,
            patch.object(handler, "prompt_ask", side_effect=_prompt) as prompt_ask,
        ):
            bench_cls.get_object.return_value = bench
            raised = None
            try:
                reset_cmd.reset(ctx, benchname=bench_name, yes=yes, admin_pass=admin_pass)
            except (typer.Exit, NonInteractiveError) as exc:
                raised = exc
    finally:
        handler.set_interactive_mode(not previous)

    return SimpleNamespace(
        raised=raised,
        bench=bench,
        prompt=prompt_ask,
        warning=warning,
        error=display_error,
        events=events,
    )


def _said(mock_call_recorder):
    """Everything one output method was handed, as one searchable string."""
    return " ".join(str(call.args) + str(call.kwargs) for call in mock_call_recorder.call_args_list)


# ------------------------------------------------------------------ the refusal


def test_declining_the_confirmation_resets_nothing():
    result = _run(answer="no")
    assert "reset" not in result.events


def test_declining_is_not_an_error():
    """A cancelled destroy is the operator changing their mind, not a failure."""
    result = _run(answer="no")
    assert isinstance(result.raised, typer.Exit)
    assert result.raised.exit_code == 0


def test_an_unrecognised_answer_is_not_taken_as_consent():
    """Only the literal "yes" proceeds: an empty or garbled answer must not destroy."""
    for answer in ("", "y", "YES", "no", "maybe"):
        result = _run(answer=answer)
        assert "reset" not in result.events, answer


def test_the_confirmation_default_is_no():
    """A bare Enter on the prompt keeps the site: the destructive answer is never the default."""
    result = _run(answer="no")
    assert result.prompt.call_args.kwargs["default"] == "no"


# ------------------------------------------------------------------- consenting


def test_confirming_resets_the_site_with_the_given_password():
    result = _run(answer="yes", admin_pass="s3cret")
    result.bench.reset.assert_called_once_with("s3cret", site=BENCH)


def test_yes_skips_the_confirmation_entirely():
    result = _run(yes=True)
    assert result.events == ["reset"]
    result.bench.reset.assert_called_once_with(None, site=BENCH)


# ------------------------------------------------------- what the operator reads


def test_the_confirmation_names_the_site_it_would_destroy():
    result = _run(answer="no")
    assert BENCH in result.prompt.call_args.kwargs["prompt"]


def test_the_operator_is_told_what_is_lost_before_being_asked():
    """Naming the site is not enough: `reset` sounds recoverable and is not."""
    result = _run(answer="no")
    warned = _said(result.warning)
    assert "reinstall" in warned
    assert "no undo" in warned
    assert result.events.index("warning") < result.events.index("prompt")


# ------------------------------------------------------------- which site dies


def test_the_warning_names_the_site_and_not_the_bench():
    """A bench serving three sites, warned about as 'shop', describes a blast radius three
    times the real one. `reinstall` takes out one schema, so one site is what is named."""
    result = _run(
        answer="no",
        bench_name=MIXED_BENCH,
        own_site=UNRESOLVED,
        named_site=GLOBAL_DB_SITE,
        databases=MIXED_DATABASES,
    )
    warned = _said(result.warning)
    assert GLOBAL_DB_SITE in warned
    assert MIXED_BENCH not in warned
    assert UNRESOLVED not in warned


def test_the_prompt_names_the_site_and_not_the_bench():
    result = _run(
        answer="no",
        bench_name=MIXED_BENCH,
        own_site=UNRESOLVED,
        named_site=GLOBAL_DB_SITE,
        databases=MIXED_DATABASES,
    )
    asked = result.prompt.call_args.kwargs["prompt"]
    assert GLOBAL_DB_SITE in asked
    assert MIXED_BENCH not in asked
    assert UNRESOLVED not in asked


def test_the_reinstall_targets_the_site_the_operator_was_asked_about():
    """The name in the question and the name handed to the engine are one value, so consent
    cannot be collected for one site and spent on another."""
    result = _run(
        answer="yes",
        bench_name=MIXED_BENCH,
        own_site=UNRESOLVED,
        named_site=THIRD_SITE,
        databases=MIXED_DATABASES,
    )
    asked = result.prompt.call_args.kwargs["prompt"]
    reset_site = result.bench.reset.call_args.kwargs["site"]
    assert reset_site == THIRD_SITE
    assert reset_site in asked


def test_an_address_with_no_site_part_falls_back_to_the_benchs_own_site():
    """`fm reset shop` still works and still names a site: the bench's own one, which is a
    different string from the bench whenever the bench is not named after its site."""
    result = _run(answer="no", bench_name=MIXED_BENCH, own_site=GLOBAL_DB_SITE)
    assert GLOBAL_DB_SITE in _said(result.warning)
    assert GLOBAL_DB_SITE in result.prompt.call_args.kwargs["prompt"]


# ------------------------------------------------- the external-database refusal


def test_a_global_db_site_on_a_mixed_bench_resets():
    """One bench, two sites, two database servers. The site fm owns the schema of is not
    punished for its neighbour."""
    result = _run(
        yes=True,
        bench_name=MIXED_BENCH,
        own_site=UNRESOLVED,
        named_site=GLOBAL_DB_SITE,
        databases=MIXED_DATABASES,
    )
    assert result.events == ["reset"]
    result.bench.reset.assert_called_once_with(None, site=GLOBAL_DB_SITE)


def test_the_external_database_site_on_the_same_bench_is_refused():
    result = _run(
        yes=True,
        bench_name=MIXED_BENCH,
        own_site=UNRESOLVED,
        named_site=EXTERNAL_SITE,
        databases=MIXED_DATABASES,
    )
    assert result.events == ["refusal"]
    assert isinstance(result.raised, typer.Exit)
    assert result.raised.exit_code == 1


def test_the_refusal_names_the_site_the_schema_and_the_host():
    """A refusal an operator cannot act on is just a wall: fm will not touch this schema, so
    the message has to say which site, which schema and which server to go and do it on."""
    result = _run(
        yes=True,
        bench_name=MIXED_BENCH,
        own_site=UNRESOLVED,
        named_site=EXTERNAL_SITE,
        databases=MIXED_DATABASES,
    )
    refusal = _said(result.error)
    assert EXTERNAL_SITE in refusal
    assert EXTERNAL_SCHEMA in refusal
    assert EXTERNAL_HOST in refusal


def test_the_refusal_lands_before_the_operator_is_asked_anything():
    """Consent for something fm is going to decline anyway is consent wasted, and a "yes"
    on the record for a destroy that never happened."""
    result = _run(
        answer="yes",
        bench_name=MIXED_BENCH,
        own_site=UNRESOLVED,
        named_site=EXTERNAL_SITE,
        databases=MIXED_DATABASES,
    )
    assert result.events == ["refusal"]


def test_yes_does_not_buy_past_the_external_database():
    """--yes skips the question, not the rule."""
    result = _run(
        yes=True,
        bench_name=MIXED_BENCH,
        own_site=UNRESOLVED,
        named_site=EXTERNAL_SITE,
        databases=MIXED_DATABASES,
    )
    assert "reset" not in result.events


def test_the_bench_own_site_is_refused_when_it_is_the_external_one():
    """The bare-`BENCH` address goes through the same gate: the fallback resolves a site, and
    it is that site's database entry that decides."""
    result = _run(
        yes=True,
        bench_name=MIXED_BENCH,
        own_site=EXTERNAL_SITE,
        databases=MIXED_DATABASES,
    )
    assert result.events == ["refusal"]
    assert EXTERNAL_HOST in _said(result.error)


# --------------------------------------------------------------- no terminal


def test_no_terminal_and_no_yes_refuses_instead_of_resetting():
    """The real prompt_ask: with --non-interactive (or no TTY) it raises rather than
    returning a default, so the site survives a scripted invocation that forgot --yes."""
    result = _run(interactive=False)
    assert isinstance(result.raised, NonInteractiveError)
    assert "reset" not in result.events


def test_the_refusal_names_the_flag_that_unblocks_it():
    """A refusal an operator cannot act on is just a wall. NonInteractiveError folds its
    suggestions into str()."""
    result = _run(interactive=False)
    assert "--yes" in str(result.raised)


def test_yes_still_works_without_a_terminal():
    """--yes is what makes the scripted case possible; it must not need a TTY."""
    result = _run(yes=True, interactive=False)
    assert result.raised is None
    result.bench.reset.assert_called_once_with(None, site=BENCH)


# ------------------------------------------------------------------ flag surface


def _cli_app():
    app = typer.Typer()
    app.command("reset")(reset_cmd.reset)
    return app


def test_both_spellings_of_the_bypass_parse(tmp_path):
    """`fm delete` takes `--yes` and `-y`; reset accepts the same two spellings, so a
    script written for one command does not silently fail on the other."""
    from typer.testing import CliRunner

    benches = tmp_path / "sites"
    (benches / BENCH).mkdir(parents=True)

    runner = CliRunner()

    for flag in ("--yes", "-y"):
        bench = _bench()
        with (
            patch.object(reset_cmd, "Bench") as bench_cls,
            patch.object(reset_cmd, "check_bench_migration_required"),
            patch.object(reset_cmd, "spinner", _NoSpinner),
            patch("frappe_manager.utils.callbacks.CLI_BENCHES_DIRECTORY", benches),
        ):
            bench_cls.get_object.return_value = bench
            result = runner.invoke(_cli_app(), [BENCH, flag], obj={"services": MagicMock(), "verbose": False})
        assert result.exit_code == 0, (flag, result.output)
        bench.reset.assert_called_once_with(None, site=BENCH)


def test_the_address_accepts_a_bench_slash_site_and_resets_that_site(tmp_path):
    """The whole surface, through the real address callback: `fm reset shop/a.example.com`
    parses, the site part survives on `ctx.obj`, and it is that site that gets reinstalled."""
    from typer.testing import CliRunner

    benches = tmp_path / "sites"
    (benches / MIXED_BENCH).mkdir(parents=True)

    bench = _bench(name=MIXED_BENCH, own_site=UNRESOLVED, databases=MIXED_DATABASES)

    with (
        patch.object(reset_cmd, "Bench") as bench_cls,
        patch.object(reset_cmd, "check_bench_migration_required"),
        patch.object(reset_cmd, "spinner", _NoSpinner),
        patch("frappe_manager.utils.callbacks.CLI_BENCHES_DIRECTORY", benches),
        patch(
            "frappe_manager.utils.callbacks._recorded_sites",
            return_value=[GLOBAL_DB_SITE, EXTERNAL_SITE],
        ),
    ):
        bench_cls.get_object.return_value = bench
        result = CliRunner().invoke(
            _cli_app(),
            [f"{MIXED_BENCH}/{GLOBAL_DB_SITE}", "--yes"],
            obj={"services": MagicMock(), "verbose": False},
        )

    assert result.exit_code == 0, result.output
    bench.reset.assert_called_once_with(None, site=GLOBAL_DB_SITE)


def test_the_address_refuses_a_site_the_bench_does_not_serve(tmp_path):
    """Guarding the wrong site is worse than not guarding: the address is validated against
    the bench's recorded sites before anything destructive is described."""
    from typer.testing import CliRunner

    benches = tmp_path / "sites"
    (benches / MIXED_BENCH).mkdir(parents=True)

    bench = _bench(name=MIXED_BENCH, own_site=UNRESOLVED, databases=MIXED_DATABASES)

    with (
        patch.object(reset_cmd, "Bench") as bench_cls,
        patch.object(reset_cmd, "check_bench_migration_required"),
        patch.object(reset_cmd, "spinner", _NoSpinner),
        patch("frappe_manager.utils.callbacks.CLI_BENCHES_DIRECTORY", benches),
        patch(
            "frappe_manager.utils.callbacks._recorded_sites",
            return_value=[GLOBAL_DB_SITE, EXTERNAL_SITE],
        ),
    ):
        bench_cls.get_object.return_value = bench
        result = CliRunner().invoke(
            _cli_app(),
            [f"{MIXED_BENCH}/nope.example.com", "--yes"],
            obj={"services": MagicMock(), "verbose": False},
        )

    assert result.exit_code != 0
    bench.reset.assert_not_called()


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
