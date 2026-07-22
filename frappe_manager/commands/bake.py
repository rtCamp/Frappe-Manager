from typing import Annotated

import typer
from typer_examples import example

from frappe_manager import CLI_BENCH_CONFIG_FILE_NAME, CLI_BENCHES_DIRECTORY
from frappe_manager.output_manager import get_global_output_handler
from frappe_manager.site_manager.bench_config import BenchConfig, DeployConfig
from frappe_manager.site_manager.deploy_config_overlay import ConfigOverlayError, apply_config_overlays
from frappe_manager.site_manager.modules.bake import BakeError, BakeManager
from frappe_manager.utils.callbacks import (
    sitename_callback,
    sites_autocompletion_callback,
)


@example(
    "Bake an immutable app image for a bench",
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
def bake(
    ctx: typer.Context,
    benchname: Annotated[
        str,
        typer.Argument(
            help="Name of the bench to bake.",
            autocompletion=sites_autocompletion_callback,
            callback=sitename_callback,
        ),
    ],
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
            "bench config before baking. Repeatable; later --config wins.",
            show_default=False,
        ),
    ] = [],
):
    """
    Bake an immutable app image from a bench.

    Provisions the bench's apps into a temporary build context via docker run and
    builds a runtime image (COPY of the provisioned frappe-bench onto the base
    image, keeping the supervisor entrypoint).
    """
    output = get_global_output_handler()

    bench_config_path = CLI_BENCHES_DIRECTORY / benchname / CLI_BENCH_CONFIG_FILE_NAME
    if not bench_config_path.exists():
        output.display_error(f"Bench '{benchname}' not found ({bench_config_path} missing).")
        raise typer.Exit(1)

    try:
        apply_config_overlays(bench_config_path, config)
    except ConfigOverlayError as e:
        output.display_error(str(e))
        raise typer.Exit(1) from e

    bench_config: BenchConfig = BenchConfig.import_from_toml(bench_config_path)

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
