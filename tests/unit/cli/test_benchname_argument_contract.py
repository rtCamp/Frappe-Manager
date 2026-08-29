"""The `benchname` positional argument is one contract copy-pasted 25 times.

Twelve commands declare a *byte-identical* `benchname` argument (same help, same
default, same autocompletion, same validation callback); thirteen more declare a
deliberately different one. Someone will eventually collapse the identical dozen
into a single shared `Annotated` alias -- and that refactor silently changes the
CLI if it sweeps in a command whose help text, default, required-ness,
autocompletion or callback differs.

Contract defended here, against the LIVE typer app (never against source text):
every `benchname` argument is a positional ARGUMENT, and its full observable spec
is either the canonical one or an explicitly enumerated exception. A new command
that copies the canonical block passes for free; one that deviates fails and
names itself, forcing a decision instead of a silent CLI change.

Also pinned: `sitename_callback` and `sites_autocompletion_callback`, the two
shared callables the canonical spec points at. They are what makes the alias
load-bearing rather than cosmetic, and they carry the bench-name normalisation
(bare name -> `.localhost`) that users depend on.
"""

import inspect
from dataclasses import dataclass
from pathlib import Path
from unittest import mock

import click
import pytest
import typer

from frappe_manager.commands import app
from frappe_manager.commands.maintenance import _maintenance_sitename_callback
from frappe_manager.exceptions import FrappeManagerException, NonInteractiveError
from frappe_manager.output_manager import get_global_output_handler
from frappe_manager.site_manager.exceptions import BenchException, BenchNotFoundError
from frappe_manager.utils import callbacks
from frappe_manager.utils import site as site_utils
from frappe_manager.utils.callbacks import (
    bench_site_callback,
    sitename_callback,
    sites_autocompletion_callback,
    standalone_address_callback,
)

PARAM_NAME = "benchname"


@dataclass(frozen=True)
class BenchnameSpec:
    """Everything about a `benchname` argument that a user or shell can observe."""

    help: str
    default: object
    required: bool
    type_name: str
    autocompletion: object
    callback: object


# The block repeated verbatim across 12 command modules -- the dedup target.
CANONICAL = BenchnameSpec(
    help="Name of the bench.",
    default=None,
    required=False,
    type_name="text",
    autocompletion=sites_autocompletion_callback,
    callback=sitename_callback,
)

# Commands known to carry the canonical block today. Asserted as a subset of the
# live app, so adding a 13th canonical command does not break this file.
KNOWN_CANONICAL = frozenset(
    {
        "fm auth",
        "fm code",
        "fm delete",
        "fm info",
        "fm logs",
        "fm ngrok",
        "fm reset",
        "fm restart",
        "fm start",
        "fm stop",
        "fm update",
    }
)

