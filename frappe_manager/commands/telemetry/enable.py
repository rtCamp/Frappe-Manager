"""Enable an APM provider on a bench."""

from typing import Annotated

import typer
from typer_examples import example

from frappe_manager import TelemetryProviderEnum
from frappe_manager.commands import check_bench_migration_required
from frappe_manager.commands.arguments import BenchNameArgument
from frappe_manager.output_manager import get_global_output_handler, spinner
from frappe_manager.site_manager.exceptions import BenchNotRunning
from frappe_manager.site_manager.site import Bench

from ._helpers import apply_newrelic, describe_newrelic, require_license_key


@example(
    "Start reporting to NewRelic",
    "{benchname} newrelic --license-key YOUR_INGEST_KEY",
    detail="Recreates the frappe container so the web process starts under the agent.",
    benchname="mybench",
)
@example(
    "Re-enable after a disable, reusing the stored key",
    "{benchname} newrelic",
    detail="Your edits to config/newrelic.ini are kept.",
    benchname="mybench",
)
@example(
    "Rotate the ingest key",
    "{benchname} newrelic --license-key NEW_KEY",
    benchname="mybench",
)
@example(
    "Throw away local agent tuning and restore fm's generated newrelic.ini",
    "{benchname} newrelic --force-config",
    benchname="mybench",
)
def enable(
    ctx: typer.Context,
    benchname: BenchNameArgument = None,
    provider: Annotated[
        TelemetryProviderEnum,
        typer.Argument(
            metavar="PROVIDER",
            help="APM backend to enable.",
            show_default=False,
        ),
    ] = TelemetryProviderEnum.newrelic,
    license_key: Annotated[
        str | None,
        typer.Option(
            "--license-key",
            help="Ingest license key for the provider. Required the first time; reused from bench_config.toml afterwards.",
            show_default=False,
        ),
    ] = None,
    force_config: Annotated[
        bool,
        typer.Option(
            "--force-config",
            help="Overwrite the provider's on-disk agent config with fm's generated one, discarding local edits.",
            show_default=False,
        ),
    ] = False,
):
    """
    Turn on APM reporting for a bench.

    The license key is recorded in bench_config.toml and passed to the agent through the container environment, so a key rotation is just this command again with the new key.

    The agent's own config file (config/newrelic.ini) is seeded once and then yours: fm never rewrites it, so a disable/enable cycle keeps your tuning. Use --force-config to take fm's generated version back.
    """

    services_manager = ctx.obj["services"]
    output = get_global_output_handler()
    check_bench_migration_required(benchname)

    bench = Bench.get_object(benchname, services_manager, output_handler=output)

    # Refused before any write: the recreate below needs a running bench, and a half-applied
    # enable (config saved, container untouched) is the disagreement this command exists to
    # avoid rather than create.
    if not bench.running:
        raise BenchNotRunning(bench_name=bench.name)

    require_license_key(bench, license_key)

    enabled, _ = describe_newrelic(bench)
    if enabled and not license_key and not force_config:
        # An already-enabled bench is this command's goal state already reached, so it reports
        # and stops instead of recreating the container for nothing. `fm update --newrelic` had
        # no such check and force-recreated the web container on every repeat invocation.
        output.print(f"{provider.value} is already enabled on {bench.name}")
        return

    with spinner(output, f"Enabling {provider.value}"):
        apply_newrelic(bench, output, enabled=True, license_key=license_key, force_config=force_config)

    output.print(f"Enabled {provider.value} on {bench.name}")
