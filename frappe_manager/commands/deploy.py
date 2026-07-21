from typing import Annotated

import typer
from typer_examples import example

from frappe_manager.output_manager import get_global_output_handler
from frappe_manager.site_manager.bench_config import DeploymentMode
from frappe_manager.site_manager.modules.bake import BakeError, BakeManager
from frappe_manager.site_manager.modules.deploy_orchestrator import DeployError, DeployOrchestrator
from frappe_manager.site_manager.site import Bench
from frappe_manager.utils.callbacks import sitename_callback, sites_autocompletion_callback


def _load_image_bench(ctx: typer.Context, benchname: str) -> Bench:
    services_manager = ctx.obj["services"]
    output = get_global_output_handler()
    logger = ctx.obj.get("logger") if ctx.obj else None
    bench = Bench.get_object(benchname, services_manager, logger=logger, output_handler=output)
    if bench.bench_config.deployment_mode != DeploymentMode.image:
        output.display_error(
            f"Bench '{benchname}' is not in image deployment mode. "
            f"Set deployment_mode = 'image' and [deploy].image in its bench_config.toml first.",
        )
        raise typer.Exit(1)
    return bench


@example(
    "Bake and deploy the current bench code",
    "{benchname}",
    detail="Bakes a fresh immutable image from the bench and deploys it via recreate-swap.",
    benchname="mybench",
)
@example(
    "Deploy into a specific image repository",
    "{benchname} --image local/mybench",
    detail="Overrides [deploy].image for this bake+deploy.",
    benchname="mybench",
)
def deploy(
    ctx: typer.Context,
    benchname: Annotated[
        str,
        typer.Argument(
            help="Name of the bench to deploy.",
            autocompletion=sites_autocompletion_callback,
            callback=sitename_callback,
        ),
    ],
    image: Annotated[
        str | None,
        typer.Option("--image", help="Image repository to bake into (overrides [deploy].image).", show_default=False),
    ] = None,
    tag: Annotated[
        str | None,
        typer.Option("--tag", help="Full image tag to build (overrides the auto-generated tag).", show_default=False),
    ] = None,
):
    """
    Bake an immutable image from the bench and deploy it (recreate-swap).

    Runs the image pipeline: fetch -> pre-flight -> backup -> maintenance ->
    drain -> migrate (one-shot new image) -> recreate-swap -> finalize -> record.
    """
    output = get_global_output_handler()
    logger = ctx.obj.get("logger") if ctx.obj else None
    bench = _load_image_bench(ctx, benchname)

    if image:
        bench.bench_config.deploy.image = image

    try:
        bake_manager = BakeManager(bench.bench_config, output_handler=output, logger=logger)
        built_tag = bake_manager.bake(tag=tag)
    except BakeError as e:
        output.display_error(str(e))
        raise typer.Exit(1) from e

    try:
        orchestrator = DeployOrchestrator(bench, output_handler=output, logger=logger)
        orchestrator.deploy(built_tag)
    except DeployError as e:
        output.display_error(str(e))
        raise typer.Exit(1) from e


@example(
    "Switch a bench to an already-built image tag",
    "{benchname} local/mybench:20260721-abc123",
    detail="Deploys an existing image tag without baking. Runs the full recreate-swap pipeline.",
    benchname="mybench",
)
def switch(
    ctx: typer.Context,
    benchname: Annotated[
        str,
        typer.Argument(
            help="Name of the bench.",
            autocompletion=sites_autocompletion_callback,
            callback=sitename_callback,
        ),
    ],
    tag: Annotated[str, typer.Argument(help="Full image tag to switch to (e.g. local/mybench:20260721-abc123).")],
):
    """
    Switch a bench to an existing image tag (no bake) via recreate-swap.
    """
    output = get_global_output_handler()
    logger = ctx.obj.get("logger") if ctx.obj else None
    bench = _load_image_bench(ctx, benchname)

    try:
        orchestrator = DeployOrchestrator(bench, output_handler=output, logger=logger)
        orchestrator.deploy(tag)
    except DeployError as e:
        output.display_error(str(e))
        raise typer.Exit(1) from e


@example(
    "Roll back to the previously deployed image",
    "{benchname}",
    detail="Re-pins the compose to the previous tag and recreates (no migrate).",
    benchname="mybench",
)
def rollback(
    ctx: typer.Context,
    benchname: Annotated[
        str,
        typer.Argument(
            help="Name of the bench.",
            autocompletion=sites_autocompletion_callback,
            callback=sitename_callback,
        ),
    ],
):
    """
    Roll back the bench to the previously deployed image tag (no migrate).
    """
    output = get_global_output_handler()
    logger = ctx.obj.get("logger") if ctx.obj else None
    bench = _load_image_bench(ctx, benchname)

    state = bench.bench_config.deploy_state
    previous = state.previous_tag if state else None
    if not previous:
        output.display_error(
            f"Bench '{benchname}' has no previous image tag recorded; nothing to roll back to.",
        )
        raise typer.Exit(1)

    try:
        orchestrator = DeployOrchestrator(bench, output_handler=output, logger=logger)
        orchestrator.rollback(previous)
    except DeployError as e:
        output.display_error(str(e))
        raise typer.Exit(1) from e
