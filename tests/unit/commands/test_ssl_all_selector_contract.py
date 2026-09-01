"""What `--all` does on `fm ssl list` and `fm ssl renew`, pinned before it becomes an address.

Both paths shipped with no command-level test at all, and they are about to be rewritten into
`fm ssl list all` / `fm ssl renew all`. Converting untested code is how behaviour changes without
anyone noticing, so this file records what the flag does TODAY, including the parts that look
wrong. Where a test pins something questionable it says so, and the rewrite is free to change it
deliberately as long as it changes this file in the same commit.

Two facts these lock down, because they are the ones a rewrite is most likely to alter by accident:

- The two commands walk the same registry (`BenchService.get_bench_names`, which is compose-file
  backed) but handle a failing bench COMPLETELY differently. `list` has no error handling at all;
  `renew` catches two specific exceptions per bench and aborts the whole run on anything else.
- `renew` resolves the Bench object OUTSIDE its own try, so a bench whose config will not load
  aborts every remaining bench rather than being reported and skipped.
"""

from contextlib import ExitStack
from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock, patch

import pytest
import typer

from frappe_manager.commands.ssl.list import list_certificates
from frappe_manager.commands.ssl.renew import renew
from frappe_manager.site_manager.exceptions import BenchSSLCertificateNotIssued
from frappe_manager.ssl_manager.certificate_exceptions import SSLCertificateNotDueForRenewalError

LIST_MODULE = "frappe_manager.commands.ssl.list"
RENEW_MODULE = "frappe_manager.commands.ssl.renew"

BENCHES = ["alpha.localhost", "beta.localhost", "gamma.localhost"]


class _Harness:
    def __init__(self, stack: ExitStack, module: str, benches: list[str]):
        self.output = MagicMock(name="output_handler")
        self.ctx = MagicMock(name="ctx")
        self.ctx.obj = {"services": MagicMock(name="services_manager")}

        stack.enter_context(patch(f"{module}.get_output_handler", return_value=self.output))
        self.bench_service = stack.enter_context(patch(f"{module}.BenchService")).return_value
        self.bench_service.get_bench_names.return_value = list(benches)

    def printed(self) -> str:
        return "\n".join(str(c) for c in self.output.print.call_args_list)


# --------------------------------------------------------------------------- ssl list --all


@pytest.fixture
def listing():
    with ExitStack() as stack:
        harness = _Harness(stack, LIST_MODULE, BENCHES)
        harness.external = stack.enter_context(patch(f"{LIST_MODULE}._list_external_certificates"))
        harness.bench_listing = stack.enter_context(patch(f"{LIST_MODULE}._list_bench_certificates"))
        yield harness


def test_list_all_covers_the_external_domains_and_then_every_bench(listing):
    list_certificates(listing.ctx, benchname=None, standalone=False, all=True)

    listing.external.assert_called_once()
    assert [c.args[1] for c in listing.bench_listing.call_args_list] == BENCHES


def test_list_all_puts_the_external_section_before_the_bench_one(listing):
    """Order is the contract here: the two sections are only distinguishable by their headings."""
    list_certificates(listing.ctx, benchname=None, standalone=False, all=True)

    printed = listing.printed()
    assert printed.index("External Certificates") < printed.index("Bench Certificates")


def test_list_all_walks_the_compose_backed_registry(listing):
    """`get_bench_names` is `discover_benches`, which counts a directory as a bench when it holds a
    `docker-compose.yml`. `fm migrate --all-benches` walks a DIFFERENT registry keyed on
    `bench_config.toml`, so the two disagree on a half-created or half-destroyed bench."""
    list_certificates(listing.ctx, benchname=None, standalone=False, all=True)

    listing.bench_service.get_bench_names.assert_called_once_with()


def test_list_all_with_no_benches_still_lists_the_external_domains(listing):
    listing.bench_service.get_bench_names.return_value = []

    list_certificates(listing.ctx, benchname=None, standalone=False, all=True)

    listing.external.assert_called_once()
    assert "No benches found" in listing.printed()


