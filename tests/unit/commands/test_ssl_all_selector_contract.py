"""What the address `all` does on the `fm ssl` commands, now that the flag is gone.

This file was a characterisation baseline: it pinned `fm ssl list --all` and `fm ssl renew --all`
before either became an address, including the parts that looked wrong, and said the rewrite was
free to change them deliberately as long as it changed this file in the same commit. This is that
change.

Four of the old pins are now asserted the other way round, and each says so where it stands:

- The selector is the literal benchname `all`, not a `--all` flag. The flag no longer exists on
  either command, which is asserted at the CLI boundary because "no such option" is what the
  operator meets.
- `renew all` reports a failing bench and carries on to the rest, then exits 1 naming what failed.
  The baseline pinned the opposite: the first unexpected failure took every remaining bench with
  it, so a cron run left certificates unrenewed and said nothing.
- A bench whose object will not construct is part of that same report-and-continue policy. It used
  to escape as itself from OUTSIDE the loop's try, which was not even the tolerated path.
- The commands take ONE positional address, `BENCH/DOMAIN`, not two positionals. `BENCH/all` is
  every domain the named bench serves, and a bare `all` is refused on `add` and `remove` because
  fanning either of those over every bench is a rate limit or a blast radius, not a convenience.
"""

import inspect
from contextlib import ExitStack
from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock, patch

import pytest
import typer
from typer.testing import CliRunner

from frappe_manager.commands.ssl.add import add_certificate
from frappe_manager.commands.ssl.list import list_certificates
from frappe_manager.commands.ssl.remove import remove_certificate
from frappe_manager.commands.ssl.renew import renew
from frappe_manager.site_manager.exceptions import BenchSSLCertificateNotIssued
from frappe_manager.ssl_manager.certificate_exceptions import SSLCertificateNotDueForRenewalError
from frappe_manager.utils.callbacks import RESERVED_BENCH_NAME

LIST_MODULE = "frappe_manager.commands.ssl.list"
RENEW_MODULE = "frappe_manager.commands.ssl.renew"
ADD_MODULE = "frappe_manager.commands.ssl.add"
REMOVE_MODULE = "frappe_manager.commands.ssl.remove"
BENCH_HELPERS = "frappe_manager.commands.ssl.bench_helpers"

BENCHES = ["alpha.localhost", "beta.localhost", "gamma.localhost"]
BENCH = "alpha.localhost"
DOMAINS = ["alpha.localhost", "shop.example.com"]

runner = CliRunner()


class _Harness:
    def __init__(self, stack: ExitStack, module: str):
        self.output = MagicMock(name="output_handler")
        self.ctx = MagicMock(name="ctx")
        self.ctx.obj = {"services": MagicMock(name="services_manager")}

        stack.enter_context(patch(f"{module}.get_output_handler", return_value=self.output))

    def address(self, domain: str) -> None:
        """The second segment of `BENCH/DOMAIN`, where `bench_domain_callback` puts it."""
        self.ctx.obj["domain"] = domain

    def printed(self) -> str:
        return "\n".join(str(c) for c in self.output.print.call_args_list)

    def errors(self) -> str:
        return "\n".join(str(c) for c in self.output.display_error.call_args_list)


def _app(name, fn):
    app = typer.Typer()
    app.command(name)(fn)
    return app


def _said(result) -> str:
    """`result.output` with rich's box drawing and wrapping flattened, as in the CLI tests."""
    text = result.output
    for char in "│╭╮╰╯─":
        text = text.replace(char, " ")
    return " ".join(text.split())


def test_the_reserved_word_is_the_one_an_operator_types():
    """Every assertion below spells the selector out, so this ties them to the constant the
    commands compare against."""
    assert RESERVED_BENCH_NAME == "all"


# ------------------------------------------------------- the flag is gone, the address replaced it


@pytest.mark.parametrize(("name", "command"), [("list", list_certificates), ("renew", renew)])
def test_the_all_flag_no_longer_exists(name, command):
    """INVERTED. The baseline called both commands with `all=True`; there is no such parameter and
    no such option now, and an operator who types the old form must be told so rather than have it
    parsed as something else."""
    assert "all" not in inspect.signature(command).parameters

    result = runner.invoke(_app(name, command), ["--all"])

    assert result.exit_code == 2
    assert "No such option" in _said(result)


