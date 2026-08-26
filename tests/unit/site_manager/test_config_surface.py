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

import ast
import re
from functools import lru_cache
from pathlib import Path

import pytest

# Every module declaring a table a bench_config.toml can set. `DNSProviderConfig` lives in
# ssl_manager because bench_config imports FMConfigManager from metadata_manager, so metadata_manager
# cannot import from bench_config, and the global config now declares labelled providers too. A model
# that moves out of this list stops being checked, which is how a relocation quietly shrinks the
# guard: keep it whole-file, not per-class.
CONFIG_SOURCES = (
    Path("frappe_manager/site_manager/bench_config.py"),
    Path("frappe_manager/ssl_manager/dns_provider.py"),
)
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
    # SSLConfig, the one entry this list used to carry, is gone: it was never constructed, and while
    # it existed its `dns_challenge_providers` field shared a name with the live FMConfigManager one,
    # so a name-keyed reader scan could not tell them apart and reported the dead field as read.
}


def _model_fields() -> list[tuple[str, str]]:
    out: list[tuple[str, str]] = []
    for source in CONFIG_SOURCES:
        src = source.read_text()
        for name, body in re.findall(r"class (\w+)\(BaseModel\):(.*?)(?=\nclass |\Z)", src, re.S):
            for field in re.findall(r"^    ([a-z_][a-z0-9_]*)\s*:", body, re.M):
                out.append((name, field))
    return out


@lru_cache(maxsize=1)
def _attribute_names_read() -> frozenset[str]:
    """Every attribute name the package actually accesses, via AST rather than text.

    A line-based ``\\.field`` search counts prose: a comment or a log message naming the TOML path
    ``[ssl.dns_challenge_providers.cloudflare]`` reads as an attribute access and silently retires an
    allowlist entry, which is the one failure this file cannot afford. Parsing also drops the need to
    exclude declaration lines, since a declaration is an AnnAssign and never an Attribute.
    """
    names: set[str] = set()
    for path in PACKAGE.rglob("*.py"):
        try:
            tree = ast.parse(path.read_text())
        except SyntaxError:  # a template or a partial file is not a reader
            continue
        names.update(n.attr for n in ast.walk(tree) if isinstance(n, ast.Attribute))
    return frozenset(names)


def _is_read(field: str) -> bool:
    """True when some ``.field`` attribute access exists anywhere in the package."""
    return field in _attribute_names_read()


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