def test_list_all_aborts_on_the_first_failing_bench(listing):
    """QUESTIONABLE, pinned as-is: there is no try/except in the loop, so one unreadable bench
    hides every bench after it from a command whose whole job is to report. The rewrite may well
    make this report-and-continue; this test exists so that becomes a decision."""
    listing.bench_listing.side_effect = [None, RuntimeError("beta is broken"), None]

    with pytest.raises(RuntimeError, match="beta is broken"):
        list_certificates(listing.ctx, benchname=None, standalone=False, all=True)

    assert [c.args[1] for c in listing.bench_listing.call_args_list] == BENCHES[:2]


# --------------------------------------------------------------------------- ssl renew --all


@pytest.fixture
def renewing():
    with ExitStack() as stack:
        harness = _Harness(stack, RENEW_MODULE, BENCHES)
        harness.Bench = stack.enter_context(patch(f"{RENEW_MODULE}.Bench"))
        harness.benches = {name: MagicMock(name=f"bench:{name}") for name in BENCHES}
        harness.Bench.get_object.side_effect = lambda name, *a, **k: harness.benches[name]
        stack.enter_context(patch(f"{RENEW_MODULE}.spinner"))
        yield harness


def _renew_all(harness, **kwargs):
    return renew(harness.ctx, benchname=None, domain=None, all=True, standalone=False, dry_run=False, force=False, **kwargs)


def test_renew_all_renews_every_bench_in_the_registry(renewing):
    _renew_all(renewing)

    renewed = [name for name, bench in renewing.benches.items() if bench.ssl.renew_all_certificates.called]
    assert renewed == BENCHES


def test_renew_all_asks_each_bench_for_all_of_its_certificates(renewing):
    """With no domain named, the per-bench call is the bulk one, not a loop over domains."""
    _renew_all(renewing)

    for bench in renewing.benches.values():
        bench.ssl.renew_all_certificates.assert_called_once_with(dry_run=False, force=False)


def test_renew_all_carries_dry_run_and_force_to_every_bench(renewing):
    renew(
        renewing.ctx, benchname=None, domain=None, all=True, standalone=False, dry_run=True, force=True
    )

    for bench in renewing.benches.values():
        bench.ssl.renew_all_certificates.assert_called_once_with(dry_run=True, force=True)


def test_a_certificate_not_due_is_reported_and_the_next_bench_still_runs(renewing):
    """The one failure the loop tolerates: not-due and not-issued warn, and the walk continues."""
    not_due = SSLCertificateNotDueForRenewalError(
        "beta.localhost", datetime.now(UTC) + timedelta(days=30)
    )
    renewing.benches["beta.localhost"].ssl.renew_all_certificates.side_effect = not_due

    _renew_all(renewing)

    assert renewing.benches["gamma.localhost"].ssl.renew_all_certificates.called
    assert renewing.output.warning.called


def test_a_certificate_not_issued_is_also_tolerated(renewing):
    renewing.benches["beta.localhost"].ssl.renew_all_certificates.side_effect = BenchSSLCertificateNotIssued(
        "beta.localhost"
    )

    _renew_all(renewing)

    assert renewing.benches["gamma.localhost"].ssl.renew_all_certificates.called


def test_any_other_failure_aborts_the_whole_run(renewing):
    """QUESTIONABLE, pinned as-is: one bench failing for an unexpected reason means every bench
    after it is silently not renewed, on a command run from cron."""
    renewing.benches["beta.localhost"].ssl.renew_all_certificates.side_effect = RuntimeError("acme exploded")

    with pytest.raises(typer.Exit) as exc:
        _renew_all(renewing)

    assert exc.value.exit_code == 1
    assert not renewing.benches["gamma.localhost"].ssl.renew_all_certificates.called


def test_a_bench_that_will_not_load_aborts_before_the_loop_can_catch_it(renewing):
    """`Bench.get_object` is called OUTSIDE the try, so this is not even the tolerated path: the
    exception escapes as itself rather than becoming a reported failure."""
    renewing.Bench.get_object.side_effect = [
        renewing.benches["alpha.localhost"],
        RuntimeError("bench_config.toml is unreadable"),
    ]

    with pytest.raises(RuntimeError, match="unreadable"):
        _renew_all(renewing)

    assert not renewing.benches["gamma.localhost"].ssl.renew_all_certificates.called
