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
from frappe_manager.site_manager.bench_config import AppConfig, BenchConfig, BuildConfig
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
from frappe_manager.utils.helpers import has_explicit_tag


def _bake_name(image: str | None) -> str:
    """A provisioning/container name for a standalone bake, derived from the
    image repo basename (``ghcr.io/acme/mysite`` -> ``mysite``)."""
    if image:
        name = image.rsplit("/", 1)[-1].split(":")[0]
        if name:
            return name
    return "fm-bake"


def _base_image_callback(value: str | None) -> str | None:
    """``--base-image`` pins a specific base, so a bare repo is almost certainly a mistake."""
    if value and not has_explicit_tag(value):
        raise typer.BadParameter("--base-image must include a tag, e.g. 'ghcr.io/acme/frappe-custom:v15'.")
    return value


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
    touched -- bake provisions the resulting apps into a temp build context.
    """
    doc = tomlkit.document()
    doc["name"] = _bake_name(image)
    doc["developer_mode"] = False
    doc["admin_tools"] = False
    doc["environment"] = "prod"
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
        doc["apps"] = apps_aot

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
    benchname="mybench",
)
@example(
    "Bake into a specific image repository",
    "{benchname} --image local/mybench",
    benchname="mybench",
)
@example(
    "Bake an exact image reference",
    "{benchname} --image ghcr.io/acme/mysite:v42 --push",
    benchname="mybench",
    detail="A ref that already carries a tag is built verbatim; drop the tag to get a generated :<timestamp>-<sha> instead.",
)
@example(
    "Pin the base image the build starts FROM",
    "{benchname} --base-image ghcr.io/acme/frappe-custom:v15",
    benchname="mybench",
    detail="--base-image is what the runtime Dockerfile builds FROM, while --image is what the bake produces.",
)
@example(
    "Bake exactly what is on disk right now",
    "{benchname} --source workspace",
    benchname="mybench",
)
@example(
    "Standalone bake, no bench involved",
    "--apps erpnext:version-15 --image ghcr.io/acme/mysite --push",
)
@example(
    "Standalone bake from a config file",
    "--config ci/build.toml",
    detail="The config supplies the image, [[apps]] and [build]; nothing else on disk is needed.",
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
            help="Image to build. A full ref (ghcr.io/acme/mysite:v42) is built as-is; a bare repo (ghcr.io/acme/mysite) gets a generated :<timestamp>-<sha> tag. Defaults to the bench's configured image.",
            show_default=False,
        ),
    ] = None,
    base_image: Annotated[
        str | None,
        typer.Option(
            "--base-image",
            help="Image the runtime Dockerfile builds FROM. Defaults to \\[build].base_image, else fm's published frappe image for this fm version.",
            callback=_base_image_callback,
            show_default=False,
        ),
    ] = None,
    push: Annotated[
        bool | None,
        typer.Option(
            "--push/--no-push",
            help="Push the baked image to the registry after building. Defaults to \\[build].push, which is off unless set. A bake that does not push still loads the image into the local daemon.",
            show_default=False,
        ),
    ] = None,
    config: Annotated[
        list[str],
        typer.Option(
            "--config",
            help="TOML overlay, either a file path or inline TOML. With a bench it is merged into bench_config.toml and stays there; standalone it supplies the whole config. Repeatable; later --config wins.",
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
            help="Standalone bake only: Python version to bake.",
            show_default=False,
        ),
    ] = None,
    node_version: Annotated[
        str | None,
        typer.Option(
            "--node",
            help="Standalone bake only: Node version to bake.",
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
    source: Annotated[
        str | None,
        typer.Option(
            "--source",
            help="Where app code comes from: 'provision' (default) clones and installs fresh, 'workspace' snapshots the bench's current on-disk workspace (bench mode only).",
            show_default=False,
        ),
    ] = None,
    include: Annotated[
        list[str],
        typer.Option(
            "--include",
            help="Host path to copy into the image, as 'src' or 'src:dest' with dest relative to the bench root (default: the src basename). Overwrites whatever the app source put there. Repeatable.",
            show_default=False,
        ),
    ] = [],
):
    """
    Bake an immutable app image.

    Two modes:

    - With a bench name: bakes that bench's apps.
    - With --apps or --config and no bench name: builds with no bench, compose project or site.
    """
    output = get_global_output_handler()

    apps_config = cast("list[AppConfig]", apps)

    if benchname is None and not (apps_config or config):
        # Falling through to sitename_callback here would open the interactive bench picker and
        # bake whatever bench it lands on, instead of refusing a usage error.
        output.display_error("Standalone bake needs apps: pass --apps or a --config providing \\[\\[apps]].")
        raise typer.Exit(1)

    standalone = benchname is None

    if standalone:
        try:
            bench_config = _build_standalone_config(
                apps_config, image, python_version, node_version, github_token, config
            )
        except ConfigOverlayError as e:
            output.display_error(str(e))
            raise typer.Exit(1) from e
        if not bench_config.apps_list:
            output.display_error("Standalone bake needs apps: pass --apps or a --config providing \\[\\[apps]].")
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

    explicit_tag: str | None = None
    if image:
        if has_explicit_tag(image):
            explicit_tag = image
        else:
            bench_config.image = image

    if base_image:
        if bench_config.build is None:
            bench_config.build = BuildConfig()
        bench_config.build.base_image = base_image

    if source is not None:
        if source not in ("provision", "workspace"):
            output.display_error("--source must be 'provision' or 'workspace'.")
            raise typer.Exit(1)
        if bench_config.build is None:
            bench_config.build = BuildConfig()
        bench_config.build.source = source

    if standalone and bench_config.build and bench_config.build.source == "workspace":
        output.display_error(
            "--source workspace requires a bench (it snapshots the bench's workspace); omit it for standalone bake.",
        )
        raise typer.Exit(1)

    if include:
        if bench_config.build is None:
            bench_config.build = BuildConfig()
        bench_config.build.include = [*bench_config.build.include, *include]

    try:
        bake_manager = BakeManager(bench_config, output_handler=output)
        built_tag = bake_manager.bake(tag=explicit_tag, push=push)
    except BakeError as e:
        output.display_error(str(e))
        raise typer.Exit(1) from e

    output.print(f"Baked image: {built_tag}", emoji_code=":package:")
