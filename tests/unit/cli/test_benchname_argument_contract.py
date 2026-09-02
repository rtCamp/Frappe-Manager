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
    bench_all_autocompletion_callback,
    bench_all_callback,
    bench_domain_autocompletion_callback,
    bench_domain_callback,
    bench_site_autocompletion_callback,
    bench_site_callback,
    sitename_callback,
    sites_autocompletion_callback,
)

PARAM_NAME = "benchname"


@dataclass(frozen=True)
class BenchnameSpec:
    """Everything about a `benchname` argument that a user or shell can observe."""

    help: str
    metavar: str | None
    """The token the USAGE LINE shows. Observable, and the thing that told an operator `fm shell`
    took a bench name when it takes `BENCH/SITE`, so it belongs in this spec like the help does."""
    default: object
    required: bool
    type_name: str
    autocompletion: object
    callback: object


# The block repeated verbatim across 12 command modules -- the dedup target.
CANONICAL = BenchnameSpec(
    help="Bench to act on. Omit to pick from the benches you have.",
    metavar="BENCH",
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
        "fm info",
        "fm logs",
        "fm ngrok",
        "fm restart",
        "fm start",
        "fm stop",
    }
)

# Commands whose `benchname` legitimately differs today. Each entry is a real
# difference a dedup refactor must either preserve or change on purpose -- NOT a
# list of things that are allowed to drift. The spec is pinned exactly.
EXCEPTIONS: dict[str, BenchnameSpec] = {
    # `create` makes a NEW bench, so completing over existing benches would be
    # actively wrong, and `sitename_callback` (which requires the bench to exist)
    # would reject every valid input. Required, bare `str`, and its own callback:
    # `create_command_sitename_callback` validates the name and refuses one whose
    # bench directory already exists. It used to carry NO callback at all, which is
    # what let `fm create existing.localhost` overwrite a live bench -- nothing else
    # on the create path checks, and `--allow-domain-conflicts` turns the only other
    # gate off. The absent callback was the bug, not the contract.
    #
    # It is now the SECOND command to accept a site part, and for the opposite reason
    # to `fm shell`: `BENCH/SITE` adds a site to a bench that exists. So the help text
    # describes an address, and the callback takes `ctx` in order to hand the site half
    # on through `ctx.obj["site"]`.
    "fm create": BenchnameSpec(
        help=(
            "Bench to create, or BENCH/SITE to add a site to a bench that already exists. A bench name is just "
            "a name: 'shop' creates a bench 'shop' serving a site 'shop.localhost', and a name that is already "
            "a domain serves that domain."
        ),
        metavar="BENCH(/SITE)",
        default=None,
        required=True,
        type_name="text",
        autocompletion=None,
        callback=callbacks.create_command_sitename_callback,
    ),
    # `maintenance --status` may run bench-less, so it swaps in a wrapper that
    # lets `None` through for that one flag and otherwise delegates.
    "fm maintenance": BenchnameSpec(
        help="Bench to act on. Optional with --status, which then lists every domain in maintenance.",
        metavar="BENCH",
        default=None,
        required=False,
        type_name="text",
        autocompletion=sites_autocompletion_callback,
        callback=_maintenance_sitename_callback,
    ),
    # `migrate` is the one bench-scoped command that can also run over every bench in a single run,
    # so it carries `BenchAllArgument`: completion offers `all` beside the bench names, and
    # `bench_all_callback` is `sitename_callback` for a named bench and a pass-through for the `all`
    # address, which names no directory. It used to have neither completion nor validation, because
    # acting on every bench lived in a `--all-benches` flag and the body did its own existence check.
    "fm migrate": BenchnameSpec(
        help="Bench to act on, or 'all' for every bench fm manages. Omit to act on nothing but fm itself.",
        metavar="BENCH|all",
        default=None,
        required=False,
        type_name="text",
        autocompletion=bench_all_autocompletion_callback,
        callback=bench_all_callback,
    ),
    # `bake` supports a standalone mode driven by --apps/--config, so it keeps
    # completion but drops the must-exist callback.
    "fm bake": BenchnameSpec(
        help="Bench to bake. Omit for a standalone bake driven by --apps/--config.",
        metavar="BENCH",
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
        help="Bench to act on.",
        metavar="BENCH",
        default=None,
        required=True,
        type_name="text",
        autocompletion=sites_autocompletion_callback,
        callback=sitename_callback,
    ),
    "fm prune": BenchnameSpec(
        help="Bench to act on.",
        metavar="BENCH",
        default=None,
        required=True,
        type_name="text",
        autocompletion=sites_autocompletion_callback,
        callback=sitename_callback,
    ),
    # `self compose` is a maintenance escape hatch: required, no completion, no callback.
    "fm self compose": BenchnameSpec(
        help="Bench to act on.",
        metavar="BENCH",
        default=None,
        required=True,
        type_name="text",
        autocompletion=None,
        callback=None,
    ),
    # The four `ssl` subcommands address a DOMAIN rather than a site: a certificate is keyed by
    # hostname and a bench serves its sites' names AND their aliases, so the population is wider
    # than `BenchSiteArgument`'s. `bench_domain_callback` parses `BENCH/DOMAIN` and hands the
    # domain half on through `ctx.obj["domain"]`. It deliberately does NOT require the bench to
    # exist: `--standalone` puts an external domain, belonging to no bench, in this position.
    #
    # They share the callback and the completer and SPLIT on the metavar, because the forms each
    # one will act on differ and a usage line must not advertise a refusal. `add` and `remove`
    # reject a bare `all` on blast radius (a certificate per domain of every bench crosses Let's
    # Encrypt's rate limit; dropping every certificate is a fleet-wide move to plain HTTP), while
    # `renew all` is the reason the form exists. `list` reports per BENCH and rejects a domain.
    **{
        f"fm ssl {sub}": BenchnameSpec(
            help="Bench, or BENCH/DOMAIN to act on one hostname it serves. 'BENCH/all' means every domain of that bench; a bare domain is for --standalone.",
            metavar="BENCH(/DOMAIN)",
            default=None,
            required=False,
            type_name="text",
            autocompletion=bench_domain_autocompletion_callback,
            callback=bench_domain_callback,
        )
        for sub in ("add", "remove")
    },
    "fm ssl renew": BenchnameSpec(
        help="Bench, BENCH/DOMAIN for one hostname, 'BENCH/all' for every domain of that bench, or 'all' for every bench. A bare domain is for --standalone.",
        metavar="BENCH(/DOMAIN)|all",
        default=None,
        required=False,
        type_name="text",
        autocompletion=bench_domain_autocompletion_callback,
        callback=bench_domain_callback,
    ),
    "fm ssl list": BenchnameSpec(
        help="Bench, or 'all' for every bench and the external domains together. Naming a single domain is refused: this reports every certificate the bench holds.",
        metavar="BENCH|all",
        default=None,
        required=False,
        type_name="text",
        autocompletion=bench_domain_autocompletion_callback,
        callback=bench_domain_callback,
    ),
    # Four commands address a SITE, for three different reasons, and all four share the one
    # alias `BenchSiteArgument`: same help text, same callback, `bench_site_callback`, the only
    # one that accepts a site part, and the only completer that offers sites.
    #
    # `fm shell`: the site half of the address becomes FRAPPE_SITE in the container, which Frappe
    # reads above common_site_config's default_site, so bare `bench` commands inside the shell
    # target it.
    #
    # `fm delete` and `fm reset`: a bench holds several sites, so destroying one site is a
    # different act from destroying the bench, and the address is how the operator says which.
    # They are here rather than in KNOWN_CANONICAL because moving off `sitename_callback` is
    # exactly the change that must not happen by accident.
    #
    # `fm update`: alias domains moved from the bench to the site, so `--add-alias` has to say
    # WHICH site the new hostname reaches. It moved off the canonical block deliberately -- this
    # entry is that decision, not drift.
    **{
        f"fm {command}": BenchnameSpec(
            help="Bench, or BENCH/SITE to act on one of its sites. Without a site part, the bench's primary site is used.",
            metavar="BENCH(/SITE)",
            default=None,
            required=False,
            type_name="text",
            autocompletion=bench_site_autocompletion_callback,
            callback=bench_site_callback,
        )
        for command in ("shell", "delete", "reset", "update")
    },
    # dns-config credentials can be global, hence its own wording.
    "fm ssl dns-config cloudflare": BenchnameSpec(
        help="Bench to configure. Omit for global credentials.",
        metavar="BENCH",
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
        metavar=param.metavar,
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
    assert completers == {
        sites_autocompletion_callback,
        bench_all_autocompletion_callback,
        bench_site_autocompletion_callback,
        bench_domain_autocompletion_callback,
    }

    # Each completer reaches exactly the arguments whose callback accepts what it offers. A
    # completer wired to an argument that refuses its output completes the operator straight into a
    # refusal: `shop/b.example.com` offered where only a bench is accepted, or `all` offered where
    # the bench has to exist.
    by_completer: dict[object, set[str]] = {}
    for name, param in BENCHNAME_ARGUMENTS.items():
        by_completer.setdefault(spec_of(param).autocompletion, set()).add(name)
    assert by_completer[bench_site_autocompletion_callback] == {"fm shell", "fm delete", "fm reset", "fm update"}
    assert by_completer[bench_domain_autocompletion_callback] == {
        "fm ssl add",
        "fm ssl list",
        "fm ssl remove",
        "fm ssl renew",
    }
    assert by_completer[bench_all_autocompletion_callback] == {"fm migrate"}


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
    """The narrower contract the test above stopped covering once the `ssl` commands gained a
    callback of their own: they have one now, but `bench_domain_callback` deliberately does NOT
    require the bench to exist, because `--standalone` puts a domain belonging to no bench into that
    same position.

    Three callbacks run the check, all of them through `_resolve_bench`: `sitename_callback`,
    `bench_site_callback`, and `bench_all_callback`, which is `sitename_callback` for every value
    except the `all` address. `fm migrate` is on this side of the line now: the existence check that
    used to live in its body moved into the argument when `--all-benches` became the `all` address,
    so a missing bench is refused as a usage error before the command body runs. A command that
    silently moves off one of those three loses the check and the picker, which is what this pins.
    """
    must_exist = {sitename_callback, bench_site_callback, bench_all_callback}
    skipping = {name for name, param in BENCHNAME_ARGUMENTS.items() if unwrap_callback(param) not in must_exist}
    expected = {
        "fm bake",
        "fm create",
        "fm maintenance",
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
    """A directory that `_bench_names` counts as a bench, so the completers and `all` see it.

    The bench config is what makes it one. The compose file is written too because a real bench has
    both, and a test that dropped it would stop noticing if the registry ever moved back.
    """
    bench = benches_dir / name
    bench.mkdir()
    (bench / "bench_config.toml").write_text(f'name = "{name}"\n')
    (bench / "docker-compose.yml").write_text("services: {}\n")
    return bench


class TestSitenameCallbackNormalisation:
    """A bench name is taken as typed, with `<name>.localhost` as a legacy fallback.

    A bench used to BE its site, so a bare `hello` was normalised to `hello.localhost` and resolved
    against that directory. Now the bench is just a name: `fm create shop` makes a bench called
    `shop` serving a site called `shop.localhost`. Resolution therefore prefers the name as typed
    and only then tries the suffixed form, so benches created under the old rule keep resolving.
    """

    def test_a_name_that_exists_as_typed_resolves_to_itself(self, benches):
        make_bench(benches, "hello")
        assert sitename_callback("hello") == "hello"

    def test_a_bare_name_falls_back_to_the_suffixed_bench(self, benches):
        """The legacy shape: a bench created before the names came apart is called
        `hello.localhost`, and `fm start hello` has to keep finding it."""
        make_bench(benches, "hello.localhost")
        assert sitename_callback("hello") == "hello.localhost"

    def test_the_name_as_typed_wins_over_the_suffixed_one(self, benches):
        """With both on disk the fallback must not shadow the real thing, so it is tried second."""
        make_bench(benches, "hello")
        make_bench(benches, "hello.localhost")
        assert sitename_callback("hello") == "hello"

    def test_name_already_ending_in_localhost_is_returned_unchanged(self, benches):
        make_bench(benches, "hello.localhost")
        assert sitename_callback("hello.localhost") == "hello.localhost"

    def test_multi_level_domain_is_returned_unchanged(self, benches):
        make_bench(benches, "shop.example.com")
        assert sitename_callback("shop.example.com") == "shop.example.com"

    def test_a_name_on_disk_nowhere_is_still_not_found(self, benches):
        """The fallback widens resolution; it must not make a missing bench resolve to something."""
        make_bench(benches, "other.localhost")
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


class TestBenchAllCallback:
    """`bench_all_callback` is `sitename_callback` plus one address.

    `fm migrate` used to spell "every bench" as `--all-benches` and check the named bench itself,
    inside the body. Both halves moved here: `all` is now a value of the argument, and everything
    that is not `all` goes through the same must-exist resolution every other bench command uses.
    """

    def test_the_all_address_is_returned_untouched_and_resolves_nothing(self, benches):
        # No bench called `all` exists here, and none needs to: `all` names no directory. If this
        # went through `_resolve_bench` it would raise BenchNotFoundError instead.
        assert callbacks.bench_all_callback("all") == "all"

    def test_a_named_bench_still_has_to_exist(self, benches):
        make_bench(benches, "real.localhost")
        with pytest.raises(BenchNotFoundError):
            callbacks.bench_all_callback("ghost.localhost")

    def test_a_named_bench_that_exists_resolves_like_any_other(self, benches):
        make_bench(benches, "hello.localhost")
        assert callbacks.bench_all_callback("hello") == "hello.localhost"

    def test_a_site_part_is_refused_even_though_all_is_accepted(self, benches):
        # Migrating one site of a bench is meaningless: the containers, workspace and workers are
        # shared, so the address form has to stay bench-wide.
        make_bench(benches, "shop")
        with pytest.raises(typer.BadParameter, match=r"takes a bench, not a site"):
            callbacks.bench_all_callback("shop/b.example.com")

    def test_all_is_offered_by_the_completer_beside_the_bench_names(self, benches):
        make_bench(benches, "a.localhost")
        assert callbacks.bench_all_autocompletion_callback("") == ["a.localhost", "all"]
        assert callbacks.bench_all_autocompletion_callback("al") == ["all"]


# --------------------------------------------------------------------------- #
# sites_autocompletion_callback -- the bench-ONLY completer. It reads the
# benches dir, so every test here points it at tmp_path.
# --------------------------------------------------------------------------- #


class TestSitesAutocompletionCallback:
    def test_returns_the_bench_names(self, benches):
        make_bench(benches, "a.localhost")
        make_bench(benches, "b.localhost")
        assert sites_autocompletion_callback("") == ["a.localhost", "b.localhost"]

    def test_offers_only_the_benches_matching_what_has_been_typed(self, benches):
        make_bench(benches, "shop")
        make_bench(benches, "warehouse")
        assert sites_autocompletion_callback("sh") == ["shop"]

    def test_directory_without_a_bench_config_is_not_a_bench(self, benches):
        # The config is the registry marker, not the directory and not the compose file: a
        # half-created bench has the compose file long before anything can act on it.
        half_baked = benches / "half-baked.localhost"
        half_baked.mkdir()
        (half_baked / "docker-compose.yml").write_text("services: {}\n")
        make_bench(benches, "real.localhost")
        assert sites_autocompletion_callback("") == ["real.localhost"]

    def test_plain_files_in_the_benches_directory_are_ignored(self, benches):
        (benches / "stray.txt").write_text("x")
        assert sites_autocompletion_callback("") == []

    def test_empty_benches_directory_yields_no_completions(self, benches):
        assert sites_autocompletion_callback("") == []

    def test_missing_benches_directory_yields_no_completions(self, tmp_path, monkeypatch):
        # A fresh install has no ~/frappe/sites yet. Shell completion runs on every TAB, so
        # this has to be silence and not a traceback in the middle of the operator's line.
        monkeypatch.setattr(callbacks, "CLI_BENCHES_DIRECTORY", tmp_path / "absent")
        assert sites_autocompletion_callback("") == []

    def test_a_bench_scoped_argument_never_offers_an_address(self, benches):
        """The invariant behind having several completers, asserted on behaviour not identity.

        `sitename_callback` REFUSES a value with a site part, so the completer it carries must never
        produce one: a shell that fills in `shop/b.example.com` would be completing the operator
        straight into a refusal.
        """
        bench = make_bench(benches, "shop")
        sites_dir = bench / "workspace" / "frappe-bench" / "sites" / "b.example.com"
        sites_dir.mkdir(parents=True)
        (sites_dir / "site_config.json").write_text("{}")

        for incomplete in ("", "sh", "shop", "shop/", "shop/b"):
            assert not [c for c in sites_autocompletion_callback(incomplete) if "/" in c]
