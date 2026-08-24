"""Every config field a bench_config.toml can set must be read by something.

This is the check that would have caught, mechanically, a run of things found by
hand instead: the `[switch]` worker-care keys that were defined and consumed by
nothing, `[deploy].fm_source` and `[deploy].benches_root` (shipped, documented as
"accepted and ignored"), `[switch].search_replace`, and `[registry].distribution`.

A dead config key is worse than dead code. It is a documented promise: someone
sets it, nothing happens, and there is no error to tell them so.

The allowlist below is the point of the design. A field that nothing reads
statically has to be justified here, so adding one is a deliberate act rather
than an oversight.
"""

import re
from pathlib import Path

import pytest

CONFIG_SOURCE = Path("frappe_manager/site_manager/bench_config.py")
PACKAGE = Path("frappe_manager")

# field -> why no static `.field` read exists. Anything not listed must have one.
DYNAMIC_OR_INDIRECT: dict[str, str] = {
    # Read through getattr by hook name: hooks.py:60,71 and deploy_orchestrator.py:284.
    "SwitchHookScripts.before_restart": "getattr(hooks, name)",
    "SwitchHookScripts.after_restart": "getattr(hooks, name)",
    "SwitchHookScripts.before_migrate": "getattr(hooks, name)",
    "SwitchHookScripts.after_migrate": "getattr(hooks, name)",
    # The migration gate reads these from raw TOML on purpose, so it stays
    # schema-tolerant enough to run against a config it is about to migrate
    # (see bench_migration_state, line 48).
    "MigrationState.migrated_to": "raw TOML read in bench_migration_state.py",
    "MigrationState.last_migration_date": "raw TOML read in bench_migration_state.py",
    # SUSPECT. Not verified as reachable; left failing-safe here rather than
    # quietly deleted, because it is secret-bearing and user-visible.
    # SSLConfig.dns_challenge_providers: no reader found anywhere, yet
    # docs/reference/configuration.md describes per-certificate api_token/api_key
    # as taking precedence over it.
    "SSLConfig.dns_challenge_providers": "SUSPECT: no reader found",
}


def _model_fields() -> list[tuple[str, str]]:
    src = CONFIG_SOURCE.read_text()
    out: list[tuple[str, str]] = []
    for name, body in re.findall(r"class (\w+)\(BaseModel\):(.*?)(?=\nclass |\Z)", src, re.S):
        for field in re.findall(r"^    ([a-z_][a-z0-9_]*)\s*:", body, re.M):
            out.append((name, field))
    return out


def _is_read(field: str) -> bool:
    """True when some ``.field`` attribute access exists outside its own declaration.

    Scanned in-process rather than by shelling out to grep: no PATH dependency, and
    the declaration lines have to be excluded anyway or every field looks read.
    """
    attr = re.compile(rf"\.{field}\b")
    decl = re.compile(rf"^\s*{field}\s*:")
    for path in PACKAGE.rglob("*.py"):
        for line in path.read_text().splitlines():
            if attr.search(line) and not decl.match(line):
                return True
    return False


def test_every_config_field_is_read_by_something():
    fields = _model_fields()
    assert fields, "the model scan found nothing, so this test is not testing anything"

    unread = [f"{cls}.{field}" for cls, field in fields if not _is_read(field)]

    assert sorted(unread) == sorted(DYNAMIC_OR_INDIRECT), (
        "Config surface drift. A field here that nothing reads is a promise fm does not keep: "
        "either wire it up, delete it (add the key to REMOVED_CONFIG_KEYS so stale files still "
        "load and the migration strips them), or document why it is read indirectly by adding it "
        "to DYNAMIC_OR_INDIRECT."
    )


@pytest.mark.parametrize("entry", sorted(DYNAMIC_OR_INDIRECT))
def test_allowlisted_fields_still_exist(entry):
    """An allowlist entry for a field that is gone is stale and hides the next one."""
    cls, field = entry.rsplit(".", 1)

    assert (cls, field) in _model_fields(), f"{entry} is allowlisted but no longer defined"
