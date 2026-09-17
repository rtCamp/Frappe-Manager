"""`fm services prune`: the host tier's disk hygiene.

The bench-tier counterpart is `fm prune BENCH`; this one covers what belongs to fm itself
and to the shared services:

- backups: host-tier migration backup sessions (~/frappe/backups/migrations/<ts>/) and the
  wholesale `services_<date>` directory backups a services recreate leaves in
  ~/frappe/backups/ -- both kept to [prune].keep_backup_sessions
- logs:    the shared services' log files (~/frappe/services/mariadb/logs/,
  ~/frappe/services/nginx-proxy/logs/), gzip + truncate-in-place like the bench command.
  fm.log is excluded: it rotates itself.

Purely path-based: it never talks to docker or constructs a ServicesManager, so it works on
a stopped stack and on an install whose services tier is behind (it is whitelisted from the
migration gate for exactly that reason -- a full disk is when you cannot migrate). Holds
the ordinary SHARED host grip like any command, so a running migration (exclusive) refuses
it and it refuses to start mid-migration, with no machinery of its own.
"""

from pathlib import Path
from typing import Annotated

import typer
from typer_examples import example

from frappe_manager import CLI_DIR, CLI_SERVICES_DIRECTORY
from frappe_manager.metadata_manager import FMConfigManager
from frappe_manager.output_manager import get_global_output_handler
from frappe_manager.utils.prune import (
    execute_log_prune,
    execute_session_prune,
    format_size,
    parse_size,
    plan_log_prune,
    plan_session_prune,
)


def _legacy_services_dirs(backups_root: Path, keep: int) -> list[Path]:
    """`services_<date>` wholesale backups beyond the newest ``keep``, oldest first.

    A services recreate renames the whole services directory here; nothing else manages
    those dirs, so they are retained like sessions of their own group.
    """
    if not backups_root.is_dir():
        return []
    dirs = [p for p in backups_root.iterdir() if p.is_dir() and p.name.startswith("services_")]
    dirs.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    return list(reversed(dirs[keep:]))


@example("See what a prune would remove, without removing it", "--dry-run")
@example("Reclaim host-tier disk: old backup sessions, oversized service logs", "")
@example("Only rotate the shared services' logs", "--only logs")
def prune_services(
    ctx: typer.Context,
    only: Annotated[
        str | None,
        typer.Option(
            "--only",
            help="Run only this category: backups or logs. Default: both.",
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
    dry_run: Annotated[
        bool,
        typer.Option("--dry-run", help="Report what would be pruned without deleting anything."),
    ] = False,
):
    """
    Reclaim the host tier's disk: fm's own backup sessions and the shared services' logs.

    Covers ~/frappe/backups (migration sessions and services_<date> wholesale backups) and the shared services' log files. Retention comes from the \\[prune] table in fm_config.toml; flags override for one run. fm.log is not touched: it rotates itself. The bench tier has its own command, fm prune BENCH.
    """
    if only is not None and only not in ("backups", "logs"):
        raise typer.BadParameter(f"Unknown category '{only}': expected backups or logs.")

    output = get_global_output_handler()
    fm_config: FMConfigManager = ctx.obj["fm_config_manager"]
    keep_sessions = keep_backups if keep_backups is not None else fm_config.prune.keep_backup_sessions
    keep_archives = keep_logs if keep_logs is not None else fm_config.prune.keep_log_archives
    over_bytes = parse_size(rotate_over if rotate_over is not None else fm_config.prune.rotate_logs_over)

    from frappe_manager.utils.prune import dir_size

    backups_root = CLI_DIR / "backups"
    total = 0

    if only in (None, "backups"):
        plan = plan_session_prune(backups_root / "migrations", keep_sessions)
        legacy = _legacy_services_dirs(backups_root, keep_sessions)
        legacy_size = sum(dir_size(p) for p in legacy)
        verb = "would remove" if dry_run else "removed"
        parts = []
        if plan.count:
            parts.append(f"migrations: {plan.count} session(s) beyond keep {plan.kept} ({format_size(plan.size)})")
            total += plan.size
        if legacy:
            parts.append(f"services_*: {len(legacy)} old dir(s) ({format_size(legacy_size)})")
            total += legacy_size
        if parts:
            output.print(f"Backups  : {verb} " + " · ".join(parts), emoji_code="")
            for stale in plan.stale:
                output.print(f"session     {stale}", emoji_code="", prefix="  ")
            for stale in legacy:
                output.print(f"session     {stale}", emoji_code="", prefix="  ")
        else:
            output.print("Backups  : nothing beyond retention", emoji_code="")
        if not dry_run:
            execute_session_prune(plan)
            import shutil

            for stale in legacy:
                shutil.rmtree(stale, ignore_errors=True)

    if only in (None, "logs"):
        from frappe_manager.commands.prune import report_log_plan

        log_dirs = [
            CLI_SERVICES_DIRECTORY / "mariadb" / "logs",
            CLI_SERVICES_DIRECTORY / "nginx-proxy" / "logs",
        ]
        log_plan = plan_log_prune(log_dirs, over_bytes, keep_archives)
        total += report_log_plan(output, log_plan, dry_run)
        if not dry_run:
            execute_log_prune(log_plan)

    if total:
        verb = "reclaimable" if dry_run else "reclaimed"
        output.print(f"Total    : ~{format_size(total)} {verb}", emoji_code="")
