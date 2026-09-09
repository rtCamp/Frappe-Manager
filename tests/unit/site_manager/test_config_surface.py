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
import inspect
import re
import textwrap
from functools import lru_cache
from pathlib import Path

import pytest

from frappe_manager.metadata_manager import FMConfigManager, recognised_fm_config_keys
from frappe_manager.site_manager.bench_config import (
    BenchConfig,
    recognised_bench_config_keys,
    recognised_deploy_state_keys,
    recognised_ssl_keys,
)

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
    # `MigrationState.migrated_to`/`last_migration_date` used to be exempted here (raw-TOML read
    # in bench_migration_state.py, never a `.field` access) -- now mutated via plain attribute
    # assignment (`config.migration_state.migrated_to = ...`) in bench_migration_state.py and
    # bench_orchestrator.py instead of being reconstructed, so the AST scan finds them like any
    # other field and neither needs the exemption any more.
    # SSLConfig, the one entry this list used to carry, is gone: it was never constructed, and while
    # it existed its `dns_challenge_providers` field shared a name with the live FMConfigManager one,
    # so a name-keyed reader scan could not tell them apart and reported the dead field as read.
}


def _model_fields() -> list[tuple[str, str]]:
    """Every pydantic field a config model declares, as (class, field).

    AST rather than a text scan, for the same reason ``_attribute_names_read`` is: a regex sweeping
    from ``class X`` to the next ``class `` swallows any module-level function declared in between,
    and that function's annotated parameters then read as fields of the preceding model. The result
    is a phantom entry this file demands a justification for. A declaration is an ``AnnAssign`` in a
    ``ClassDef`` body and nothing else is, so ask the tree.
    """
    out: list[tuple[str, str]] = []
    for source in CONFIG_SOURCES:
        tree = ast.parse(source.read_text())
        for node in ast.walk(tree):
            if not isinstance(node, ast.ClassDef):
                continue
            if not any(isinstance(base, ast.Name) and base.id == "BaseModel" for base in node.bases):
                continue
            for stmt in node.body:
                if not isinstance(stmt, ast.AnnAssign) or not isinstance(stmt.target, ast.Name):
                    continue
                field = stmt.target.id
                if re.fullmatch(r"[a-z_][a-z0-9_]*", field):
                    out.append((node.name, field))
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


def _keys_read_from(receiver: str, source: str) -> frozenset[str]:
    """Every string-literal key `receiver.get("key", ...)`, `receiver["key"]`, or `"key" in
    receiver` names in `source`, found by walking the AST rather than scanning text -- the same
    reasoning as `_attribute_names_read` above: a comment mentioning a key must not count as a
    read, and a text scan cannot tell `data.get(...)` from `ssl_data.get(...)` apart, which is
    exactly the distinction between a bench_config.toml top-level key and a `[ssl]` one.
    """
    tree = ast.parse(source)
    keys: set[str] = set()
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "get"
            and isinstance(node.func.value, ast.Name)
            and node.func.value.id == receiver
            and node.args
            and isinstance(node.args[0], ast.Constant)
            and isinstance(node.args[0].value, str)
        ):
            keys.add(node.args[0].value)
        elif (
            isinstance(node, ast.Subscript)
            and isinstance(node.value, ast.Name)
            and node.value.id == receiver
            and isinstance(node.slice, ast.Constant)
            and isinstance(node.slice.value, str)
        ):
            keys.add(node.slice.value)
        elif isinstance(node, ast.Compare):
            left = node.left
            for op, comparator in zip(node.ops, node.comparators, strict=True):
                if (
                    isinstance(op, (ast.In, ast.NotIn))
                    and isinstance(comparator, ast.Name)
                    and comparator.id == receiver
                    and isinstance(left, ast.Constant)
                    and isinstance(left.value, str)
                ):
                    keys.add(left.value)
                left = comparator
    return frozenset(keys)


def test_recognised_bench_config_keys_covers_every_key_the_reader_touches():
    """Guards the derivation this fix relies on: if `collect_from_data` starts reading a new
    top-level key that `recognised_bench_config_keys()` does not know about, the loader would warn
    about the very key it just consumed. That drift is the failure mode of this whole design, so
    it gets its own test rather than trusting the two to be kept in sync by hand.

    Scans `BenchConfig.collect_from_data`, not `import_from_toml`: the latter is now a thin
    wrapper (parse, delegate, then decide what to do with the two lists collect_from_data
    returns), and every `data.get(...)`/`data[...]` read that matters lives in the method that
    actually does it.
    """
    source = textwrap.dedent(inspect.getsource(BenchConfig.collect_from_data))
    keys_read = _keys_read_from("data", source)

    assert keys_read, "the AST scan found nothing, so this test is not testing anything"
    missing = keys_read - recognised_bench_config_keys()
    assert not missing, (
        f"collect_from_data reads {sorted(missing)} but recognised_bench_config_keys() does not "
        "know them -- add the spelling there, next to the other hand-added aliases."
    )


def test_recognised_ssl_keys_covers_every_key_the_reader_touches():
    """The third hand-read table, same drift risk as `[deploy_state]` above: `[ssl]` is read by
    hand (`ssl_data.get(...)`), not splatted into a model, so a key `collect_from_data` starts
    reading there needs the identical guard or the loader would warn about the very key it just
    consumed."""
    source = textwrap.dedent(inspect.getsource(BenchConfig.collect_from_data))
    keys_read = _keys_read_from("ssl_data", source)

    assert keys_read, "the AST scan found nothing, so this test is not testing anything"
    missing = keys_read - recognised_ssl_keys()
    assert not missing, (
        f"collect_from_data reads {sorted(missing)} from [ssl] but recognised_ssl_keys() does not know them."
    )


def test_recognised_deploy_state_keys_covers_every_key_the_reader_touches():
    source = textwrap.dedent(inspect.getsource(BenchConfig.collect_from_data))
    keys_read = _keys_read_from("deploy_state_data", source)

    assert keys_read, "the AST scan found nothing, so this test is not testing anything"
    missing = keys_read - recognised_deploy_state_keys()
    assert not missing, (
        f"collect_from_data reads {sorted(missing)} from [deploy_state] but "
        "recognised_deploy_state_keys() does not know them."
    )


def test_recognised_fm_config_keys_covers_every_key_the_reader_touches():
    source = textwrap.dedent(inspect.getsource(FMConfigManager.import_from_toml))
    keys_read = _keys_read_from("data", source)

    assert keys_read, "the AST scan found nothing, so this test is not testing anything"
    missing = keys_read - recognised_fm_config_keys()
    assert not missing, (
        f"FMConfigManager.import_from_toml reads {sorted(missing)} but "
        "recognised_fm_config_keys() does not know them."
    )
