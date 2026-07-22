import tempfile
from pathlib import Path
from typing import Annotated, cast

import tomlkit
import typer
from typer_examples import example

from frappe_manager import (
    CLI_BENCH_CONFIG_FILE_NAME,
    CLI_BENCHES_DIRECTORY,
    STABLE_APP_BRANCH_MAPPING_LIST,
)
from frappe_manager.output_manager import get_global_output_handler
from frappe_manager.site_manager.bench_config import AppConfig, BenchConfig, DeployConfig
from frappe_manager.site_manager.deploy_config_overlay import (
    ConfigOverlayError,
    apply_config_overlays,
    merge_overlays,
)
from frappe_manager.site_manager.modules.bake import BakeError, BakeManager
from frappe_manager.utils.callbacks import (
    apps_list_validation_callback,
    sitename_callback,
    sites_autocompletion_callback,
)


def _bake_name(image: str | None) -> str:
    """A provisioning/container name for a standalone bake, derived from the
    image repo basename (``ghcr.io/acme/mysite`` -> ``mysite``)."""
    if image:
        name = image.rsplit("/", 1)[-1].split(":")[0]
        if name:
            return name
    return "fm-bake"


def _frappe_first(apps: list[AppConfig]) -> list[AppConfig]:
    """Ensure frappe is present and first, mirroring ``fm create``."""
    frappe_app = None
    others: list[AppConfig] = []
    for app in apps:
        if app.name == "frappe" or app.name.endswith("/frappe"):
            frappe_app = app
        else:
            others.append(app)
    if frappe_app is None:
        frappe_app = AppConfig.from_string(f"frappe:{STABLE_APP_BRANCH_MAPPING_LIST['frappe']}")
    return [frappe_app, *others]


def _build_standalone_config(
    apps: list[AppConfig],
    image: str | None,
    python_version: str | None,
    node_version: str | None,
    github_token: str | None,
    config: list[str],
) -> BenchConfig:
    """Assemble a transient ``BenchConfig`` for a bench-less bake.

    The flags seed a minimal base config; each ``--config`` overlay deep-merges
    on top (later wins), exactly like the bench-mode overlay. No bench dir is
    touched — bake provisions the resulting apps into a temp build context.
    """
    doc = tomlkit.document()
    doc["name"] = _bake_name(image)
    doc["developer_mode"] = False
    doc["admin_tools"] = False
    doc["environment_type"] = "prod"
    doc["use_uv"] = True
    if github_token:
        doc["github_token"] = github_token

    if apps:
        apps_aot = tomlkit.aot()
        for app in _frappe_first(apps):
            table = tomlkit.table()
            for key, value in app.model_dump(exclude_none=True).items():
                table[key] = value
            apps_aot.append(table)
        doc["apps_list"] = apps_aot

    if python_version or node_version:
        build_table = tomlkit.table()
        if python_version:
            build_table["python_version"] = python_version
        if node_version:
            build_table["node_version"] = node_version
        doc["build"] = build_table

    merged = merge_overlays(tomlkit.dumps(doc), config)

    handle = tempfile.NamedTemporaryFile("w", suffix=".toml", delete=False)  # noqa: SIM115
    try:
        handle.write(merged)
        handle.close()
        return BenchConfig.import_from_toml(Path(handle.name))
    finally:
        Path(handle.name).unlink(missing_ok=True)


