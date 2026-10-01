"""Reading whether a bench is reporting telemetry.

Lives here rather than in `commands/telemetry/` so `fm info` can report it without a command
importing a command: data leaving the host to a third party is headline-relevant, and the only
code that knew was behind the CLI verb that configures it.
"""

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from frappe_manager.site_manager.bench_config import BenchConfig


def telemetry_state(bench_config: "BenchConfig", provider: str = "newrelic") -> tuple[bool, bool]:
    """``(enabled, has_key)`` — the two halves that must BOTH hold for the agent to run.

    Kept as a pair because "enabled with no key" is a real recorded state that monitors nothing:
    the exporter emits the env vars only when both are set, so the wrapper falls back to plain
    gunicorn. Reporting that as "enabled" would be a lie.
    """
    # Reporting must survive a config too old or too damaged to answer: `fm info` exists to tell
    # you what is wrong with a bench, so an optional section must never be what stops it.
    reader = getattr(bench_config, "get_telemetry_config", None)
    if reader is None:
        return False, False
    try:
        state = reader(provider)
    except Exception:
        return False, False
    return bool(state and getattr(state, "enabled", False)), bool(state and getattr(state, "license_key", None))
