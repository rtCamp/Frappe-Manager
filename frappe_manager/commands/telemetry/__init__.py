"""APM / monitoring commands module.

Verb first, provider as a positional (`fm telemetry enable BENCH newrelic`), matching
`fm services start SERVICE` rather than nesting a sub-app per backend: adding a provider is
then a new `TelemetryProviderEnum` member, not a new command tree.

This owns what `fm update --newrelic/--newrelic-license-key` used to. Those flags were the only
ones in that command carrying a credential -- they needed its single hoisted pre-check -- and an
APM agent with a lifecycle, an ingest key and a user-editable config file is a subsystem, not a
setting, so it follows `fm tools` out of `fm update`.
"""

import typer
from typer_examples import install

telemetry_app = typer.Typer(no_args_is_help=True, rich_markup_mode="rich")

install(telemetry_app)

from .disable import disable
from .enable import enable
from .status import status

telemetry_app.command(name="enable")(enable)
telemetry_app.command(name="disable")(disable)
telemetry_app.command(name="status")(status)

__all__ = ["telemetry_app"]