@example(
    "Bake an image from an existing bench",
    "{benchname}",
    detail="Provisions the bench's apps into a build context and builds a runtime image tagged from [deploy].image.",
    benchname="mybench",
)
@example(
    "Bake with an explicit image repository",
    "{benchname} --image local/mybench",
    detail="Overrides [deploy].image for this bake. The tag is derived automatically as <repo>:<timestamp>-<git sha>.",
    benchname="mybench",
)
@example(
    "Standalone bake (no bench) from apps",
    "--apps erpnext:version-15 --image ghcr.io/acme/mysite --push",
    detail="Builds an image directly from apps — no bench/compose/site. Ideal for CI 'build once -> push -> deploy elsewhere'.",
)
@example(
    "Standalone bake from a config file",
    "--config ci/build.toml",
    detail="The config supplies [deploy].image, apps_list and [build]; nothing else on disk is needed.",
)
def bake(
    ctx: typer.Context,
    benchname: Annotated[
        str | None,
        typer.Argument(
            help="Bench to bake. Omit for a standalone bake driven by --apps/--config.",
            autocompletion=sites_autocompletion_callback,
        ),
    ] = None,
    image: Annotated[
        str | None,
        typer.Option(
            "--image",
            help="Image repository to bake into (overrides [deploy].image).",
            show_default=False,
        ),
    ] = None,
    tag: Annotated[
        str | None,
        typer.Option(
            "--tag",
            help="Full image tag to build (overrides the auto-generated <repo>:<timestamp>-<sha>).",
            show_default=False,
        ),
    ] = None,
    push: Annotated[
        bool | None,
        typer.Option(
            "--push/--no-push",
            help="Push the baked image to the registry (default: push when [registry] is configured for 'registry').",
            show_default=False,
        ),
    ] = None,
    config: Annotated[
        list[str],
        typer.Option(
            "--config",
            help="TOML config overlay: a file path or inline TOML content, deep-merged into the "
            "config before baking. Repeatable; later --config wins.",
            show_default=False,
        ),
    ] = [],
    apps: Annotated[
        list[str],
        typer.Option(
            "--apps",
            "-a",
            help="Standalone bake only: apps to bake (appname:branch or appname, e.g. erpnext:version-15). Repeatable.",
            callback=apps_list_validation_callback,
            show_default=False,
        ),
    ] = [],
    python_version: Annotated[
        str | None,
        typer.Option(
            "--python",
            help="Standalone bake only: Python version to bake (sets [build].python_version).",
            show_default=False,
        ),
    ] = None,
    node_version: Annotated[
        str | None,
        typer.Option(
            "--node",
            help="Standalone bake only: Node version to bake (sets [build].node_version).",
            show_default=False,
        ),
    ] = None,
    github_token: Annotated[
        str | None,
        typer.Option(
            "--github-token",
            "-t",
            help="Standalone bake only: GitHub token for private app repos (or use GITHUB_TOKEN env var).",
            envvar="GITHUB_TOKEN",
            show_default=False,
        ),
    ] = None,
):
    """
    Bake an immutable app image.

    Two modes:

    - Bench: `fm bake <bench>` provisions the named bench's apps into a temp
      build context and builds a runtime image.
    - Standalone: `fm bake --apps ... --image ...` (or `--config`) builds an
      image with no bench/compose/site — for CI "build once -> push -> deploy".

    Both provision via docker run and COPY the provisioned frappe-bench onto the
    base image (keeping the supervisor entrypoint).
    """
    output = get_global_output_handler()

    apps_config = cast("list[AppConfig]", apps)
    standalone = benchname is None and (bool(apps_config) or bool(config))

    if standalone:
        try:
            bench_config = _build_standalone_config(
                apps_config, image, python_version, node_version, github_token, config
            )
        except ConfigOverlayError as e:
            output.display_error(str(e))
            raise typer.Exit(1) from e
        if not bench_config.apps_list:
            output.display_error("Standalone bake needs apps: pass --apps or a --config providing apps_list.")
            raise typer.Exit(1)
    else:
        if apps_config or python_version or node_version:
            output.display_error("--apps/--python/--node are only for standalone bake (omit the bench name).")
            raise typer.Exit(1)
        resolved_name = sitename_callback(benchname)
        bench_config_path = CLI_BENCHES_DIRECTORY / resolved_name / CLI_BENCH_CONFIG_FILE_NAME
        if not bench_config_path.exists():
            output.display_error(f"Bench '{resolved_name}' not found ({bench_config_path} missing).")
            raise typer.Exit(1)
        try:
            apply_config_overlays(bench_config_path, config)
        except ConfigOverlayError as e:
            output.display_error(str(e))
            raise typer.Exit(1) from e
        bench_config = BenchConfig.import_from_toml(bench_config_path)

    if image:
        if bench_config.deploy is None:
            bench_config.deploy = DeployConfig(image=image)
        else:
            bench_config.deploy.image = image

    logger = ctx.obj.get("logger") if ctx.obj else None

    try:
        bake_manager = BakeManager(bench_config, output_handler=output, logger=logger)
        built_tag = bake_manager.bake(tag=tag, push=push)
    except BakeError as e:
        output.display_error(str(e))
        raise typer.Exit(1) from e

    output.print(f"Baked image: {built_tag}", emoji_code=":package:")
