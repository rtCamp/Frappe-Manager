"""Bench-config overlays for ``fm bake`` and ``fm create`` (#323).

Each ``--config`` value is a **path to a TOML file** or **inline TOML content**;
multiple ``--config`` flags merge **left-to-right (later wins, deep merge)** into
the bench's ``bench_config.toml`` before the bake. This gives advanced config
(`[build]`/`[fc]`, hooks, ...) a first-class surface, including
CI configs committed in an app repo, without hand-editing the server-side toml.

The overlay is **persisted**: it is merged into ``bench_config.toml`` (the single
source of truth), so the bench config reflects exactly what was baked.
Secrets should be written as ``${ENV_VAR}`` refs (resolved at use-time by the
registry layer), so they never land resolved in the file.
"""

from pathlib import Path

import tomlkit

from frappe_manager.exceptions import FrappeManagerException
from frappe_manager.site_manager.bench_config import BenchConfig
from frappe_manager.utils import toml_document


class ConfigOverlayError(FrappeManagerException):
    """Raised when a --config value cannot be read or parsed as TOML."""


def resolve_source(value: str) -> str:
    """TOML text for a ``--config`` value: the file's contents when it is a path to
    an existing file, else the value itself (treated as inline TOML)."""
    try:
        candidate = Path(value).expanduser()
        if candidate.is_file():
            return candidate.read_text()
    except OSError:
        pass
    return value


def _refused_keys(doc) -> list[str]:
    """Every key `BenchConfig` would not recognise if `doc` were loaded for real right now.

    Built by running `doc` through `BenchConfig.collect_from_data`, the exact method `import_from_toml`
    calls on a real read, rather than checking key names against the recognised-key sets by hand:
    a stray inside `[switch]` (or any other splatted table) is retained (`extra="allow"`) rather
    than rejected, so a shallow top-level-plus-two-tables check misses it entirely. Running the
    real reader is what makes the overlay refusal and the on-disk warning agree by construction --
    they cannot name a different set of keys for the same document, because nothing here recomputes
    that set a second way.

    Every field `BenchConfig` requires with no default (`name`, `developer_mode`, `admin_tools`,
    `environment_type`, `root_path`) is placeholder-filled when absent, not just `root_path`/
    `name`: a `create.py` seed document may not carry any of them yet (they are filled in from
    flags afterward), and only the KEY SET matters here, not the values. A value that fails
    validation for an unrelated reason (a bad enum, wrong type) is not this check's job to report
    -- the real load, once the bench is created or baked, raises its own clearer error for that.

    `collect_from_data` is the real reader, not a pure key-name comparison, so a table-shaped key
    holding the wrong shape can fail while *building* the model rather than while validating it:
    `data["sites"].items()`, `ssl_data.get(...)` and `dict(data["switch"])` all assume a dict where
    a hostile overlay can hand them a scalar, a list, or a bare string instead. That failure is
    NOT swallowed here the way `ValidationError` is: `ValidationError` means the shape was fine and
    a recognised field just got a bad value, which the real load reports more clearly later, but a
    crash while building means this function cannot determine the unknown-key set at all -- there
    is nothing safe to defer to, so it is `merge_overlays`'s job to turn that into a refusal instead
    of a traceback (see its docstring for the exception inventory).
    """
    from pydantic import ValidationError

    candidate = dict(doc.unwrap()) if hasattr(doc, "unwrap") else dict(doc)
    candidate.setdefault("name", "overlay-check")
    candidate.setdefault("developer_mode", False)
    candidate.setdefault("admin_tools", False)
    candidate.setdefault("environment", "dev")
    candidate.setdefault("root_path", "/")
    try:
        _config, unknown, _stale = BenchConfig.collect_from_data(candidate)
    except ValidationError:
        return []
    return unknown


def deep_merge(base, overlay: dict) -> None:
    """Recursively merge plain-dict ``overlay`` into the tomlkit ``base`` container.
    Later wins; nested tables merge; scalars and lists overwrite."""
    for key, value in overlay.items():
        existing = base.get(key) if hasattr(base, "get") else None
        if isinstance(value, dict) and hasattr(existing, "items"):
            deep_merge(base[key], value)
        else:
            base[key] = value


