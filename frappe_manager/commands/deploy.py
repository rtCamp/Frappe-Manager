from typing import Annotated

import typer
from typer_examples import example

from frappe_manager import CLI_BENCH_CONFIG_FILE_NAME, CLI_BENCHES_DIRECTORY
from frappe_manager.commands.arguments import RequiredBenchNameArgument
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
    remote_daemon_arch,
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


def _reject_impossible_keep(output, keep: int | None) -> None:
    """Refuse ``--keep`` below 1 instead of silently rewriting it.

    ``plan_release_prune`` floors the retention at 1 (the current release is
    never pruned), so ``--keep 0`` used to mean ``--keep 1`` with nothing said:
    an operator asking to drop all history kept a row and its image tag anyway.
    The floor stays as the pure function's backstop; the impossible ask is
    refused here, where the operator can see it.
    """
    if keep is not None and keep < 1:
        output.display_error("--keep must be at least 1: the current release is never pruned.")
        raise typer.Exit(1)


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
    detail="Bakes a fresh immutable image from the bench and deploys it (same pipeline as fm switch).",
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
    keep: Annotated[
        int | None,
        typer.Option(
            "--keep",
            help="After a successful deploy, prune old releases keeping the newest N (minimum 1; see fm prune).",
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
    Bake an immutable image from the bench and deploy it.

    Bake + switch in one command: builds the image, transports it if configured, then runs the same deploy pipeline as fm switch -- fetch -> pre-flight -> backup -> maintenance -> drain -> migrate (one-shot new image) -> swap (rolling when safe) -> finalize -> record.

    Transport: in registry mode the image is pushed after bake and the (possibly remote) daemon pulls it during fetch; in save_load mode the image is streamed to the remote via ``docker save | ssh docker load`` before deploy. With ``--remote`` the local orchestrator drives the remote daemon via ``DOCKER_HOST``.
    """
    output = get_global_output_handler()
    _reject_impossible_keep(output, keep)
    # The runtime check comes FIRST: apply_config_overlays is a persisted rewrite
    # of bench_config.toml, and running it before the check left a deploy that
    # was then refused (mount runtime) with the operator's config already
    # mutated on disk. Same ordering as `fm bake`. The reload is what keeps the
    # overlay effective -- the first load read the pre-overlay file.
    bench = _load_image_bench(ctx, benchname)
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

    # Bake for where the image will RUN: a remote target's daemon arch fills
    # in when [build].platform is unset (explicit config always wins).
    deploy_platform = None
    if docker_host:
        remote_arch = remote_daemon_arch(docker_host)
        if remote_arch:
            deploy_platform = f"linux/{remote_arch}"

    try:
        bake_manager = BakeManager(bench.bench_config, output_handler=output)
        built_tag = bake_manager.bake(tag=tag, push=push, deploy_platform=deploy_platform)
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
            orchestrator.deploy(built_tag, rolling=rolling, prune_keep=keep)
    except DeployError as e:
        output.display_error(str(e))
        raise typer.Exit(1) from e


@example(
    "Deploy a tag you baked earlier",
    "{benchname} local/mybench:20260721-abc123",
    detail="The everyday forward deploy: `fm bake` printed this tag (also in `fm list`). Runs backup, "
    "migrate per config, swap, and records the tag in deploy history.",
    benchname="mybench",
)
@example(
    "Deploy a tag from a registry",
    "{benchname} ghcr.io/acme/mybench:v15.2.1",
    detail="Pulls with your ambient docker login if the image is not local. Typical on a prod box "
    "where CI pushed the image.",
    benchname="mybench",
)
@example(
    "Roll back a bad deploy",
    "{benchname} --previous",
    detail="The 3am command. Returns to the last deployed tag with migrate disabled automatically "
    "(old code must never migrate a newer schema). Run it again to undo the rollback.",
    benchname="mybench",
)
@example(
    "Roll back further than one release",
    "{benchname} local/mybench:20260718-9f21e0 --no-migrate",
    detail="--previous only knows the last tag; for anything older pass the tag explicitly "
    "(recorded in bench_config.toml deploy history) and keep migrate off.",
    benchname="mybench",
)
@example(
    "Undo a bad migration (code AND database)",
    "{benchname} --previous --restore-db",
    detail="Also restores the DB dump recorded during the current deploy, so code and schema go back "
    "together. Runs under the maintenance window like a migrate.",
    benchname="mybench",
)
@example(
    "Ship a code-only hotfix without the migrate ceremony",
    "{benchname} local/mybench:20260721-hotfix1 --no-migrate",
    detail="Skips migrate, and with it the maintenance window -- which makes the zero-downtime "
    "rolling swap eligible. Fastest safe path for template/py-only fixes.",
    benchname="mybench",
)
@example(
    "Force the zero-downtime rolling swap",
    "{benchname} local/mybench:20260722-def456 --rolling",
    detail="Old and new web replicas serve side by side, then the old drains away. Only force it when "
    "both versions work against the same DB schema; --no-rolling forces the plain recreate instead.",
    benchname="mybench",
)
def switch(
    ctx: typer.Context,
    benchname: RequiredBenchNameArgument,
    tag: Annotated[
        str | None,
        typer.Argument(help="Image tag to switch to. Omit when using --previous.", show_default=False),
    ] = None,
    previous: Annotated[
        bool,
        typer.Option("--previous", help="Roll back to the previously deployed tag (disables migrate)."),
    ] = False,
    migrate: Annotated[
        bool | None,
        typer.Option(
            "--migrate/--no-migrate",
            help="Force or skip bench migrate for this run (overrides bench config).",
            show_default=False,
        ),
    ] = None,
    restore_db: Annotated[
        bool,
        typer.Option("--restore-db", help="Also restore the DB dump recorded during the current deploy."),
    ] = False,
    keep: Annotated[
        int | None,
        typer.Option(
            "--keep",
            help="After a successful deploy, prune old releases keeping the newest N (minimum 1; see fm prune).",
            show_default=False,
        ),
    ] = None,
    rolling: Annotated[
        bool | None,
        typer.Option(
            "--rolling/--no-rolling",
            help="Force/disable the rolling web swap (default: auto when the overlap is safe).",
            show_default=False,
        ),
    ] = None,
):
    """
    Switch a bench to an already-built image tag, or roll back.

    Forward deploys and rollbacks are the same pipeline pointed at different tags: fetch -> pre-flight -> backup -> migrate (per config) -> swap (rolling when safe) -> record. With --previous, migrate is disabled so old code never runs against a newer schema.
    """
    output = get_global_output_handler()
    _reject_impossible_keep(output, keep)
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
        orchestrator.deploy(target, rolling=rolling, migrate_override=migrate, restore_db_dump=dump, prune_keep=keep)
    except DeployError as e:
        output.display_error(str(e))
        raise typer.Exit(1) from e


@example(
    "Preview what a prune would remove",
    "{benchname} --dry-run",
    detail="Lists the history entries, backup dirs, and local image tags that would go. Nothing is touched.",
    benchname="mybench",
)
@example(
    "Prune old releases now",
    "{benchname}",
    detail="Keeps the newest releases per keep_releases in bench config (default 7); current and "
    "previous tags are always safe. Also available inline: --keep N on fm deploy/switch.",
    benchname="mybench",
)
@example(
    "Keep only the last 3 releases",
    "{benchname} --keep 3",
    detail="One-off override of the configured retention.",
    benchname="mybench",
)
def prune(
    ctx: typer.Context,
    benchname: RequiredBenchNameArgument,
    keep: Annotated[
        int | None,
        typer.Option(
            "--keep",
            help="Retain this many releases, minimum 1 (overrides bench config).",
            show_default=False,
        ),
    ] = None,
    dry_run: Annotated[
        bool,
        typer.Option("--dry-run", help="Report what would be pruned without deleting anything."),
    ] = False,
):
    """
    Remove old deploy releases (history, DB dumps, unused image tags).

    Keeps the newest N releases per keep_releases in bench config (--keep overrides). Current and previous tags -- and any dump a kept release still references -- are never touched.
    """
    output = get_global_output_handler()
    _reject_impossible_keep(output, keep)
    bench = _load_image_bench(ctx, benchname)

    try:
        orchestrator = DeployOrchestrator(bench, output_handler=output)
        summary = orchestrator.prune_releases(keep=keep, dry_run=dry_run)
    except DeployError as e:
        output.display_error(str(e))
        raise typer.Exit(1) from e

    if not summary["entries"]:
        output.print(f"Nothing to prune ({summary['kept']} release(s) recorded, all within retention).")
        return
    if dry_run:
        output.print(f"Would prune {summary['entries']} release(s), keep {summary['kept']}:")
        for backup_dir in summary["backups"]:
            output.print(f"backup dir  {backup_dir}", emoji_code="", prefix="  ")
        for image in summary["images"]:
            output.print(f"image tag   {image}", emoji_code="", prefix="  ")
