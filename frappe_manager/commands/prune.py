"""`fm prune`: one bench's disk hygiene -- releases, backup sessions, log rotation.

Cleanup in fm is COMMAND-TRIGGERED only: nothing prunes as a side effect of a deploy or a
migration (those print a size-aware hint at most). This command is the bench-tier trigger;
`fm services prune` is the host-tier one.

Three categories, all run by default, narrowed with --only:

- releases: deploy history rows, their unreferenced DB-dump dirs and local images
  (exactly the old `fm prune`; retention from [switch].keep_releases or --keep-releases)
- backups:  migration and workers backup sessions under <bench>/backups/
  (retention from [prune].keep_backup_sessions, bench overriding host)
- logs:     gzip + truncate-in-place rotation of the bench's frappe and nginx logs
  (threshold [prune].rotate_logs_over, archives kept per [prune].keep_log_archives)

Deliberately whitelisted from the migration gate: reclaiming disk must work on a bench that
is behind (a full disk is exactly when you cannot migrate). Holds the bench's EXCLUSIVE
lock: it deletes state a concurrent switch/bake would be reading.
"""

from enum import Enum
from typing import Annotated

import typer
from typer_examples import example

from frappe_manager.commands.arguments import RequiredBenchNameArgument
from frappe_manager.metadata_manager import FMConfigManager
from frappe_manager.output_manager import get_global_output_handler
from frappe_manager.site_manager.bench_config import BenchRuntime
from frappe_manager.site_manager.site import Bench
from frappe_manager.utils.process_lock import bench_lock
from frappe_manager.utils.prune import (
    LogPrune,
    SessionPrune,
    execute_log_prune,
    execute_session_prune,
    format_size,
    parse_size,
    plan_log_prune,
    plan_session_prune,
)


class PruneCategory(str, Enum):
    releases = "releases"
    backups = "backups"
    logs = "logs"


def resolve_prune_settings(
    fm_config: FMConfigManager,
    bench_prune,
    *,
    keep_backups: int | None = None,
    keep_logs: int | None = None,
    rotate_over: str | None = None,
) -> tuple[int, int, int]:
    """(keep_backup_sessions, keep_log_archives, rotate_over_bytes).

    Precedence per key: command flag -> bench [prune] -> host [prune] -> built-in default
    (the host model's defaults). ``bench_prune`` is a BenchPruneConfig or None.
    """
    host = fm_config.prune

    def pick(flag, bench_value, host_value):
        if flag is not None:
            return flag
        if bench_value is not None:
            return bench_value
        return host_value

    sessions = pick(keep_backups, getattr(bench_prune, "keep_backup_sessions", None), host.keep_backup_sessions)
    archives = pick(keep_logs, getattr(bench_prune, "keep_log_archives", None), host.keep_log_archives)
    over = pick(rotate_over, getattr(bench_prune, "rotate_logs_over", None), host.rotate_logs_over)
    return int(sessions), int(archives), parse_size(over)


def report_session_plans(output, plans: list[SessionPrune], dry_run: bool) -> int:
    """One `Backups :` line naming each root's stale/kept counts and size, then every
    session directory that goes, one row each -- same shape the releases category has
    always used for its backup dirs. Deletions are never anonymous. Returns bytes."""
    parts = []
    total = 0
    for plan in plans:
        if not plan.count:
            continue
        total += plan.size
        parts.append(f"{plan.root.name}: {plan.count} session(s) beyond keep {plan.kept} ({format_size(plan.size)})")
    verb = "would remove" if dry_run else "removed"
    if parts:
        output.print(f"Backups  : {verb} " + " · ".join(parts), emoji_code="")
        for plan in plans:
            for stale in plan.stale:
                output.print(f"session     {stale}", emoji_code="", prefix="  ")
    else:
        output.print("Backups  : nothing beyond retention", emoji_code="")
    return total