# Commands whose `benchname` legitimately differs today. Each entry is a real
# difference a dedup refactor must either preserve or change on purpose -- NOT a
# list of things that are allowed to drift. The spec is pinned exactly.
EXCEPTIONS: dict[str, BenchnameSpec] = {
    # `create` makes a NEW bench, so completing over existing benches would be
    # actively wrong, and `sitename_callback` (which requires the bench to exist)
    # would reject every valid input. Required, bare `str`, and its own callback:
    # `create_command_sitename_callback` normalises the name and then refuses one
    # whose bench directory already exists. It used to carry NO callback at all,
    # which is what let `fm create existing.localhost` overwrite a live bench --
    # nothing else on the create path checks, and `--allow-domain-conflicts` turns
    # the only other gate off. The absent callback was the bug, not the contract.
    "fm create": BenchnameSpec(
        help="Bench name, also its domain. A bare name becomes mybench.localhost.",
        default=None,
        required=True,
        type_name="text",
        autocompletion=None,
        callback=callbacks.create_command_sitename_callback,
    ),
    # `maintenance --status` may run bench-less, so it swaps in a wrapper that
    # lets `None` through for that one flag and otherwise delegates.
    "fm maintenance": BenchnameSpec(
        help="Name of the bench. Optional with --status, which then lists every domain in maintenance.",
        default=None,
        required=False,
        type_name="text",
        autocompletion=sites_autocompletion_callback,
        callback=_maintenance_sitename_callback,
    ),
    # `migrate` runs BEFORE benches are known-good (and supports --all-benches),
    # so it deliberately has neither completion nor validation.
    "fm migrate": BenchnameSpec(
        help="Bench name to migrate",
        default=None,
        required=False,
        type_name="text",
        autocompletion=None,
        callback=None,
    ),
    # `bake` supports a standalone mode driven by --apps/--config, so it keeps
    # completion but drops the must-exist callback.
    "fm bake": BenchnameSpec(
        help="Bench to bake. Omit for a standalone bake driven by --apps/--config.",
        default=None,
        required=False,
        type_name="text",
        autocompletion=sites_autocompletion_callback,
        callback=None,
    ),
    # `switch` / `prune`: canonical help and canonical callback, but REQUIRED.
    # These are the dangerous ones -- identical help text, different arity. A
    # dedup that reuses the canonical alias here would make the argument optional
    # and silently trigger the interactive bench picker.
    "fm switch": BenchnameSpec(
        help="Name of the bench.",
        default=None,
        required=True,
        type_name="text",
        autocompletion=sites_autocompletion_callback,
        callback=sitename_callback,
    ),
    "fm prune": BenchnameSpec(
        help="Name of the bench.",
        default=None,
        required=True,
        type_name="text",
        autocompletion=sites_autocompletion_callback,
        callback=sitename_callback,
    ),
    # `self compose` is a maintenance escape hatch: required, no completion, no callback.
    "fm self compose": BenchnameSpec(
        help="Name of the bench.",
        default=None,
        required=True,
        type_name="text",
        autocompletion=None,
        callback=None,
    ),
    # The four `ssl` subcommands all support standalone (bench-less) certificates, so they
    # share their own variant: completion yes, must-exist validation no. They DO carry
    # `standalone_address_callback`, which parses the `BENCH[/SITE]` address and refuses a
    # site part without normalising the name or requiring the bench to exist. Before it,
    # a slashed value reached `Bench.get_object` and died as a not-found error on a nested
    # path rather than as a parse error.
    **{
        f"fm ssl {sub}": BenchnameSpec(
            help="Name of the bench (omit for standalone mode).",
            default=None,
            required=False,
            type_name="text",
            autocompletion=sites_autocompletion_callback,
            callback=standalone_address_callback,
        )
        for sub in ("add", "list", "remove", "renew")
    },
    # `shell` is the one command that addresses a SITE, because a shell is the one place a
    # site is addressable today: the site half of the address becomes FRAPPE_SITE in the
    # container, which Frappe reads above common_site_config's default_site, so bare
    # `bench` commands inside the shell target it. Hence its own help text and its own
    # callback, `bench_site_callback`, the only one that accepts a site part.
    "fm shell": BenchnameSpec(
        help="Bench, or bench/site.",
        default=None,
        required=False,
        type_name="text",
        autocompletion=sites_autocompletion_callback,
        callback=bench_site_callback,
    ),
    # dns-config credentials can be global, hence its own wording.
    "fm ssl dns-config cloudflare": BenchnameSpec(
        help="Bench to configure. Omit for global credentials.",
        default=None,
        required=False,
        type_name="text",
        autocompletion=sites_autocompletion_callback,
        callback=None,
    ),
}


def iter_click_commands(command: click.Command, prefix: str = "fm"):
    """(qualified name, click command) for the whole built CLI tree."""
    yield prefix, command
    if isinstance(command, click.Group):
        for name, sub in command.commands.items():
            yield from iter_click_commands(sub, f"{prefix} {name}")


