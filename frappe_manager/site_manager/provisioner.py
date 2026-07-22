"""Shared bench provisioning.

The single provisioning path used by both `fm create` (mount runtime, exec into
the live bench) and `fm bake` (image runtime, one-shot container). The only
difference between the two callers is the ``use_run`` seam on
``BenchAppManager._container_run`` (``False`` = ``docker compose exec`` into a
running bench, ``True`` = ``docker compose run --rm`` in a fresh container) and
the target frappe-bench directory. Extracted from
``BenchOrchestrator._phase2_initialize_bench`` so image bake reuses it verbatim.

Build hooks (``[deploy].{before,after}_{python_install,bench_build}`` + host
variants) run here when configured — around dependency install and asset build,
shared by create and bake (see ``frappe_manager.site_manager.hooks``). When no
build hook is set the untouched ``install_apps(skip_clone=True)`` fast path runs.
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
from frappe_manager.site_manager.hooks import has_build_hooks, hook_env, hook_script
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
    deploy_config=None,
) -> list[AppConfig]:
    """Clone apps -> detect/setup Python+Node runtimes -> install deps + build.

    Mutates ``app_manager.bench_config.python_version``/``node_version`` when
    ``detect_versions`` is set and they are unset. Returns the (possibly
    module-name-corrected) app list from the final install pass. When
    ``deploy_config`` carries build hooks they are run around the install/build
    steps; otherwise the untouched ``install_apps`` fast path runs.
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

    if has_build_hooks(deploy_config):
        return _install_with_build_hooks(
            app_manager, apps, deploy_config, use_uv=use_uv, use_run=use_run, output=output
        )

    output.change_head("Installing dependencies for all apps")
    return app_manager.install_apps(
        apps,
        github_token=github_token,
        use_uv=use_uv,
        skip_clone=True,
        use_run=use_run,
    )


def _build_hook_env(app_manager: BenchAppManager, deploy_config) -> dict[str, str]:
    apps_dir = app_manager.frappe_bench_dir / "apps"
    apps = ",".join(sorted(d.name for d in apps_dir.iterdir() if d.is_dir())) if apps_dir.exists() else ""
    return hook_env(deploy_config, {"BENCH_PATH": str(app_manager.frappe_bench_dir), "APPS": apps})


def _run_build_hook(
    app_manager: BenchAppManager,
    value: str | None,
    phase: str,
    *,
    deploy_config,
    on_host: bool,
    use_run: bool,
    output: OutputHandler,
) -> None:
    """Run one build hook on the host (bash subprocess in the bench dir) or in the
    provisioning container (temp script on the logs mount via ``_container_run``).
    A non-zero exit raises ``ProvisionHookError``, failing the provision/bake."""
    if not value:
        return
    script = hook_script(value, _build_hook_env(app_manager, deploy_config))
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
    name = f".fm_build_hook_{phase}_{int(time.time())}.sh"
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


def _install_with_build_hooks(
    app_manager: BenchAppManager,
    apps: list[AppConfig],
    deploy_config,
    *,
    use_uv: bool,
    use_run: bool,
    output: OutputHandler,
) -> list[AppConfig]:
    """``install_apps(skip_clone=True)`` (python deps -> node deps -> build) with
    build hooks interleaved around python-install and bench-build, host then
    container (fmd order)."""

    def run(field: str, *, on_host: bool) -> None:
        _run_build_hook(
            app_manager,
            getattr(deploy_config, field),
            field,
            deploy_config=deploy_config,
            on_host=on_host,
            use_run=use_run,
            output=output,
        )

    run("host_before_python_install", on_host=True)
    run("before_python_install", on_host=False)
    output.change_head("Installing Python dependencies")
    app_manager._install_python_deps_with_uv(apps, use_uv=use_uv, use_run=use_run)  # noqa: SLF001
    run("after_python_install", on_host=False)
    run("host_after_python_install", on_host=True)

    output.change_head("Installing Node dependencies")
    app_manager._install_node_deps(use_run=use_run)  # noqa: SLF001

    run("host_before_bench_build", on_host=True)
    run("before_bench_build", on_host=False)
    output.change_head("Building frontend assets")
    app_manager.build(use_run=use_run)
    run("after_bench_build", on_host=False)
    run("host_after_bench_build", on_host=True)

    return apps
