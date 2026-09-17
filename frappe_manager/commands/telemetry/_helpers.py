"""Shared state transition behind `fm telemetry enable` / `fm telemetry disable`.

Both verbs are the same three writes in a different order of intent, so they live together:
record the wanted state in `bench_config.toml`, re-render the compose (the frappe service's
environment is what the web wrapper reads at boot), refresh the supervisor-side files, then
recreate the one container that serves the web process.

The recreate is not optional and not a restart: a container keeps the environment it was
created with, so `docker restart` would re-run the old `NEWRELIC_ENABLED` and report success
while changing nothing.
"""

import typer

from frappe_manager.site_manager.bench_config import NewRelicConfig, TelemetryConfig
from frappe_manager.site_manager.site import Bench


def newrelic_state(bench: Bench) -> NewRelicConfig | None:
    """The bench's recorded NewRelic config, or None when it has never been configured."""
    return bench.bench_config.get_telemetry_config("newrelic")


def describe_newrelic(bench: Bench) -> tuple[bool, bool]:
    """`(enabled, has_key)` — the two halves that must BOTH hold for the agent to run.

    Kept as a pair because "enabled with no key" is a real recorded state that does not
    monitor anything: the exporter only emits the env vars when both are set, so the wrapper
    falls back to plain gunicorn. Reporting it as simply "enabled" would be a lie.
    """
    state = newrelic_state(bench)
    return bool(state and state.enabled), bool(state and state.license_key)


def apply_newrelic(
    bench: Bench,
    output,
    *,
    enabled: bool,
    license_key: str | None = None,
    force_config: bool = False,
) -> None:
    """Write the wanted NewRelic state through to the running web process.

    ``force_config`` only reaches ``newrelic.ini``: the generated file is seeded once and then
    belongs to the operator (agent sampling and tracer thresholds are per-bench tuning), so it
    is preserved across an off/on cycle unless this asks for the template back.
    """
    telemetry = bench.bench_config.telemetry or TelemetryConfig()
    newrelic_config = telemetry.newrelic or NewRelicConfig()

    newrelic_config.enabled = enabled
    if license_key is not None:
        newrelic_config.license_key = license_key

    telemetry.newrelic = newrelic_config
    bench.bench_config.telemetry = telemetry

    # Saved BEFORE the container work, unlike the path this replaced: `fm update` deferred the
    # write to a terminal save, so a failure in the recreate below exited with the agent state
    # already live in the compose file and the container, and bench_config.toml still claiming
    # the old one.
    bench.save_bench_config()

    bench.generate_compose(bench.bench_config.export_to_compose_inputs())
    bench.supervisor.setup_newrelic(bench.path, force=force_config)

    output.change_head("Recreating the frappe container")
    bench.docker_client.compose.up(services=["frappe"], detach=True, force_recreate=True)


def require_license_key(bench: Bench, license_key: str | None) -> None:
    """Refuse an enable that would record a monitored-but-keyless bench.

    Raised before anything is written. With no key there is nothing to authenticate to, so
    the exporter emits no env vars and the wrapper runs plain gunicorn: the bench would report
    itself enabled and send nothing.
    """
    _, has_key = describe_newrelic(bench)
    if not license_key and not has_key:
        raise typer.BadParameter("--license-key is required the first time you enable NewRelic.")