def report_log_plan(output, plan: LogPrune, dry_run: bool) -> int:
    """One `Logs :` line (files rotated with sizes, archives dropped), then one row per
    touched path: `rotate` for a live file about to be archived+truncated, `drop` for an
    old archive leaving the disk. Returns bytes."""
    verb = "would rotate" if dry_run else "rotated"
    if not plan.rotations and not plan.drop_count:
        output.print("Logs     : nothing over the rotation threshold", emoji_code="")
        return 0
    parts = []
    if plan.rotations:
        names = ", ".join(f"{r.path.name} ({format_size(r.size)})" for r in plan.rotations)
        parts.append(f"{verb} {names}")
    if plan.drop_count:
        parts.append(f"{'would drop' if dry_run else 'dropped'} {plan.drop_count} old archive(s)")
    output.print("Logs     : " + " · ".join(parts), emoji_code="")
    for rotation in plan.rotations:
        output.print(f"rotate      {rotation.path}", emoji_code="", prefix="  ")
        for old in rotation.archives_to_drop:
            output.print(f"drop        {old}", emoji_code="", prefix="  ")
    for old in plan.archives_to_drop:
        output.print(f"drop        {old}", emoji_code="", prefix="  ")
    return plan.rotate_size


@example(
    "See what a prune would remove, without removing it",
    "{benchname} --dry-run",
    benchname="mybench",
)
@example(
    "Everything: old releases, old backup sessions, oversized logs",
    "{benchname}",
    detail="Shows the full plan (every path) first, then asks. A bare Enter aborts; type y to proceed, or pass --yes.",
    benchname="mybench",
)
@example(
    "Only rotate the logs",
    "{benchname} --only logs",
    benchname="mybench",
)
@example(
    "Keep only the last 3 releases",
    "{benchname} --only releases --keep-releases 3",
    benchname="mybench",
)
@bench_lock(operation="prune")
def prune(
    ctx: typer.Context,
    benchname: RequiredBenchNameArgument,
    only: Annotated[
        list[PruneCategory] | None,
        typer.Option(
            "--only",
            help="Run only this category (repeatable): releases, backups, logs. Default: all three.",
            show_default=False,
        ),
    ] = None,
    keep_releases: Annotated[
        int | None,
        typer.Option(
            "--keep-releases",
            "--keep",
            help="Releases to keep instead of \\[switch].keep_releases. Minimum 1: the current release is never pruned.",
            show_default=False,
        ),
    ] = None,
    keep_backups: Annotated[
        int | None,
        typer.Option(
            "--keep-backups",
            help="Backup sessions to keep per location instead of \\[prune].keep_backup_sessions.",
            show_default=False,
        ),
    ] = None,
    keep_logs: Annotated[
        int | None,
        typer.Option(
            "--keep-logs",
            help="Rotated archives to keep per log file instead of \\[prune].keep_log_archives.",
            show_default=False,
        ),
    ] = None,
    rotate_over: Annotated[
        str | None,
        typer.Option(
            "--rotate-over",
            help="Rotate log files larger than this (e.g. '500K', '10M') instead of \\[prune].rotate_logs_over.",
            show_default=False,
        ),
    ] = None,
    yes: Annotated[
        bool,
        typer.Option("--yes", "-y", help="Prune without asking for confirmation."),
    ] = False,
    dry_run: Annotated[
        bool,
        typer.Option("--dry-run", help="Print the plan and exit without deleting anything; never prompts."),
    ] = False,
):
    """
    Reclaim this bench's disk: old deploy releases, old backup sessions, oversized logs.

    Plan-first: the full plan (every path that would be touched) is printed, then one confirmation covers it; a bare Enter aborts, --yes skips the question, --dry-run stops after the plan. Runs all three categories by default; narrow with --only. Retention comes from the \\[prune] table (bench overriding host) and \\[switch].keep_releases; flags override for one run. Log rotation copies to <name>.log.<timestamp>.gz and truncates the live file in place, because the writing processes hold it open. Nothing in fm cleans up on its own: this command (and fm services prune for the host tier) is the only trigger.
    """
    from frappe_manager.commands.deploy import _reject_impossible_keep
    from frappe_manager.site_manager.modules.deploy_orchestrator import DeployError, DeployOrchestrator

    output = get_global_output_handler()
    _reject_impossible_keep(output, keep_releases)

    services_manager = ctx.obj["services"]
    fm_config: FMConfigManager = ctx.obj["fm_config_manager"]
    bench = Bench.get_object(benchname, services_manager, output_handler=output)

    categories = set(only) if only else set(PruneCategory)
    keep_sessions, keep_archives, over_bytes = resolve_prune_settings(
        fm_config,
        bench.bench_config.prune,
        keep_backups=keep_backups,
        keep_logs=keep_logs,
        rotate_over=rotate_over,
    )

    # ---- plan everything first; the report below is exactly what execution will do.
    total = 0

    release_summary = None
    if PruneCategory.releases in categories:
        if bench.bench_config.runtime != BenchRuntime.image:
            output.print("Releases : not an image-runtime bench, nothing to prune", emoji_code="")
        else:
            try:
                release_summary = DeployOrchestrator(bench, output_handler=output).prune_releases(
                    keep=keep_releases, dry_run=True
                )
            except DeployError as e:
                output.display_error(str(e))
                raise typer.Exit(1) from e
            if not release_summary["entries"]:
                output.print(
                    f"Releases : nothing to prune ({release_summary['kept']} release(s) recorded, all within retention)",
                    emoji_code="",
                )
            else:
                detail = f"would prune {release_summary['entries']} release(s), keep {release_summary['kept']}"
                if release_summary["backups"]:
                    detail += f" · {len(release_summary['backups'])} backup dir(s)"
                if release_summary["images"]:
                    detail += f" · {len(release_summary['images'])} image(s)"
                output.print(f"Releases : {detail}", emoji_code="")
                for backup_dir in release_summary["backups"]:
                    output.print(f"backup dir  {backup_dir}", emoji_code="", prefix="  ")
                for image in release_summary["images"]:
                    output.print(f"image       {image}", emoji_code="", prefix="  ")

    session_plans = []
    if PruneCategory.backups in categories:
        session_plans = [
            plan_session_prune(bench.path / "backups" / group, keep_sessions) for group in ("migrations", "workers")
        ]
        total += report_session_plans(output, session_plans, dry_run=True)

    log_plan = None
    if PruneCategory.logs in categories:
        log_dirs = [
            bench.path / "workspace" / "frappe-bench" / "logs",
            bench.path / "configs" / "nginx" / "logs",
        ]
        log_plan = plan_log_prune(log_dirs, over_bytes, keep_archives)
        total += report_log_plan(output, log_plan, dry_run=True)

    if total:
        output.print(f"Total    : ~{format_size(total)} reclaimable", emoji_code="")

    nothing_to_do = (
        (release_summary is None or not release_summary["entries"])
        and not any(plan.count for plan in session_plans)
        and (log_plan is None or (not log_plan.rotations and not log_plan.drop_count))
    )
    if nothing_to_do or dry_run:
        return

    # ---- one confirmation covers everything shown above; a bare Enter aborts.
    if not yes:
        choice = output.prompt_ask(
            prompt="Proceed with the deletions and rotations listed above? (default: no)",
            choices=["yes", "no"],
            default="no",
            required_flag="--yes",
        )
        if choice != "yes":
            output.print("Aborted; nothing touched.", emoji_code="")
            raise typer.Exit(1)

    # ---- execute exactly the plans that were shown.
    reclaimed = 0
    if release_summary is not None and release_summary["entries"]:
        try:
            DeployOrchestrator(bench, output_handler=output).prune_releases(keep=keep_releases, dry_run=False)
        except DeployError as e:
            output.display_error(str(e))
            raise typer.Exit(1) from e
    for plan in session_plans:
        reclaimed += plan.size
        execute_session_prune(plan)
    if log_plan is not None:
        reclaimed += log_plan.rotate_size
        execute_log_prune(log_plan)

    output.print(f"Done     : ~{format_size(reclaimed)} reclaimed", emoji_code="")