@pytest.mark.parametrize(
    ("name", "command"),
    [("add", add_certificate), ("renew", renew), ("remove", remove_certificate), ("list", list_certificates)],
)
def test_the_commands_take_one_positional_address(name, command):
    """INVERTED. `fm ssl add mybench example.com` was two positionals; the address is one word
    now, so the old form is an extra argument rather than a domain."""
    assert "domain" not in inspect.signature(command).parameters

    result = runner.invoke(_app(name, command), ["mybench", "example.com"])

    assert result.exit_code == 2
    assert "extra argument" in _said(result)


# --------------------------------------------------------------------------------- fm ssl list all


@pytest.fixture
def listing():
    with ExitStack() as stack:
        harness = _Harness(stack, LIST_MODULE)
        # The real `resolve_bench_targets` runs, so `all` expanding to the shared registry is the
        # code under test rather than a stub.
        harness.bench_names = stack.enter_context(
            patch("frappe_manager.utils.callbacks._bench_names", return_value=list(BENCHES))
        )
        harness.external = stack.enter_context(patch(f"{LIST_MODULE}._list_external_certificates"))
        harness.bench_listing = stack.enter_context(patch(f"{LIST_MODULE}._list_bench_certificates"))
        yield harness


def test_list_all_covers_the_external_domains_and_then_every_bench(listing):
    """INVERTED from `all=True` to the address: the selector sits where a bench name goes."""
    list_certificates(listing.ctx, address="all", standalone=False)

    listing.external.assert_called_once()
    assert [c.args[1] for c in listing.bench_listing.call_args_list] == BENCHES


def test_list_all_puts_the_external_section_before_the_bench_one(listing):
    """Order is the contract here: the two sections are only distinguishable by their headings."""
    list_certificates(listing.ctx, address="all", standalone=False)

    printed = listing.printed()
    assert printed.index("External Certificates") < printed.index("Bench Certificates")


def test_list_all_walks_the_same_registry_as_every_other_all(listing):
    """One registry for every `all`, the one the picker and shell completion offer, so what the
    shell suggests is what the address acts on. `list` used to walk its own compose-file backed
    registry instead, which disagreed with renew's on a half-created bench."""
    list_certificates(listing.ctx, address="all", standalone=False)

    listing.bench_names.assert_called_once_with()


def test_list_all_with_no_benches_still_lists_the_external_domains(listing):
    listing.bench_names.return_value = []

    list_certificates(listing.ctx, address="all", standalone=False)

    listing.external.assert_called_once()
    assert "No benches found" in listing.printed()


def test_list_standalone_lists_the_external_domains_and_nothing_else(listing):
    """The other half of the split `all` makes visible: --standalone is a namespace, not a
    selector, so it must not pick up the benches `all` covers."""
    list_certificates(listing.ctx, address=None, standalone=True)

    listing.external.assert_called_once()
    listing.bench_listing.assert_not_called()
    listing.bench_names.assert_not_called()


def test_list_all_wins_over_standalone(listing):
    """`all` is the wider of the two, and it already includes the external domains."""
    list_certificates(listing.ctx, address="all", standalone=True)

    listing.external.assert_called_once()
    assert [c.args[1] for c in listing.bench_listing.call_args_list] == BENCHES


def test_list_refuses_a_domain_in_the_address(listing):
    """`fm ssl list bench/shop.example.com` reads like it filters, and it cannot: listing is
    per-bench and reports every certificate the bench holds."""
    listing.address("shop.example.com")

    with pytest.raises(typer.Exit) as exc:
        list_certificates(listing.ctx, address=BENCH, standalone=False)

    assert exc.value.exit_code == 1
    assert "takes a bench, not a single domain" in listing.errors()
    listing.bench_listing.assert_not_called()


def test_the_refusal_names_the_address_that_does_work(listing):
    listing.address("shop.example.com")

    with pytest.raises(typer.Exit):
        list_certificates(listing.ctx, address=BENCH, standalone=False)

    assert f"fm ssl list {BENCH}" in listing.errors()


