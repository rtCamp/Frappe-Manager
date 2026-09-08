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
from frappe_manager.site_manager.bench_config import (
    recognised_bench_config_keys,
    recognised_deploy_state_keys,
    recognised_ssl_keys,
)
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


def _unrecognised_keys(overlay: dict) -> list[str]:
    """Overlay keys ``BenchConfig.import_from_toml`` would never look at: an unrecognised
    top-level key, or one inside ``[deploy_state]``/``[ssl]`` if the overlay sets that table.

    Checked against `bench_config`'s own recognised-key derivation (not a second list), and
    checked HERE rather than left to the eventual bench-config load, because this seam serves one
    interactive command with an operator present to fix a typo before it lands on disk, unlike a
    later `fm list`/`fm bake`/`fm switch` run that must not fail loudly over one bad file.
    """
    unrecognised = sorted(set(overlay) - recognised_bench_config_keys())
    deploy_state = overlay.get("deploy_state")
    if isinstance(deploy_state, dict):
        unrecognised += sorted(f"deploy_state.{key}" for key in set(deploy_state) - recognised_deploy_state_keys())
    ssl = overlay.get("ssl")
    if isinstance(ssl, dict):
        unrecognised += sorted(f"ssl.{key}" for key in set(ssl) - recognised_ssl_keys())
    return unrecognised


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
    """Return ``base_toml`` with each ``--config`` overlay deep-merged in order."""
    doc = tomlkit.parse(base_toml)
    for value in configs:
        text = resolve_source(value)
        try:
            overlay = tomlkit.parse(text).unwrap()
        except Exception as e:
            raise ConfigOverlayError(f"Could not parse --config value as TOML ({value!r}): {e}") from e
        if not isinstance(overlay, dict):
            raise ConfigOverlayError(f"--config value is not a TOML table ({value!r})")
        unrecognised = _unrecognised_keys(overlay)
        if unrecognised:
            raise ConfigOverlayError(
                f"--config value ({value!r}) has unrecognised key(s) {', '.join(unrecognised)}; "
                "check for a typo."
            )
        deep_merge(doc, overlay)
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
