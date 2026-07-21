"""Shared bench provisioning.

The single provisioning path used by both `fm create` (mount runtime, exec into
the live bench) and `fm bake` (image runtime, one-shot container). The only
difference between the two callers is the ``use_run`` seam on
``BenchAppManager._container_run`` (``False`` = ``docker compose exec`` into a
running bench, ``True`` = ``docker compose run --rm`` in a fresh container) and
the target frappe-bench directory. Extracted from
``BenchOrchestrator._phase2_initialize_bench`` so image bake reuses it verbatim.
"""

from frappe_manager.output_manager import OutputHandler
from frappe_manager.site_manager.bench_config import (
    AppConfig,
    extract_node_version_requirement,
    extract_python_version_requirement,
)
from frappe_manager.site_manager.modules.bench_app import BenchAppManager


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
    module-name-corrected) app list from the final install pass.
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

    output.change_head("Installing dependencies for all apps")
    return app_manager.install_apps(
        apps,
        github_token=github_token,
        use_uv=use_uv,
        skip_clone=True,
        use_run=use_run,
    )