def test_list_all_reports_a_failing_bench_and_lists_the_rest(listing):
    """INVERTED. The baseline pinned no try/except in the loop at all, so one unreadable bench hid
    every bench after it from a command whose whole job is to report."""
    listing.bench_listing.side_effect = [None, RuntimeError("beta is broken"), None]

    with pytest.raises(typer.Exit):
        list_certificates(listing.ctx, address="all", standalone=False)

    assert [c.args[1] for c in listing.bench_listing.call_args_list] == BENCHES
    assert "beta is broken" in listing.errors()


def test_a_failing_bench_makes_the_listing_exit_nonzero(listing):
    """Same shape as `renew`, for the scripting reason rather than the reporting one: a caller that
    checks the exit code must not be told the report was complete when a bench is missing from it.
    The listing still reaches every other bench first, which is what separates this from aborting."""
    listing.bench_listing.side_effect = [None, RuntimeError("beta is broken"), None]

    with pytest.raises(typer.Exit) as excinfo:
        list_certificates(listing.ctx, address="all", standalone=False)

    assert excinfo.value.exit_code == 1


def test_the_listing_exit_names_every_bench_it_could_not_read(listing):
    # One unreadable bench must not mask a second one: the summary is the only place the operator
    # sees them together, since each error was printed pages apart in the report above it.
    listing.bench_listing.side_effect = [RuntimeError("alpha is broken"), None, RuntimeError("gamma is broken")]

    with pytest.raises(typer.Exit):
        list_certificates(listing.ctx, address="all", standalone=False)

    assert f"Could not list: {BENCHES[0]}, {BENCHES[2]}" in listing.errors()


def test_a_listing_where_every_bench_is_readable_exits_zero(listing):
    listing.bench_listing.side_effect = [None, None, None]

    assert list_certificates(listing.ctx, address="all", standalone=False) is None


# -------------------------------------------------------------------------------- fm ssl renew all


@pytest.fixture
def renewing():
    with ExitStack() as stack:
        harness = _Harness(stack, RENEW_MODULE)
        harness.Bench = stack.enter_context(patch(f"{RENEW_MODULE}.Bench"))
        harness.benches = {name: MagicMock(name=f"bench:{name}") for name in BENCHES}
        harness.Bench.get_object.side_effect = lambda name, *a, **k: harness.benches[name]
        # The real `resolve_bench_targets` runs, so `all` expanding to the registry (and a named
        # bench NOT expanding) is the code under test rather than a stub.
        stack.enter_context(patch("frappe_manager.utils.callbacks._bench_names", return_value=list(BENCHES)))
        stack.enter_context(patch(f"{RENEW_MODULE}.spinner"))
        harness.external_all = stack.enter_context(patch(f"{RENEW_MODULE}._renew_all_external_certificates"))
        yield harness


def _renew_all(harness, **kwargs):
    kwargs.setdefault("standalone", False)
    kwargs.setdefault("dry_run", False)
    kwargs.setdefault("force", False)
    return renew(harness.ctx, address="all", **kwargs)


def _renewed(harness) -> list[str]:
    return [name for name, bench in harness.benches.items() if bench.ssl.renew_all_certificates.called]


def test_renew_all_renews_every_bench_in_the_registry(renewing):
    """INVERTED from `all=True` to the address."""
    _renew_all(renewing)

    assert _renewed(renewing) == BENCHES


def test_renew_all_asks_each_bench_for_all_of_its_certificates(renewing):
    """With no domain in the address, the per-bench call is the bulk one, not a loop over
    domains."""
    _renew_all(renewing)

    for bench in renewing.benches.values():
        bench.ssl.renew_all_certificates.assert_called_once_with(dry_run=False, force=False)


def test_renew_all_carries_dry_run_and_force_to_every_bench(renewing):
    _renew_all(renewing, dry_run=True, force=True)

    for bench in renewing.benches.values():
        bench.ssl.renew_all_certificates.assert_called_once_with(dry_run=True, force=True)


def test_renew_of_one_bench_stays_on_that_bench(renewing):
    """The selector has to select: naming a bench must not expand to the registry now that the
    same positional carries both."""
    renew(renewing.ctx, address=BENCH, standalone=False, dry_run=False, force=False)

    assert _renewed(renewing) == [BENCH]