def unwrap_autocompletion(param: click.Parameter):
    """The user-supplied `autocompletion=` callable, or None if there was none.

    Typer does not keep the callable on the param. It builds two nested shims --
    an arity-adapting `completion_wrapper` (carrying `__wrapped__`) inside a
    `compat_autocompletion` closure stored on `_custom_shell_complete`. Peel both
    to recover the identity the CLI was declared with.
    """
    wrapper = getattr(param, "_custom_shell_complete", None)
    if wrapper is None:
        return None
    captured = [cell.cell_contents for cell in (wrapper.__closure__ or ()) if callable(cell.cell_contents)]
    return inspect.unwrap(captured[0]) if len(captured) == 1 else wrapper


def unwrap_callback(param: click.Parameter):
    """The user-supplied `callback=`, unwrapped from typer's `update_wrapper` shim."""
    return inspect.unwrap(param.callback) if param.callback else None


def spec_of(param: click.Parameter) -> BenchnameSpec:
    return BenchnameSpec(
        help=getattr(param, "help", None),
        default=param.default,
        required=param.required,
        type_name=param.type.name,
        autocompletion=unwrap_autocompletion(param),
        callback=unwrap_callback(param),
    )


def spec_diff(expected: BenchnameSpec, actual: BenchnameSpec) -> dict[str, str]:
    """Only the fields that differ, rendered short so pytest never truncates the
    command name out of the failure message."""
    return {
        field: f"expected {_render(getattr(expected, field))!s}, got {_render(getattr(actual, field))!s}"
        for field in expected.__dataclass_fields__
        if getattr(expected, field) != getattr(actual, field)
    }


def _render(value) -> str:
    return getattr(value, "__name__", None) or repr(value)


def discover_benchname_arguments() -> dict[str, click.Parameter]:
    """Every positional `benchname` argument in the live app, keyed by command path.

    Built from `typer.main.get_command(app)` so it tracks the CLI that actually
    ships, and picks up new commands automatically.
    """
    found = {}
    for name, command in iter_click_commands(typer.main.get_command(app)):
        for param in command.params:
            if param.name == PARAM_NAME and isinstance(param, click.Argument):
                found[name] = param
    return found


BENCHNAME_ARGUMENTS = discover_benchname_arguments()


# --------------------------------------------------------------------------- #
# The inventory
# --------------------------------------------------------------------------- #


def test_the_app_exposes_benchname_arguments():
    # If discovery breaks (typer internals move, app import changes shape) every
    # other test here degrades to a silent no-op. Fail loudly instead.
    assert BENCHNAME_ARGUMENTS, "no benchname arguments discovered -- discovery is broken, not the CLI"
    assert len(BENCHNAME_ARGUMENTS) >= len(KNOWN_CANONICAL) + len(EXCEPTIONS)


def test_known_commands_still_take_a_benchname_argument():
    missing = sorted((KNOWN_CANONICAL | EXCEPTIONS.keys()) - BENCHNAME_ARGUMENTS.keys())
    assert not missing, f"commands lost their benchname argument (or were renamed): {missing}"


def test_no_benchname_is_declared_as_an_option():
    # `--benchname foo` was never the interface; keep it positional.
    wrong = [
        name
        for name, param in BENCHNAME_ARGUMENTS.items()
        if isinstance(param, click.Option) or param.param_type_name != "argument" or param.opts != [PARAM_NAME]
    ]
    assert not wrong, f"benchname is not a bare positional argument in: {wrong}"


def test_every_benchname_takes_exactly_one_value_and_is_visible():
    odd = {
        name: (param.nargs, param.expose_value, param.is_eager, getattr(param, "hidden", False))
        for name, param in BENCHNAME_ARGUMENTS.items()
        if (param.nargs, param.expose_value, param.is_eager, getattr(param, "hidden", False)) != (1, True, False, False)
    }
    assert not odd, f"benchname arity/visibility differs in: {odd}"


# --------------------------------------------------------------------------- #
# The canonical dozen -- the actual dedup target
# --------------------------------------------------------------------------- #