def merge_overlays(base_toml: str, configs: list[str]) -> str:
    """Return ``base_toml`` with each ``--config`` overlay deep-merged in order.

    The refusal check runs `_refused_keys` on the merged document, but a merged document also
    contains everything the BASE already carried -- and a pre-migration bench (1.0.0 is
    unreleased, so this is every bench right now) still has plenty of top-level keys and whole
    tables `BenchConfig` does not recognise (see the migration-gate note at `certificate.py:19-23`
    for why a real read tolerates them). Checking the whole merged document blamed those on
    whichever `--config` value happened to be merged last, refusing a bake this seam was supposed
    to allow and naming a key the operator never typed. Severity is by ORIGIN: a key this
    invocation's overlay introduced is refused, a key that was already sitting in the file is only
    ever warned about (by the plain read this check is not), so the refusal here is the SET
    DIFFERENCE between what's unknown after this overlay and what was already unknown before it.

    Two edge cases fall out of comparing key SETS rather than table identity or values:

    - An overlay that re-sets an already-unknown key to a new value (e.g. the base already has a
      stray top-level `alias_domains` and this overlay writes `alias_domains = [...]` again) is
      NOT refused. The key's name was already unrecognised before this overlay touched it; only
      its value changed, and this check only ever looked at names. Origin tracking here is about
      whether the NAME is new, not who most recently supplied the value -- a plain read of the
      base file would already retain-and-warn about that same name regardless of who last wrote
      it, so refusing it here just because this invocation happened to repeat it would make the
      refusal stricter than the warning it exists to agree with.
    - An overlay that introduces a stray inside a table that already had a different stray (e.g.
      base has `[switch]\\nold_typo = true` and the overlay adds `[switch]\\nnew_typo = true`) DOES
      get refused, naming only `switch.new_typo`. `_refused_keys` reports dotted paths per field,
      not per table, so the pre-existing `switch.old_typo` and the newly-introduced
      `switch.new_typo` are distinct set members; the diff isolates exactly the one this overlay
      actually added.

    Computed once against the pristine base rather than re-diffed before every overlay: a
    successful overlay step can only ever change an unknown key's VALUE, never its key set (adding
    a new key name is exactly what gets refused), so the unknown-key set is invariant across every
    overlay that has already passed, and the pristine base is that invariant set.

    `_refused_keys` runs the real reader, `BenchConfig.collect_from_data`, which does
    `data["sites"].items()`, `ssl_data.get(...)`, `dict(data["switch"])` and the like while
    building the model -- all of which assume a dict where a hostile overlay (`ssl = 5`,
    `switch = "x"`, a list where a table belongs, ...) can hand a scalar, list, or string instead.
    Tried directly against the reader across every splatted table (scalar, list, string, empty
    table, and the same wrong shape nested a level deeper inside `[sites."x"]`/`[ssl]`), the
    failures were exactly `AttributeError` (`.get`/`.items` on a non-dict), `TypeError` (a
    non-iterable, or a single-item iterable fed to `dict(...)`), and `ValueError`
    (`dict("some_string")` iterates characters instead of raising `TypeError`). Nothing else
    turned up, so nothing else is caught: nothing here (a `KeyError`/`IndexError`/anything else)
    would mean the real reader has a bug of its own, and should still surface as one rather than be
    reported as a rejected `--config` value.
    """
    doc = tomlkit.parse(base_toml)
    try:
        base_unknown = set(_refused_keys(doc))
    except (AttributeError, TypeError, ValueError) as e:
        raise ConfigOverlayError(f"Bench config could not be read to validate --config overlays: {e}") from e
    for value in configs:
        text = resolve_source(value)
        try:
            overlay = tomlkit.parse(text).unwrap()
        except Exception as e:
            raise ConfigOverlayError(f"Could not parse --config value as TOML ({value!r}): {e}") from e
        if not isinstance(overlay, dict):
            raise ConfigOverlayError(f"--config value is not a TOML table ({value!r})")
        deep_merge(doc, overlay)
        try:
            introduced = sorted(set(_refused_keys(doc)) - base_unknown)
        except (AttributeError, TypeError, ValueError) as e:
            raise ConfigOverlayError(f"--config value ({value!r}) produced an invalid bench config: {e}") from e
        if introduced:
            raise ConfigOverlayError(
                f"--config value ({value!r}) has unrecognised key(s) {', '.join(introduced)}; "
                "check for a typo."
            )
    return tomlkit.dumps(doc)


def apply_config_overlays(bench_config_path: Path, configs: list[str]) -> None:
    """Merge each ``--config`` overlay into ``bench_config_path`` in order (persisted)."""
    if not configs:
        return
    if not bench_config_path.is_file():
        raise ConfigOverlayError(f"Bench config not found for --config overlay: {bench_config_path}")
    merged = merge_overlays(bench_config_path.read_text(), configs)
    # Atomic and 0600: this file holds the bench's basic-auth password and any token an overlay
    # brought with it, and a plain write_text left it at the process umask.
    toml_document.save_text(bench_config_path, merged)