def test_a_certificate_not_due_is_reported_and_the_next_bench_still_runs(renewing):
    not_due = SSLCertificateNotDueForRenewalError("beta.localhost", datetime.now(UTC) + timedelta(days=30))
    renewing.benches["beta.localhost"].ssl.renew_all_certificates.side_effect = not_due

    _renew_all(renewing)

    assert renewing.benches["gamma.localhost"].ssl.renew_all_certificates.called
    assert renewing.output.warning.called


def test_a_certificate_not_due_is_not_a_failure(renewing):
    """Nothing was wrong, so the run must still succeed: exiting 1 here would make a cron entry
    that renews nothing on most days look permanently broken."""
    not_due = SSLCertificateNotDueForRenewalError("beta.localhost", datetime.now(UTC) + timedelta(days=30))
    renewing.benches["beta.localhost"].ssl.renew_all_certificates.side_effect = not_due

    _renew_all(renewing)

    assert not renewing.output.display_error.called


def test_a_certificate_not_issued_is_also_tolerated(renewing):
    renewing.benches["beta.localhost"].ssl.renew_all_certificates.side_effect = BenchSSLCertificateNotIssued(
        "beta.localhost"
    )

    _renew_all(renewing)

    assert renewing.benches["gamma.localhost"].ssl.renew_all_certificates.called


def test_any_other_failure_is_reported_and_the_walk_continues(renewing):
    """INVERTED. The baseline pinned the first unexpected failure aborting the whole run, which on
    a scheduled renewal meant every bench after the broken one silently went unrenewed until a
    certificate expired."""
    renewing.benches["beta.localhost"].ssl.renew_all_certificates.side_effect = RuntimeError("acme exploded")

    with pytest.raises(typer.Exit) as exc:
        _renew_all(renewing)

    assert exc.value.exit_code == 1
    assert _renewed(renewing) == BENCHES
    assert "acme exploded" in renewing.errors()


def test_a_bench_that_will_not_load_is_reported_and_skipped(renewing):
    """INVERTED. `Bench.get_object` used to be called OUTSIDE the try, so a bench whose config will
    not parse escaped as itself and took every remaining bench with it. It is now the same
    report-and-continue path as any other failure."""

    def get_object(name, *a, **k):
        if name == "beta.localhost":
            raise RuntimeError("bench_config.toml is unreadable")
        return renewing.benches[name]

    renewing.Bench.get_object.side_effect = get_object

    with pytest.raises(typer.Exit) as exc:
        _renew_all(renewing)

    assert exc.value.exit_code == 1
    assert renewing.benches["gamma.localhost"].ssl.renew_all_certificates.called
    assert "unreadable" in renewing.errors()


def test_the_exit_names_every_bench_that_failed(renewing):
    """A run over the whole registry is only actionable if it says which benches to go and look
    at, and one failure must not mask a later one."""
    renewing.benches["alpha.localhost"].ssl.renew_all_certificates.side_effect = RuntimeError("acme exploded")
    renewing.benches["gamma.localhost"].ssl.renew_all_certificates.side_effect = RuntimeError("dns timed out")

    with pytest.raises(typer.Exit):
        _renew_all(renewing)

    summary = renewing.output.display_error.call_args.args[0]
    assert summary == "Renewal failed for: alpha.localhost, gamma.localhost"


def test_a_run_where_every_bench_succeeds_does_not_exit_nonzero(renewing):
    _renew_all(renewing)

    assert not renewing.output.display_error.called


def test_renew_all_standalone_renews_every_external_domain(renewing):
    """--standalone is the other namespace, so `all` there means every external domain and no
    bench is touched at all."""
    renew(renewing.ctx, address="all", standalone=True, dry_run=True, force=True)

    renewing.external_all.assert_called_once_with(renewing.ctx, True, True)
    assert _renewed(renewing) == []


# ------------------------------------------------------------- fm ssl add / remove, and BENCH/all


