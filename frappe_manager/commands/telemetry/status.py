import typer
from typer_examples import example

from frappe_manager.commands import check_bench_migration_required
from frappe_manager.commands.arguments import BenchNameArgument, JsonResultOption
from frappe_manager.output_manager import get_global_output_handler, railcard
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
    json_result: JsonResultOption = False,
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

    if json_result:
        output.print_data(
            {
                "bench": bench.name,
                "providers": {
                    "newrelic": {
                        "reporting": bool(enabled and has_key),
                        "enabled": bool(enabled),
                        "license_key_stored": bool(has_key),
                        "agent_config_present": agent_config.is_file(),
                    }
                },
            }
        )
        return

    # "reporting" is the answer to "is this bench reporting": BOTH enabled and a stored key
    # must hold, same pair `fm info` derives from, so the two views cannot drift apart.
    reporting = bool(enabled and has_key)
    card = railcard.Card(bench.name, "reporting" if reporting else "not reporting", reporting)
    card.fact("enabled", "yes" if enabled else "no")
    card.fact("license key", "stored" if has_key else "not set")
    card.fact(
        "agent config",
        "present (yours; --force-config overwrites)" if agent_config.is_file() else "not seeded",
    )
    if enabled and not has_key:
        card.fact("note", "enabled without a key sends nothing; pass --license-key to fm telemetry enable.")

    output.print_data(card.render())
