"""Shared deploy/build hook helpers (#323).

Hooks are user scripts run at deploy phases (switch: around the restart) and
build phases (provision: around python-install / bench-build). A hook value is
inline shell or a path to a ``.sh``/``.py`` file (read + inlined); it runs as
``set -e`` + exported env + content, so no exec env passthrough is needed.

Env = caller-supplied core vars + every scalar ``[deploy]`` field upper-cased
(the hook script fields themselves excluded), mirroring fmd's ``get_script_env``.
"""

import json
import shlex
from pathlib import Path

HOOK_FIELDS = frozenset(
    {
        "before_restart",
        "after_restart",
        "host_before_restart",
        "host_after_restart",
        "before_bench_build",
        "after_bench_build",
        "host_before_bench_build",
        "host_after_bench_build",
        "before_python_install",
        "after_python_install",
        "host_before_python_install",
        "host_after_python_install",
    }
)

# Build-phase hooks (run in provision, shared by create + bake), in fmd's order.
BUILD_HOOK_FIELDS = (
    "host_before_python_install",
    "before_python_install",
    "after_python_install",
    "host_after_python_install",
    "host_before_bench_build",
    "before_bench_build",
    "after_bench_build",
    "host_after_bench_build",
)


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


def hook_env(deploy_config, core: dict[str, str]) -> dict[str, str]:
    """Build the hook environment: ``core`` vars + every scalar ``[deploy]`` field
    upper-cased (hook script fields excluded, None dropped, bool lowercased,
    dict/list JSON-encoded)."""
    env: dict[str, str] = dict(core)
    data = deploy_config.model_dump() if deploy_config else {}
    for name, value in data.items():
        if name in HOOK_FIELDS or value is None:
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


def has_build_hooks(deploy_config) -> bool:
    """True when any build-phase hook is configured on ``deploy_config``."""
    if deploy_config is None:
        return False
    return any(getattr(deploy_config, name, None) for name in BUILD_HOOK_FIELDS)
