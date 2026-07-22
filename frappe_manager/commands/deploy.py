from typing import Annotated

import typer
from typer_examples import example

from frappe_manager import CLI_BENCH_CONFIG_FILE_NAME, CLI_BENCHES_DIRECTORY
from frappe_manager.output_manager import get_global_output_handler
from frappe_manager.site_manager.bench_config import BenchRuntime
from frappe_manager.site_manager.deploy_config_overlay import ConfigOverlayError, apply_config_overlays
from frappe_manager.site_manager.modules.bake import BakeError, BakeManager
from frappe_manager.site_manager.modules.deploy_orchestrator import DeployError, DeployOrchestrator
from frappe_manager.site_manager.modules.transport import (
    TransportError,
    build_docker_host,
    docker_host_env,
    present_tags,
    remote_docker_host,
    transport_save_load,
)
from frappe_manager.site_manager.site import Bench
from frappe_manager.utils.callbacks import sitename_callback, sites_autocompletion_callback


def _load_image_bench(ctx: typer.Context, benchname: str) -> Bench:
    services_manager = ctx.obj["services"]
    output = get_global_output_handler()
    logger = ctx.obj.get("logger") if ctx.obj else None
    bench = Bench.get_object(benchname, services_manager, logger=logger, output_handler=output)
    if bench.bench_config.runtime != BenchRuntime.image:
        output.display_error(
            f"Bench '{benchname}' is not in image runtime. "
            f"Set runtime = 'image' and [deploy].image in its bench_config.toml first.",
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
    remote: Annotated[
        str | None,
        typer.Option(
            "--remote",
            help="Deploy to a remote daemon over SSH (DOCKER_HOST=ssh://<user>@<host>:<port>). "
            "Falls back to [remote].ssh_server when omitted.",
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
    rolling: Annotated[
        bool | None,
        typer.Option(
            "--rolling/--no-rolling",
            help="Force/disable the rolling (blue-green) web swap. Default: auto "
            "(rolling when the deploy is no-migrate or asserts an additive migration).",
            show_default=False,
        ),
    ] = None,
    config: Annotated[
        list[str],
        typer.Option(
            "--config",
            help="TOML config overlay: a file path or inline TOML content, deep-merged into the "
            "bench config before deploy. Repeatable; later --config wins.",
            show_default=False,
        ),
    ] = [],
):
    """
    Bake an immutable image from the bench and deploy it (recreate-swap).

    Runs the image pipeline: fetch -> pre-flight -> backup -> maintenance ->
    drain -> migrate (one-shot new image) -> recreate-swap -> finalize -> record.

    Transport (Phase 5): in registry mode the image is pushed after bake and the
    (possibly remote) daemon pulls it during fetch; in save_load mode the image
    is streamed to the remote via ``docker save | ssh docker load`` before deploy.
    With ``--remote`` the local orchestrator drives the remote daemon via
    ``DOCKER_HOST``.
    """
    output = get_global_output_handler()
    logger = ctx.obj.get("logger") if ctx.obj else None
    if config:
        try:
            apply_config_overlays(CLI_BENCHES_DIRECTORY / benchname / CLI_BENCH_CONFIG_FILE_NAME, config)
        except ConfigOverlayError as e:
            output.display_error(str(e))
            raise typer.Exit(1) from e
    bench = _load_image_bench(ctx, benchname)

    if image:
        bench.bench_config.deploy.image = image

    registry = bench.bench_config.registry
    remote_config = bench.bench_config.remote
    distribution = registry.distribution if registry else "registry"

    docker_host = build_docker_host(remote, remote_config) if remote else remote_docker_host(remote_config)

    try:
        bake_manager = BakeManager(bench.bench_config, output_handler=output, logger=logger)
        built_tag = bake_manager.bake(tag=tag, push=push)
    except BakeError as e:
        output.display_error(str(e))
        raise typer.Exit(1) from e

    # save_load (airgap): transport the images to the remote daemon before deploy.
    if distribution == "save_load":
        try:
            nginx_tag = BakeManager.nginx_image_tag(built_tag)
            tags = present_tags(bench.docker_client, [built_tag, nginx_tag])
            transport_save_load(tags, remote_config, output=output)
        except TransportError as e:
            output.display_error(str(e))
            raise typer.Exit(1) from e

    try:
        with docker_host_env(docker_host):
            orchestrator = DeployOrchestrator(bench, output_handler=output, logger=logger)
            orchestrator.deploy(built_tag, rolling=rolling)
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
    rolling: Annotated[
        bool | None,
        typer.Option(
            "--rolling/--no-rolling",
            help="Force/disable the rolling (blue-green) web swap. Default: auto "
            "(rolling when the deploy is no-migrate or asserts an additive migration).",
            show_default=False,
        ),
    ] = None,
):
    """
    Switch a bench to an existing image tag (no bake). Rolling (blue-green) web
    swap when eligible, else recreate-swap.
    """
    output = get_global_output_handler()
    logger = ctx.obj.get("logger") if ctx.obj else None
    bench = _load_image_bench(ctx, benchname)

    try:
        orchestrator = DeployOrchestrator(bench, output_handler=output, logger=logger)
        orchestrator.deploy(tag, rolling=rolling)
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