def test_known_canonical_commands_share_one_identical_spec():
    deviations = {
        name: diff
        for name in sorted(KNOWN_CANONICAL)
        if (diff := spec_diff(CANONICAL, spec_of(BENCHNAME_ARGUMENTS[name])))
    }
    assert not deviations, (
        "these commands used to declare an IDENTICAL benchname argument and no longer do; "
        f"a shared Annotated alias would change their CLI: {deviations}"
    )


def test_unlisted_commands_match_the_canonical_spec():
    # Any benchname argument that is neither a documented exception nor canonical
    # is an unclassified third variant: classify it or make it canonical.
    unclassified = {
        name: diff
        for name, param in sorted(BENCHNAME_ARGUMENTS.items())
        if name not in EXCEPTIONS and (diff := spec_diff(CANONICAL, spec_of(param)))
    }
    assert not unclassified, (
        "benchname argument is neither canonical nor a documented exception -- add it to "
        f"EXCEPTIONS with a reason, or make it canonical: {unclassified}"
    )


def test_documented_exceptions_differ_exactly_as_pinned():
    drifted = {
        name: diff
        for name, expected in sorted(EXCEPTIONS.items())
        if (diff := spec_diff(expected, spec_of(BENCHNAME_ARGUMENTS[name])))
    }
    assert not drifted, f"documented benchname exception changed shape: {drifted}"


def test_shared_callables_are_the_same_object_everywhere():
    # A dedup refactor is only safe because these are one function, not per-module
    # copies. Identity, not equality.
    callbacks_seen = {name: spec_of(p).callback for name, p in BENCHNAME_ARGUMENTS.items()}
    validators = {name for name, cb in callbacks_seen.items() if cb is sitename_callback}
    assert validators >= KNOWN_CANONICAL
    # Nothing pretends to be sitename_callback while being a different object.
    impostors = {
        name: cb
        for name, cb in callbacks_seen.items()
        if cb is not None and cb is not sitename_callback and getattr(cb, "__name__", "") == "sitename_callback"
    }
    assert not impostors, f"a second sitename_callback object is in play: {impostors}"

    completers = {spec_of(p).autocompletion for p in BENCHNAME_ARGUMENTS.values()} - {None}
    assert completers == {sites_autocompletion_callback}


def test_commands_without_any_benchname_callback_are_only_the_documented_ones():
    # Dropping the callback entirely removes validation *and* the interactive picker --
    # the most user-visible thing a careless dedup can do.
    unvalidated = {name for name, param in BENCHNAME_ARGUMENTS.items() if unwrap_callback(param) is None}
    expected = {name for name, spec in EXCEPTIONS.items() if spec.callback is None}
    assert unvalidated == expected, (
        f"set of commands with NO benchname callback changed: "
        f"gained {sorted(unvalidated - expected)}, lost {sorted(expected - unvalidated)}"
    )


def test_commands_that_skip_the_must_exist_check_are_only_the_documented_ones():
    """The narrower contract the test above stopped covering once the `ssl` commands gained
    `standalone_address_callback`: they have a callback now, but it deliberately does NOT
    require the bench to exist, because they also manage domains belonging to no bench.

    Only `sitename_callback` and `bench_site_callback` run the must-exist check (both go
    through `_resolve_bench`). A command that silently moves off one of those loses the
    check and the picker, which is exactly what this pins.
    """
    must_exist = {sitename_callback, bench_site_callback}
    skipping = {name for name, param in BENCHNAME_ARGUMENTS.items() if unwrap_callback(param) not in must_exist}
    expected = {
        "fm bake",
        "fm create",
        "fm maintenance",
        "fm migrate",
        "fm self compose",
        "fm ssl add",
        "fm ssl dns-config cloudflare",
        "fm ssl list",
        "fm ssl remove",
        "fm ssl renew",
    }
    assert skipping == expected, (
        f"set of commands skipping the benchname must-exist check changed: "
        f"gained {sorted(skipping - expected)}, lost {sorted(expected - skipping)}"
    )


