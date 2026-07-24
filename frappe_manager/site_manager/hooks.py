"""Shared deploy/build hook helpers (#323).

Hooks are user scripts run at deploy phases (switch: around the restart) and
build phases (provision: around python-install / bench-build). A hook value is
inline shell or a path to a ``.sh``/``.py`` file (read + inlined); it runs as
``set -e`` + exported env + content, so no exec env passthrough is needed.

Env = caller-supplied core vars + every scalar field of an optional pydantic
``config`` upper-cased (the nested ``hooks`` field excluded), mirroring fmd's
``get_script_env``.
"""

import json
import shlex
from pathlib import Path


def resolve_hook_content(value: str) -> str:
    """Inline script text, or the file contents when ``value`` is a path to an
    existing ``.sh``/``.py`` script (mirrors fmd's hook resolution)."""
    stripped = value.strip()
    looks_path = stripped.startswith(("/", "./", "~/")) or Path(stripped).suffix in (".sh", ".py")
    if looks_path:
        candidate = Path(stripped).expanduser()
        if candidate.exists():
            return candidate.read_text()
    return value


def hook_env(core: dict[str, str], config=None) -> dict[str, str]:
    """Build the hook environment: ``core`` vars + every scalar field of
    ``config`` (a pydantic model) upper-cased (the ``hooks`` field and None
    dropped, bool lowercased, dict/list JSON-encoded). ``config=None`` yields
    just ``core``."""
    env: dict[str, str] = dict(core)
    data = config.model_dump() if config is not None else {}
    for name, value in data.items():
        if name == "hooks" or value is None:
            continue
        if isinstance(value, bool):
            env[name.upper()] = str(value).lower()
        elif isinstance(value, (dict, list)):
            env[name.upper()] = json.dumps(value)
        else:
            env[name.upper()] = str(value)
    return env


def hook_script(value: str, env: dict[str, str]) -> str:
    """``set -e`` + ``export``ed env + resolved content, ready to run under bash."""
    exports = "".join(f"export {k}={shlex.quote(v)}\n" for k, v in env.items())
    return "set -e\n" + exports + resolve_hook_content(value)


def app_has_build_hooks(hooks) -> bool:
    """True when any per-app build hook (container or host) is set on ``hooks``."""
    if hooks is None:
        return False
    fields = ("before_deps", "after_deps", "before_build", "after_build")
    if any(getattr(hooks, name, None) for name in fields):
        return True
    host = getattr(hooks, "host", None)
    return host is not None and any(getattr(host, name, None) for name in fields)


def switch_has_hooks(hooks) -> bool:
    """True when any switch-phase hook (container or host) is set on ``hooks``."""
    if hooks is None:
        return False
    fields = ("before_restart", "after_restart", "before_migrate", "after_migrate")
    if any(getattr(hooks, name, None) for name in fields):
        return True
    host = getattr(hooks, "host", None)
    return host is not None and any(getattr(host, name, None) for name in fields)
