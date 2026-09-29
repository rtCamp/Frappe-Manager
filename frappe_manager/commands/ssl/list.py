from typing import Annotated

import typer
from typer_examples import example

from frappe_manager.commands.arguments import BenchOnlyAllArgument, JsonResultOption
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
    json_result: JsonResultOption = False,
):
    """
    List SSL certificates with their expiry and renewal status.

    Lists one bench by default, including its domains that have no certificate yet. 'all' lists every bench and the external domains together, and --standalone lists only the external Docker project domains.

    A DNS-01 certificate's card carries a "dns provider" fact naming the \\[ssl.dns_providers] credential set it authenticates with, "default" for the unlabelled account, and "(missing)" when the label or the default account is not stored at either scope; every other domain's card omits that fact.
    """

    if json_result:
        get_output_handler(ctx).set_json_results()

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


def _survey_line(row: dict, width: int) -> str:
    """One plain, copy-safe summary line for the `all` survey: no rich markup (a domain is a copy
    target, and markup risks corrupting it same as a table cell would) and no per-domain card (an
    estate of benches must not turn into a page of cards).
    """
    parts = [f"{row['domain']:<{width}}", row["status"].replace("_", " ")]
    if row.get("days_until_expiry") is not None:
        parts.append(f"{row['days_until_expiry']}d left")
    if row.get("renewal"):
        parts.append(f"renewal {row['renewal']}")
    return "  ".join(parts)


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

    services_manager = ctx.obj["services"]

    # A survey, not a detail view: one plain line per domain straight off the same rows `--json`
    # reads, never a page of cards -- five benches must not become five card decks.
    external_rows = _external_certificate_rows(services_manager, output)
    output.data_raw("External:")
    width = max((len(row["domain"]) for row in external_rows), default=0)
    for row in external_rows:
        output.data_raw(_survey_line(row, width))

    benches = resolve_bench_targets(RESERVED_BENCH_NAME)

    if not benches:
        output.print("No benches found", emoji_code=":information_source:")
        return

    failed: list[str] = []

    for bench_name in benches:
        output.data_raw(f"{bench_name}:")
        try:
            rows = _bench_certificate_data(ctx, bench_name)
        except Exception as e:
            output.display_error(f"{bench_name}: {e}")
            failed.append(bench_name)
            continue
        width = max((len(row["domain"]) for row in rows), default=0)
        for row in rows:
            output.data_raw(_survey_line(row, width))

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
