"""Report a bench's APM state."""

import typer
from typer_examples import example

from frappe_manager.commands import check_bench_migration_required
from frappe_manager.commands.arguments import BenchNameArgument
from frappe_manager.output_manager import get_global_output_handler
from frappe_manager.site_manager.site import Bench
from frappe_manager.utils.site import host_bench_dir

from ._helpers import describe_newrelic


@example(
    "Show APM state for a bench",
    "{benchname}",
    benchname="mybench",
)
def status(
    ctx: typer.Context,
    benchname: BenchNameArgument = None,
):
    """
    Report which APM providers are configured on a bench and whether they are reporting.

    A provider needs both a stored license key and the enabled flag to report; either alone is shown as not reporting, because the web process falls back to plain Gunicorn.
    """

    services_manager = ctx.obj["services"]
    output = get_global_output_handler()
    check_bench_migration_required(benchname)

    bench = Bench.get_object(benchname, services_manager, output_handler=output)

    enabled, has_key = describe_newrelic(bench)
    # The agent config is user-owned once seeded, so its presence is worth reporting on its own:
    # it survives a disable, and it is what --force-config would overwrite.
    agent_config = host_bench_dir(bench.path) / "config" / "newrelic.ini"

    lines = [
        f"newrelic: {'reporting' if enabled and has_key else 'not reporting'}",
        f"  enabled:      {'yes' if enabled else 'no'}",
        f"  license key:  {'stored' if has_key else 'not set'}",
        f"  agent config: {'present (yours; --force-config overwrites)' if agent_config.is_file() else 'not seeded'}",
    ]

    if enabled and not has_key:
        lines.append("  NOTE: enabled without a key sends nothing; pass --license-key to fm telemetry enable.")

    # Plain lines, not a rich table: these are copy targets and rich cells truncate or fold
    # (commands/list.py:61).
    output.stop()
    for line in lines:
        typer.echo(line)
