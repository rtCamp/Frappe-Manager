from pathlib import Path
from typing import Annotated

import typer
from typer_examples import example

from frappe_manager.commands.arguments import RequiredBenchNameArgument
from frappe_manager.output_manager import get_global_output_handler
from frappe_manager.site_manager.bench_config import BenchRuntime
from frappe_manager.site_manager.modules.deploy_orchestrator import DeployError, DeployOrchestrator
from frappe_manager.site_manager.site import Bench


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


def _find_current_deploy_backups(state) -> "tuple[dict[str, str], str | None]":
    """({site: dump_path}, error) -- the pre-migrate DB dumps recorded for the CURRENT deploy.

    The dumps taken while deploying the current (bad) tag are the exact pre-migrate
    state; restoring them alongside the code rollback undoes a bad migrate.

    Every site, not one: each site has its own schema, so a rollback that restored only
    one would leave the others migrated against code that is being rolled back under them.
    """
    current = state.current_tag if state else None
    if not current:
        return {}, "No current deploy recorded; nothing to restore."
    entries = [e for e in (state.history or []) if e.tag == current and e.backups]
    if not entries:
        return {}, (
            f"No DB backup recorded for the current deploy ({current}). "
            f"Dumps live under <bench>/backups/deploy-*/ -- restore manually if one exists."
        )
    return entries[-1].backups, None


@example(
    "Switch to a tag you baked",
    "{benchname} local/mybench:20260721-abc123",
    detail="fm bake prints the tag; fm info lists the ones this bench has already run.",
    benchname="mybench",
)
@example(
    "Switch to a tag from a registry",
    "{benchname} ghcr.io/acme/mybench:v15.2.1",
    detail="Pulled with your ambient docker login when it is not already local.",
    benchname="mybench",
)
@example(
    "Roll back the last deploy",
    "{benchname} --previous",
    benchname="mybench",
)
@example(
    "Roll back code and database together",
    "{benchname} --previous --restore-db",
    detail="For when the migration is the problem: the dump taken before it goes back with the older code.",
    benchname="mybench",
)
@example(
    "Roll back code and database unattended",
    "{benchname} --previous --restore-db --yes",
    detail="Without --yes fm asks you to type the schema name, and refuses when there is no terminal to ask on.",
    benchname="mybench",
)
@example(
    "Roll back more than one release",
    "{benchname} local/mybench:20260718-9f21e0 --no-migrate",
    detail="--previous only knows the last tag, so name an older one explicitly and keep migrate off.",
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
        typer.Option("--previous", help="Roll back to the previously deployed tag, with migrate disabled."),
    ] = False,
    migrate: Annotated[
        bool | None,
        typer.Option(
            "--migrate/--no-migrate",
            help="Force or skip bench migrate for this run, overriding the bench config.",
            show_default=False,
        ),
    ] = None,
    restore_db: Annotated[
        bool,
        typer.Option(
            "--restore-db",
            help="Also restore the DB dump taken during the deploy you are undoing. This REPLACES the current database: the dump drops and recreates every table, so everything written since that deploy is lost. fm asks you to confirm before importing.",
        ),
    ] = False,
    yes: Annotated[
        bool,
        typer.Option(
            "--yes",
            "-y",
            help="Accept the --restore-db overwrite without being asked. The only way to restore a dump unattended, and the only thing this flag skips.",
        ),
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
            help="Force or disable the rolling web swap; the default is automatic whenever the overlap is safe. Forcing it is only safe when both versions run against the same database schema.",
            show_default=False,
        ),
    ] = None,
):
    """
    Switch a bench to an already-built image tag, or roll back.

    Every switch records the tag you left, so --previous returns to it; run it twice and you are back where you started. Older releases stay until fm prune clears them.
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

    dumps: dict[str, Path] = {}
    if restore_db:
        recorded, error = _find_current_deploy_backups(state)
        if error:
            output.display_error(error)
            raise typer.Exit(1)
        missing = [p for p in recorded.values() if not Path(p).exists()]
        if missing:
            # All or nothing: restoring the sites whose dumps survive would leave the bench
            # split across two points in time, which is harder to reason about than not starting.
            output.display_error(f"Recorded DB backup(s) missing on disk: {', '.join(sorted(missing))}")
            raise typer.Exit(1)
        dumps = {site: Path(p) for site, p in recorded.items()}

    try:
        orchestrator = DeployOrchestrator(bench, output_handler=output)
        orchestrator.deploy(
            target,
            rolling=rolling,
            migrate_override=migrate,
            restore_db_dumps=dumps,
            prune_keep=keep,
            restore_confirmed=yes,
        )
    except DeployError as e:
        output.display_error(str(e))
        raise typer.Exit(1) from e


@example(
    "See what a prune would remove",
    "{benchname} --dry-run",
    benchname="mybench",
)
@example(
    "Prune old releases now",
    "{benchname}",
    detail="fm switch can do the same inline with --keep N.",
    benchname="mybench",
)
@example(
    "Keep only the last 3 releases",
    "{benchname} --keep 3",
    benchname="mybench",
)
def prune(
    ctx: typer.Context,
    benchname: RequiredBenchNameArgument,
    keep: Annotated[
        int | None,
        typer.Option(
            "--keep",
            help="Keep this many releases instead of the configured keep_releases. Minimum 1: the current release is never pruned.",
            show_default=False,
        ),
    ] = None,
    dry_run: Annotated[
        bool,
        typer.Option("--dry-run", help="Report what would be pruned without deleting anything."),
    ] = False,
):
    """
    Delete old deploy releases: history rows, their DB dumps, and their local image tags.

    Keeps the newest keep_releases from the bench config (7 by default) or --keep. Nothing else is touched: a dump or image survives while a kept release, the current or previous tag, or the seed or base image still needs it, so rollback stays possible.
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
