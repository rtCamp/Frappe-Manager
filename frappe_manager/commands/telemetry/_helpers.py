"""Shared state transition behind `fm telemetry enable` / `fm telemetry disable`.

Both verbs are the same three writes in a different order of intent, so they live together:
record the wanted state in `bench_config.toml`, re-render the compose (the frappe service's
environment is what the web wrapper reads at boot), refresh the supervisor-side files, then
recreate the frappe container and converge the rest of the stack -- web tier and workers both
-- to the same recorded deployment.

The frappe recreate is not optional and not a restart: a container keeps the environment it
was created with, so `docker restart` would re-run the old `NEWRELIC_ENABLED` and report
success while changing nothing. The rest of the stack is converged rather than left alone:
recreating only frappe would otherwise leave a drifted nginx/socketio/schedule/worker
container behind with no signal that it happened.
"""

import typer

from frappe_manager.site_manager.bench_config import NewRelicConfig, TelemetryConfig
from frappe_manager.site_manager.modules.telemetry_state import telemetry_state
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
    # Delegates: `fm info` reports the same pair, and two derivations of "is this bench reporting"
    # would drift the moment one of them learned about a second provider.
    return telemetry_state(bench.bench_config, "newrelic")


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

    # Saved BEFORE the container work: a failed recreate below must not leave bench_config.toml
    # claiming a state that compose and the running container disagree with.
    bench.save_bench_config()
    bench.generate_compose(bench.bench_config.export_to_compose_inputs())
    bench.supervisor.setup_newrelic(bench.path, force=force_config)

    output.change_head("Recreating the frappe container")
    # Force-recreate ONLY frappe: `docker restart` keeps the OLD environment a container was
    # created with, so NEWRELIC_ENABLED would never actually change (see the module docstring).
    bench.docker_client.compose.up(services=["frappe"], detach=True, force_recreate=True)
    # Converge the rest of the stack too, web tier and workers both: `generate_compose` above
    # just re-pinned every service to `deployments.current`, and recreating frappe alone while
    # leaving a drifted nginx/socketio/schedule/worker container behind is wrong on its own
    # terms, telemetry or not. Not forced: `compose up` already recreates any container whose
    # config no longer matches the file, so this only touches what actually drifted.
    bench.docker_client.compose.up(services=[], detach=True, pull="never")
    if bench.workers.compose_file_manager.compose_path.exists():
        from frappe_manager.site_manager.modules.deploy_orchestrator import pin_workers_to_current_deployment

        pin_workers_to_current_deployment(bench.workers, bench.bench_config.site_names)
        bench.workers.docker_client.compose.up(services=[], detach=True, pull="never", force_recreate=False)


def require_license_key(bench: Bench, license_key: str | None) -> None:
    """Refuse an enable that would record a monitored-but-keyless bench.

    Raised before anything is written. With no key there is nothing to authenticate to, so
    the exporter emits no env vars and the wrapper runs plain gunicorn: the bench would report
    itself enabled and send nothing.
    """
    _, has_key = describe_newrelic(bench)
    if not license_key and not has_key:
        raise typer.BadParameter("--license-key is required the first time you enable NewRelic.")
