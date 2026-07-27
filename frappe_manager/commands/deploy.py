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
    bench = Bench.get_object(benchname, services_manager, output_handler=output)
    if bench.bench_config.runtime != BenchRuntime.image:
        output.display_error(
            f"Bench '{benchname}' is not in image runtime. To convert it: set runtime = 'image' "
            f"and a top-level image in its bench_config.toml, then re-run "
            f"fm switch {benchname} <repo:tag> -- the switch migrates the existing site onto the "
            f"baked image (site data and DB carry over).",
        )
        raise typer.Exit(1)
    return bench


def _resolve_switch_tag(state, tag: str | None, previous: bool) -> tuple[str | None, str | None]:
    """(target_tag, error) for ``fm switch``: explicit TAG xor ``--previous``."""
    if tag and previous:
        return None, "Pass either an explicit TAG or --previous, not both."
    if previous:
        prev = state.previous_tag if state else None
        if not prev:
            return None, "No previous image tag recorded; nothing to roll back to (pass an explicit TAG)."
        return prev, None
    if not tag:
        return None, "Missing target: pass an image TAG or --previous."
    return tag, None


def _find_current_deploy_backup(state) -> "tuple[str | None, str | None]":
    """(dump_path, error) -- the pre-migrate DB dump recorded for the CURRENT deploy.

    The dump taken while deploying the current (bad) tag is the exact pre-migrate
    state; restoring it alongside the code rollback undoes a bad migrate.
    """
    current = state.current_tag if state else None
    if not current:
        return None, "No current deploy recorded; nothing to restore."
    entries = [e for e in (state.history or []) if e.tag == current and e.backup]
    if not entries:
        return None, (
            f"No DB backup recorded for the current deploy ({current}). "
            f"Dumps live under <bench>/backups/deploy-*/ -- restore manually if one exists."
        )
    return entries[-1].backup, None


@example(
    "Bake and deploy the current bench code",
    "{benchname}",
    detail="Bakes a fresh immutable image from the bench and deploys it via recreate-swap.",
    benchname="mybench",
)
@example(
    "Deploy into a specific image repository",
    "{benchname} --image local/mybench",
    detail="Sets the top-level image for this bake+deploy.",
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
        typer.Option("--image", help="Image repository to bake into (sets the top-level image).", show_default=False),
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
            "Falls back to [deploy].ssh_server when omitted.",
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
            help="Force/disable the rolling web swap. Default: auto (rolling whenever the overlap "
            "is safe: no migrate, additive-asserted, or migrate under a maintenance window).",
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
    if config:
        try:
            apply_config_overlays(CLI_BENCHES_DIRECTORY / benchname / CLI_BENCH_CONFIG_FILE_NAME, config)
        except ConfigOverlayError as e:
            output.display_error(str(e))
            raise typer.Exit(1) from e
    bench = _load_image_bench(ctx, benchname)

    if image:
        bench.bench_config.image = image

    registry = bench.bench_config.registry
    remote_config = bench.bench_config.deploy
    distribution = registry.distribution if registry else "registry"

    docker_host = build_docker_host(remote, remote_config) if remote else remote_docker_host(remote_config)

    try:
        bake_manager = BakeManager(bench.bench_config, output_handler=output)
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
            orchestrator = DeployOrchestrator(bench, output_handler=output)
            orchestrator.deploy(built_tag, rolling=rolling)
    except DeployError as e:
        output.display_error(str(e))
        raise typer.Exit(1) from e


@example(
    "Switch a bench to an already-built image tag",
    "{benchname} local/mybench:20260721-abc123",
    detail="Deploys an existing image tag without baking. Full pipeline: migrate per [switch] config, "
    "hooks, backup, rolling web swap when eligible.",
    benchname="mybench",
)
@example(
    "Roll back to the previously deployed image",
    "{benchname} --previous",
    detail="Full pipeline pointed backwards. --previous defaults migrate OFF (old code must never "
    "migrate a newer schema); rolling zero-drop swap when eligible.",
    benchname="mybench",
)
@example(
    "Roll back code AND database",
    "{benchname} --previous --restore-db",
    detail="Also restores the pre-migrate DB dump recorded during the current deploy -- undoes a bad "
    "migrate. Runs under the maintenance window like a migrate.",
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
    tag: Annotated[
        str | None,
        typer.Argument(
            help="Full image tag to switch to (e.g. local/mybench:20260721-abc123). "
            "Omit with --previous to roll back.",
            show_default=False,
        ),
    ] = None,
    previous: Annotated[
        bool,
        typer.Option(
            "--previous",
            help="Target the previously deployed tag (rollback). Implies --no-migrate unless "
            "--migrate is passed explicitly.",
        ),
    ] = False,
    migrate: Annotated[
        bool | None,
        typer.Option(
            "--migrate/--no-migrate",
            help="Override the migrate setting from bench config (\\[switch] table) for this run only (config supports true/false/'auto').",
            show_default=False,
        ),
    ] = None,
    restore_db: Annotated[
        bool,
        typer.Option(
            "--restore-db",
            help="Restore the pre-migrate DB dump recorded for the current deploy before the swap "
            "(code and data go back together).",
        ),
    ] = False,
    rolling: Annotated[
        bool | None,
        typer.Option(
            "--rolling/--no-rolling",
            help="Force/disable the rolling web swap. Default: auto (rolling whenever the overlap "
            "is safe: no migrate/restore, additive-asserted, or under a maintenance window).",
            show_default=False,
        ),
    ] = None,
):
    """
    Switch a bench to an existing image tag (no bake) -- forward deploys and
    rollbacks are the same full pipeline pointed at different tags. --previous
    targets the last deployed tag with migrate defaulted OFF; --restore-db also
    restores the recorded pre-migrate dump.
    """
    output = get_global_output_handler()
    bench = _load_image_bench(ctx, benchname)

    state = bench.bench_config.deploy_state
    target, error = _resolve_switch_tag(state, tag, previous)
    if error:
        output.display_error(error)
        raise typer.Exit(1)

    # Rollback safety default: old code must never migrate a newer schema.
    if previous and migrate is None:
        migrate = False
        output.print("Rollback: migrate disabled for this run (override with --migrate).")

    dump = None
    if restore_db:
        from pathlib import Path

        dump_path, error = _find_current_deploy_backup(state)
        if error:
            output.display_error(error)
            raise typer.Exit(1)
        dump = Path(dump_path)
        if not dump.exists():
            output.display_error(f"Recorded DB backup is missing on disk: {dump}")
            raise typer.Exit(1)

    try:
        orchestrator = DeployOrchestrator(bench, output_handler=output)
        orchestrator.deploy(target, rolling=rolling, migrate_override=migrate, restore_db_dump=dump)
    except DeployError as e:
        output.display_error(str(e))
        raise typer.Exit(1) from e