def test_required_benchname_commands_are_only_the_documented_ones():
    required = {name for name, param in BENCHNAME_ARGUMENTS.items() if param.required}
    expected = {name for name, spec in EXCEPTIONS.items() if spec.required}
    assert required == expected, (
        f"set of commands REQUIRING benchname changed: "
        f"gained {sorted(required - expected)}, lost {sorted(expected - required)}"
    )


# --------------------------------------------------------------------------- #
# sitename_callback -- the shared validator the canonical spec points at
# --------------------------------------------------------------------------- #


@pytest.fixture
def benches(tmp_path, monkeypatch):
    """An isolated benches directory + cache; nothing here touches ~/frappe."""
    benches_dir = tmp_path / "sites"
    benches_dir.mkdir()
    cache_dir = tmp_path / "cache"
    monkeypatch.setattr(callbacks, "CLI_BENCHES_DIRECTORY", benches_dir)
    monkeypatch.setattr(site_utils, "CLI_BENCHES_DIRECTORY", benches_dir)
    monkeypatch.setattr(callbacks, "CLI_CACHE_PATH", cache_dir)
    monkeypatch.setattr(callbacks, "CLI_RECENT_USED_SITES_CACHE_PATH", cache_dir / "recent_sites.json")
    # cwd must not be inside the fake benches dir, or the "no value" path
    # short-circuits via get_sitename_from_current_path().
    monkeypatch.chdir(tmp_path)
    return benches_dir


def make_bench(benches_dir: Path, name: str) -> Path:
    """A directory that `sites_autocompletion_callback` counts as a bench."""
    bench = benches_dir / name
    bench.mkdir()
    (bench / "docker-compose.yml").write_text("services: {}\n")
    return bench


class TestSitenameCallbackNormalisation:
    """Bare names get `.localhost` appended; anything already qualified is kept."""

    def test_bare_name_gains_the_localhost_suffix(self, benches):
        make_bench(benches, "hello.localhost")
        assert sitename_callback("hello") == "hello.localhost"

    def test_name_already_ending_in_localhost_is_returned_unchanged(self, benches):
        make_bench(benches, "hello.localhost")
        assert sitename_callback("hello.localhost") == "hello.localhost"

    def test_multi_level_domain_is_returned_unchanged(self, benches):
        make_bench(benches, "shop.example.com")
        assert sitename_callback("shop.example.com") == "shop.example.com"

    def test_suffix_is_appended_before_the_existence_check(self, benches):
        # A bare name resolves against the SUFFIXED directory, not the bare one.
        make_bench(benches, "hello")  # deliberately unsuffixed
        with pytest.raises(BenchNotFoundError):
            sitename_callback("hello")


class TestSitenameCallbackRejection:
    def test_unknown_bench_raises_bench_not_found(self, benches):
        make_bench(benches, "other.localhost")
        with pytest.raises(BenchNotFoundError) as excinfo:
            sitename_callback("nope.localhost")
        assert "nope.localhost" in str(excinfo.value)

    @pytest.mark.parametrize("bad", ["bad name", "_leading.localhost", "has_underscore"])
    def test_non_fqdn_name_raises_bench_exception(self, benches, bad):
        # validate_sitename routes through output.error(), which RAISES.
        # Note: it appends `.localhost` to a bare name *before* complaining, so
        # the message names the suffixed value. Pinned as-is.
        with pytest.raises(BenchException):
            sitename_callback(bad)

    def test_fqdn_check_happens_before_the_existence_check(self, benches):
        # An invalid name never reaches BenchNotFoundError, even though its
        # directory is absent too.
        with pytest.raises(BenchException):
            sitename_callback("bad name.localhost")