@pytest.fixture
def bench_domains():
    """`_resolve_domains` runs for real; only the bench it loads is a mock."""
    with ExitStack() as stack:
        bench = MagicMock(name="bench")
        bench.bench_config.domains = list(DOMAINS)
        stack.enter_context(patch(f"{BENCH_HELPERS}.Bench")).get_object.return_value = bench
        stack.enter_context(patch(f"{BENCH_HELPERS}.get_output_handler", return_value=MagicMock()))
        yield bench


@pytest.fixture
def adding(bench_domains):
    with ExitStack() as stack:
        harness = _Harness(stack, ADD_MODULE)
        harness.issue = stack.enter_context(patch(f"{ADD_MODULE}._add_bench_certificate"))
        yield harness


@pytest.fixture
def removing(bench_domains):
    with ExitStack() as stack:
        harness = _Harness(stack, REMOVE_MODULE)
        harness.delete = stack.enter_context(patch(f"{REMOVE_MODULE}._remove_bench_certificate"))
        yield harness


def test_add_refuses_a_bare_all(adding):
    """Issuing for every domain of every bench can cross Let's Encrypt's rate limit in one
    command, so the wide selector is refused where the narrow one is not."""
    with pytest.raises(typer.Exit) as exc:
        add_certificate(adding.ctx, address="all")

    assert exc.value.exit_code == 1
    assert "'all' is not accepted here" in adding.errors()
    adding.issue.assert_not_called()


def test_the_add_refusal_points_at_the_per_bench_form(adding):
    with pytest.raises(typer.Exit):
        add_certificate(adding.ctx, address="all")

    assert "BENCH/all" in adding.errors()


def test_remove_refuses_a_bare_all(removing):
    """Every certificate of every bench back to plain HTTP in one command is a blast radius, not a
    convenience."""
    with pytest.raises(typer.Exit) as exc:
        remove_certificate(removing.ctx, address="all")

    assert exc.value.exit_code == 1
    assert "'all' is not accepted here" in removing.errors()
    removing.delete.assert_not_called()


def test_the_remove_refusal_points_at_the_per_bench_form(removing):
    with pytest.raises(typer.Exit):
        remove_certificate(removing.ctx, address="all")

    assert "BENCH/all" in removing.errors()


def test_add_fans_a_bench_slash_all_over_every_domain_the_bench_serves(adding):
    adding.address("all")

    add_certificate(adding.ctx, address=BENCH)

    assert [c.args[2] for c in adding.issue.call_args_list] == DOMAINS
    assert {c.args[1] for c in adding.issue.call_args_list} == {BENCH}


def test_add_of_one_domain_issues_exactly_that_one(adding):
    """The narrow address must not be widened by the same code path: `_resolve_domains` returns the
    domain as given so the bench's own check can still report an unknown one."""
    adding.address("shop.example.com")

    add_certificate(adding.ctx, address=BENCH)

    assert [c.args[2] for c in adding.issue.call_args_list] == ["shop.example.com"]


def test_remove_fans_a_bench_slash_all_over_every_domain_the_bench_serves(removing):
    removing.address("all")

    remove_certificate(removing.ctx, address=BENCH, yes=True)

    assert [c.args[2] for c in removing.delete.call_args_list] == DOMAINS
    assert {c.args[1] for c in removing.delete.call_args_list} == {BENCH}


def test_remove_of_one_domain_deletes_exactly_that_one(removing):
    removing.address("shop.example.com")

    remove_certificate(removing.ctx, address=BENCH, yes=True)

    assert [c.args[2] for c in removing.delete.call_args_list] == ["shop.example.com"]


def test_add_without_a_domain_refuses_rather_than_guessing(adding):
    """A bench name alone is not an address for `add`: it could only mean `BENCH/all`, which is the
    thing the operator has to ask for explicitly."""
    with pytest.raises(typer.Exit) as exc:
        add_certificate(adding.ctx, address=BENCH)

    assert exc.value.exit_code == 1
    assert "BENCH/DOMAIN" in adding.errors()
    adding.issue.assert_not_called()


def test_remove_without_a_domain_refuses_rather_than_guessing(removing):
    with pytest.raises(typer.Exit) as exc:
        remove_certificate(removing.ctx, address=BENCH, yes=True)

    assert exc.value.exit_code == 1
    assert "BENCH/DOMAIN" in removing.errors()
    removing.delete.assert_not_called()
