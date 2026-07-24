"""Shared bench provisioning.

The single provisioning path used by both `fm create` (mount runtime, exec into
the live bench) and `fm bake` (image runtime, one-shot container). The only
difference between the two callers is the ``use_run`` seam on
``BenchAppManager._container_run`` (``False`` = ``docker compose exec`` into a
running bench, ``True`` = ``docker compose run --rm`` in a fresh container) and
the target frappe-bench directory. Extracted from
``BenchOrchestrator._phase2_initialize_bench`` so image bake reuses it verbatim.

Per-app build hooks (``[[apps.hooks]]`` / ``[[apps.hooks.host]]`` with
``{before,after}_{deps,build}``) run here when any app configures them — around
that app's dependency install and asset build, shared by create and bake (see
``frappe_manager.site_manager.hooks``). When no app has build hooks the untouched
``install_apps(skip_clone=True)`` fast path runs.
"""

import contextlib
import subprocess
import tempfile
import time
from pathlib import Path

from frappe_manager.output_manager import OutputHandler
from frappe_manager.site_manager.bench_config import (
    AppConfig,
    extract_node_version_requirement,
    extract_python_version_requirement,
)
from frappe_manager.site_manager.hooks import app_has_build_hooks, hook_env, hook_script
from frappe_manager.site_manager.modules.bench_app import BenchAppManager


class ProvisionHookError(Exception):
    """Raised when a build hook fails during provisioning."""


def provision(
    app_manager: BenchAppManager,
    apps: list[AppConfig],
    *,
    output: OutputHandler,
    use_uv: bool = True,
    github_token: str | None = None,
    use_run: bool = True,
    detect_versions: bool = True,
) -> list[AppConfig]:
    """Clone apps -> detect/setup Python+Node runtimes -> install deps + build.

    Mutates ``app_manager.bench_config.python_version``/``node_version`` when
    ``detect_versions`` is set and they are unset. Returns the (possibly
    module-name-corrected) app list from the final install pass. When any app
    configures build hooks they are run around that app's install/build steps;
    otherwise the untouched ``install_apps`` fast path runs.
    """
    output.change_head("Cloning apps")
    app_manager.install_apps(
        apps,
        github_token=github_token,
        use_uv=use_uv,
        clone_only=True,
        use_run=use_run,
    )

    bench_config = app_manager.bench_config
    if detect_versions:
        frappe_app_path = app_manager.frappe_bench_dir / "apps" / "frappe"
        if frappe_app_path.exists():
            if not bench_config.python_version:
                detected_python = extract_python_version_requirement(frappe_app_path)
                if detected_python:
                    bench_config.python_version = detected_python
                    output.print(f"Detected Python version requirement: {detected_python}")

            if not bench_config.node_version:
                detected_node = extract_node_version_requirement(frappe_app_path)
                if detected_node:
                    bench_config.node_version = detected_node
                    output.print(f"Detected Node version requirement: {detected_node}")

    if bench_config.python_version or bench_config.node_version:
        app_manager.setup_python_and_node_environments(use_run=use_run, recreate_python_env=True)

    if any(app.hooks and app_has_build_hooks(app.hooks) for app in apps):
        return _install_with_app_hooks(app_manager, apps, use_uv=use_uv, use_run=use_run, output=output)

    output.change_head("Installing dependencies for all apps")
    return app_manager.install_apps(
        apps,
        github_token=github_token,
        use_uv=use_uv,
        skip_clone=True,
        use_run=use_run,
    )


