"""List SSL certificates command."""

from typing import Annotated

import typer
from typer_examples import example

from frappe_manager.commands.arguments import BenchOnlyAllArgument
from frappe_manager.utils.callbacks import RESERVED_BENCH_NAME, prompt_for_bench_selection, resolve_bench_targets

from .bench_helpers import _bench_certificate_data, _list_bench_certificates
from .external_helpers import _external_certificate_rows, _list_external_certificates
from .helpers import get_output_handler


@example(
    "List a bench's certificates",
    "{benchname}",
    benchname="mybench",
)
@example(
    "List the external domains",
    "--standalone",
)
@example(
    "List every certificate fm manages",
    "all",
    detail="Every bench and the external domains together. A bench fm cannot read is reported in place, not fatal.",
)
def list_certificates(
    ctx: typer.Context,
    address: BenchOnlyAllArgument = None,
    standalone: Annotated[
        bool,
        typer.Option("--standalone", help="List external (non-bench) domains instead of a bench."),
    ] = False,
):
    """
    List SSL certificates with their expiry and renewal status.

    Lists one bench by default, including its domains that have no certificate yet. 'all' lists every bench and the external domains together, and --standalone lists only the external Docker project domains.

    The DNS Provider column names the \\[ssl.dns_providers] credential set each DNS-01 certificate authenticates with, "default" for the unlabelled account, and "(missing)" when the label or the default account is not stored at either scope. Any row that is not a DNS-01 certificate reads "N/A".
    """

    if ctx.obj and ctx.obj.get("domain"):
        output = get_output_handler(ctx)
        output.display_error(
            "'fm ssl list' takes a bench, not a single domain: it reports every certificate the "
            f"bench holds. Use 'fm ssl list {address}'."
        )
        raise typer.Exit(1)

    if address == RESERVED_BENCH_NAME:
        _list_all_certificates(ctx)
    elif standalone:
        _list_external_certificates(ctx)
    else:
        address = prompt_for_bench_selection(address)

        if not address:
            output = get_output_handler(ctx)
            output.display_error("Benchname required in bench mode")
            output.data_raw(ctx.get_help())
            raise typer.Exit(1)

        _list_bench_certificates(ctx, address)


def _list_all_certificates(ctx: typer.Context):
    """List all SSL certificates (bench + external).

    Reports every bench it can read and exits nonzero if any bench it could not. Both halves
    matter: a listing that stopped at the first broken bench would hide every bench sorted after
    it, and a listing that exited 0 would tell a scheduled caller the report was complete when
    part of the estate was missing from it.
    """

    output = get_output_handler(ctx)

    if output.wants_structured_data:
        _print_all_certificates_data(ctx, output)
        return

    output.print("\n[fm.accent]═══ External Certificates ═══[/fm.accent]\n", emoji_code="")
    _list_external_certificates(ctx)

    output.print("\n[fm.accent]═══ Bench Certificates ═══[/fm.accent]\n", emoji_code="")

    benches = resolve_bench_targets(RESERVED_BENCH_NAME)

    if not benches:
        output.print("No benches found", emoji_code=":information_source:")
        return

    failed: list[str] = []

    for bench_name in benches:
        output.print(f"\n[bold]Bench: {bench_name}[/bold]", emoji_code="")
        try:
            _list_bench_certificates(ctx, bench_name)
        except Exception as e:
            output.display_error(f"{bench_name}: {e}")
            failed.append(bench_name)

    if failed:
        output.display_error(f"Could not list: {', '.join(failed)}")
        raise typer.Exit(1)


def _print_all_certificates_data(ctx: typer.Context, output) -> None:
    """The `all` selector's structured payload: external domains plus every bench, keyed by name,
    as ONE coherent document -- `{"external": [...], "benches": {name: {"domains": [...], "error":
    null}}}` -- rather than N unrelated JSON events. A bench fm cannot read is a row carrying its
    error, same as `_list_all_certificates`'s human report; the run still exits nonzero for it.
    """
    services_manager = ctx.obj["services"]
    external = _external_certificate_rows(services_manager, output)

    benches: dict[str, dict] = {}
    failed = False
    for bench_name in resolve_bench_targets(RESERVED_BENCH_NAME):
        try:
            benches[bench_name] = {"domains": _bench_certificate_data(ctx, bench_name), "error": None}
        except Exception as e:
            benches[bench_name] = {"domains": [], "error": str(e)}
            failed = True

    output.print_data({"external": external, "benches": benches})

    if failed:
        raise typer.Exit(1)