class TestSitenameCallbackWithNoValue:
    """`None`/`""` fall back to cwd, then to an interactive picker."""

    @pytest.mark.parametrize("empty", [None, ""])
    def test_no_value_and_no_benches_raises_bad_parameter(self, benches, empty):
        with pytest.raises(typer.BadParameter, match=r"Invalid selection\. Must match existing sites"):
            sitename_callback(empty)

    def test_no_value_inside_a_bench_directory_uses_that_bench(self, benches, monkeypatch):
        bench = make_bench(benches, "cwd.localhost")
        monkeypatch.chdir(bench)
        assert sitename_callback(None) == "cwd.localhost"

    def test_no_value_with_benches_available_prompts_and_returns_the_choice(self, benches):
        make_bench(benches, "picked.localhost")
        make_bench(benches, "other.localhost")
        handler = get_global_output_handler()
        with mock.patch.object(handler, "prompt_fuzzy", return_value="picked.localhost") as prompt:
            assert sitename_callback(None) == "picked.localhost"
        assert prompt.call_count == 1
        assert sorted(prompt.call_args.kwargs["choices"]) == ["other.localhost", "picked.localhost"]

    def test_a_picked_bench_is_written_to_the_recent_sites_cache(self, benches):
        make_bench(benches, "picked.localhost")
        handler = get_global_output_handler()
        with mock.patch.object(handler, "prompt_fuzzy", return_value="picked.localhost"):
            sitename_callback(None)
        assert "picked.localhost" in callbacks.CLI_RECENT_USED_SITES_CACHE_PATH.read_text()

    def test_a_failing_prompt_raises_a_clean_non_interactive_error(self, benches):
        # A missing bench name in a non-interactive shell is a usage error, not an fm crash.
        # NonInteractiveError is a FrappeManagerException, so main.py prints it under
        # "Error Occurred" instead of the "Unexpected Error" banner reserved for internal
        # crashes, and the callback prints nothing itself so the message appears exactly once.
        make_bench(benches, "picked.localhost")
        handler = get_global_output_handler()
        with (
            mock.patch.object(handler, "prompt_fuzzy", side_effect=EOFError("no tty")),
            mock.patch.object(handler, "display_error") as display_error,
            pytest.raises(NonInteractiveError) as excinfo,
        ):
            sitename_callback(None)
        assert isinstance(excinfo.value, FrappeManagerException)
        assert "Bench name is required in non-interactive mode" in str(excinfo.value)
        assert "fm list" in str(excinfo.value)
        assert isinstance(excinfo.value.__cause__, EOFError)
        display_error.assert_not_called()


# --------------------------------------------------------------------------- #
# sites_autocompletion_callback -- shape only; it does read the benches dir,
# so every test here points it at tmp_path.
# --------------------------------------------------------------------------- #


class TestSitesAutocompletionCallback:
    def test_returns_compose_file_paths_for_each_bench(self, benches):
        make_bench(benches, "a.localhost")
        make_bench(benches, "b.localhost")
        result = sites_autocompletion_callback()
        assert all(isinstance(p, Path) for p in result)
        # Callers derive the bench name via `.parent.name`.
        assert sorted(p.parent.name for p in result) == ["a.localhost", "b.localhost"]
        assert {p.name for p in result} == {"docker-compose.yml"}

    def test_directory_without_a_compose_file_is_not_a_bench(self, benches):
        (benches / "half-baked.localhost").mkdir()
        make_bench(benches, "real.localhost")
        assert [p.parent.name for p in sites_autocompletion_callback()] == ["real.localhost"]

    def test_plain_files_in_the_benches_directory_are_ignored(self, benches):
        (benches / "stray.txt").write_text("x")
        assert sites_autocompletion_callback() == []

    def test_empty_benches_directory_yields_no_completions(self, benches):
        assert sites_autocompletion_callback() == []

    def test_missing_benches_directory_raises(self, tmp_path, monkeypatch):
        # No guard on iterdir(): a fresh install with no ~/frappe/sites makes shell
        # completion raise instead of returning []. Pinned; see final report.
        monkeypatch.setattr(callbacks, "CLI_BENCHES_DIRECTORY", tmp_path / "absent")
        with pytest.raises(FileNotFoundError):
            sites_autocompletion_callback()