def _run_build_hook(
    app_manager: BenchAppManager,
    value: str | None,
    phase: str,
    env: dict[str, str],
    *,
    on_host: bool,
    use_run: bool,
    output: OutputHandler,
) -> None:
    """Run one build hook on the host (bash subprocess in the bench dir) or in the
    provisioning container (temp script on the logs mount via ``_container_run``).
    ``None``/empty values are skipped. A non-zero exit raises ``ProvisionHookError``,
    failing the provision/bake."""
    if not value:
        return
    script = hook_script(value, env)
    output.change_head(f"Running {phase} hook ({'host' if on_host else 'container'})")
    if on_host:
        with tempfile.NamedTemporaryFile("w", suffix=".sh", delete=False) as fh:
            fh.write(script)
            script_path = fh.name
        try:
            proc = subprocess.run(  # noqa: S603
                ["bash", script_path],  # noqa: S607
                cwd=str(app_manager.frappe_bench_dir),
                capture_output=True,
                text=True,
                check=False,
            )
            for line in (proc.stdout or "").splitlines():
                if line.strip():
                    output.print(line.strip())
            if proc.returncode != 0:
                raise ProvisionHookError(
                    f"{phase} hook (host) failed (exit {proc.returncode}): {(proc.stderr or '').strip()}",
                )
        finally:
            with contextlib.suppress(OSError):
                Path(script_path).unlink()
        return

    logs_dir = app_manager.frappe_bench_dir / "logs"
    logs_dir.mkdir(parents=True, exist_ok=True)
    safe_phase = phase.replace(" ", "_").replace("/", "_")
    name = f".fm_build_hook_{safe_phase}_{int(time.time())}.sh"
    host_script = logs_dir / name
    container_script = f"/workspace/frappe-bench/logs/{name}"
    host_script.write_text(script)
    try:
        result = app_manager._container_run(f"bash {container_script}", capture_output=True, use_run=use_run)  # noqa: SLF001
        for line in getattr(result, "combined", None) or []:
            if line.strip():
                output.print(line.strip())
    except Exception as e:
        raise ProvisionHookError(f"{phase} hook (container) failed: {e}") from e
    finally:
        with contextlib.suppress(OSError):
            host_script.unlink()


def _install_with_app_hooks(
    app_manager: BenchAppManager,
    apps: list[AppConfig],
    *,
    use_uv: bool,
    use_run: bool,
    output: OutputHandler,
) -> list[AppConfig]:
    """Per-app install with build hooks: for each app run its deps hooks (host then
    container) around ``uv`` python-deps install, then one global node-deps install,
    then for each app run its build hooks around ``bench build --app <name>``."""
    app_names = ",".join(app.name for app in apps)

    def app_env(app: AppConfig) -> dict[str, str]:
        return hook_env(
            {
                "BENCH_PATH": str(app_manager.frappe_bench_dir),
                "APPS": app_names,
                "APP": app.name,
            }
        )

    def run(app: AppConfig, value: str | None, phase: str, *, on_host: bool) -> None:
        _run_build_hook(
            app_manager,
            value,
            phase,
            app_env(app),
            on_host=on_host,
            use_run=use_run,
            output=output,
        )

    for app in apps:
        hooks = app.hooks
        host = hooks.host if hooks else None
        run(app, host.before_deps if host else None, f"{app.name} before_deps", on_host=True)
        run(app, hooks.before_deps if hooks else None, f"{app.name} before_deps", on_host=False)
        output.change_head(f"Installing Python dependencies for {app.name}")
        app_manager._install_python_deps_with_uv([app], use_uv=use_uv, use_run=use_run)  # noqa: SLF001
        run(app, hooks.after_deps if hooks else None, f"{app.name} after_deps", on_host=False)
        run(app, host.after_deps if host else None, f"{app.name} after_deps", on_host=True)

    output.change_head("Installing Node dependencies")
    app_manager._install_node_deps(use_run=use_run)  # noqa: SLF001

    for app in apps:
        hooks = app.hooks
        host = hooks.host if hooks else None
        run(app, host.before_build if host else None, f"{app.name} before_build", on_host=True)
        run(app, hooks.before_build if hooks else None, f"{app.name} before_build", on_host=False)
        output.change_head(f"Building frontend assets for {app.name}")
        app_manager.build(app_list=[app.name], use_run=use_run)
        run(app, hooks.after_build if hooks else None, f"{app.name} after_build", on_host=False)
        run(app, host.after_build if host else None, f"{app.name} after_build", on_host=True)

    return apps
