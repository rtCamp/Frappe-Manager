from pathlib import Path
from typing import Annotated

import typer
from typer_examples import example

from frappe_manager.commands.arguments import RequiredBenchNameArgument
from frappe_manager.output_manager import get_global_output_handler
from frappe_manager.site_manager.bench_config import BenchRuntime
from frappe_manager.site_manager.modules.deploy_orchestrator import DeployError, DeployOrchestrator
from frappe_manager.site_manager.site import Bench
from frappe_manager.utils.helpers import digest_pinned_refusal, has_explicit_tag, is_digest_pinned
from frappe_manager.utils.process_lock import bench_lock


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


def _switch_target_shape_error(image: str) -> str | None:
    """Why ``image`` cannot be a switch target, or ``None`` when it is a valid full reference."""
    if is_digest_pinned(image):
        return digest_pinned_refusal(image)
    if not has_explicit_tag(image):
        return (
            f"'{image}' is not a full image reference: pass repo:tag (e.g. "
            f"ghcr.io/acme/mybench:v15.2.1), not a bare tag."
        )
    return None


def _resolve_switch_image(state, image: str | None, previous: bool) -> tuple[str | None, str | None]:
    """(target_image, error) for ``fm switch``: explicit IMAGE xor ``--previous``."""
    if image and previous:
        return None, "Pass either an explicit image or --previous, not both."
    if previous:
        prev = state.previous_image if state else None
        if not prev:
            return None, "No previous image recorded; nothing to roll back to (pass an explicit image)."
        return prev, None
    if not image:
        return None, "Missing target: pass an image reference or --previous."
    error = _switch_target_shape_error(image)
    if error:
        return None, error
    return image, None


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

    The dumps taken while deploying the current (bad) image are the exact pre-migrate
    state; restoring them alongside the code rollback undoes a bad migrate.

    Every site, not one: each site has its own schema, so a rollback that restored only
    one would leave the others migrated against code that is being rolled back under them.
    """
    current = state.current_image if state else None
    if not current:
        return {}, "No current deploy recorded; nothing to restore."
    entries = [e for e in (state.history or []) if e.image == current and e.backups]
    if not entries:
        return {}, (
            f"No DB backup recorded for the current deploy ({current}). "
            f"Dumps live under <bench>/backups/deploy-*/ -- restore manually if one exists."
        )
    return entries[-1].backups, None


@example(
    "Switch to an image you baked",
    "{benchname} local/mybench:20260721-abc123",
    detail="fm bake prints the image; fm info lists the ones this bench has already run.",
    benchname="mybench",
)
@example(
    "Switch to an image from a registry",
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
    detail="--previous only knows the last image, so name an older one explicitly and keep migrate off.",
    benchname="mybench",
)
@bench_lock(operation="switch")
def switch(
    ctx: typer.Context,
    benchname: RequiredBenchNameArgument,
    image: Annotated[
        str | None,
        typer.Argument(
            help="Image to switch to: a full reference such as ghcr.io/acme/mybench:v15.2.1. Omit when using --previous.",
            show_default=False,
        ),
    ] = None,
    previous: Annotated[
        bool,
        typer.Option("--previous", help="Roll back to the previously deployed image, with migrate disabled."),
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
    Switch a bench to an already-built image, or roll back.

    A switch is not just an image change. By default it takes a database backup, raises a maintenance page for the schema-changing steps, and runs bench migrate against the new image, so plan for the site to be briefly unavailable. Each of those is a \\[switch] config key and can be turned off there.
    """
    output = get_global_output_handler()

    _reject_impossible_keep(output, keep)
    bench = _load_image_bench(ctx, benchname)

    state = bench.bench_config.deploy_state
    target, error = _resolve_switch_image(state, image, previous)
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


