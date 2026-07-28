from enum import Enum
from typing import Annotated

import typer
from typer_examples import example
from rich.table import Table

from frappe_manager import CLI_BENCH_CONFIG_FILE_NAME, CLI_BENCHES_DIRECTORY
from frappe_manager.metadata_manager import FMConfigManager
from frappe_manager.migration_manager.bench_migration_state import (
    get_bench_migration_version,
    set_bench_migration_version,
)
from frappe_manager.migration_manager.migration_executor import MigrationExecutor
from frappe_manager.migration_manager.version import Version
from frappe_manager.output_manager import get_global_output_handler, spinner
from frappe_manager.utils.helpers import get_current_fm_version


class MigrationFailureAction(str, Enum):
    """Actions to take when migration fails."""

    prompt = "prompt"
    archive = "archive"
    rollback = "rollback"


@example(
    "Migrate FM infrastructure only (safe)",
    "",
    detail="Applies only FM infrastructure migrations without touching benches. Safe to run for CLI updates.",
)
@example(
    "Migrate specific bench",
    "{benchname}",
    detail="Runs migration steps for a specific bench to bring it in line with the FM version.",
    benchname="mybench",
)
@example(
    "Migrate all benches",
    "--all-benches",
    detail="Applies migrations to all benches managed by FM. Use with caution and consider backups.",
)
@example(
    "Skip confirmation prompt",
    "--all-benches --auto-proceed",
    detail="Runs migrations across all benches without interactive prompts. Useful for automation.",
)
@example(
    "Auto-proceed with auto-rollback on failure",
    "--all-benches --auto-proceed --on-failure=rollback",
    detail="Automatically proceeds with migrations and rolls back if a failure occurs.",
)
@example(
    "Auto-proceed, archive failed benches (partial success OK)",
    "--all-benches --auto-proceed --on-failure=archive",
    detail="Archives benches that fail migration while continuing others; useful for large fleets.",
)
@example(
    "Skip all backups (dangerous)",
    "--all-benches --skip-all-backup",
    detail="Disables taking backups before migration. This is risky and should only be used in controlled scenarios.",
)
@example(
    "Exclude specific benches",
    "--all-benches --exclude-bench mybench1,mybench2",
    detail="Excludes specific benches from a full migration run.",
)
def migrate(
    ctx: typer.Context,
    benchname: Annotated[
        str | None,
        typer.Argument(help="Bench name to migrate"),
    ] = None,
    all_benches: Annotated[
        bool,
        typer.Option("--all-benches", help="Migrate all benches"),
    ] = False,
    skip_backup: Annotated[
        bool,
        typer.Option("--skip-all-backup", help="Skip all backups (DANGEROUS - use only if backups fail)"),
    ] = False,
    skip_backup_for: Annotated[
        str | None,
        typer.Option("--skip-backup-for", help="Skip backup for specific benches (comma-separated)"),
    ] = None,
    exclude_bench: Annotated[
        str | None,
        typer.Option("--exclude-bench", help="Exclude specific benches from migration (only with --all-benches)"),
    ] = None,
    auto_proceed: Annotated[
        bool,
        typer.Option("--auto-proceed", help="Skip migration confirmation prompt (proceed automatically)"),
    ] = False,
    rerun: Annotated[
        bool,
        typer.Option(
            "--rerun",
            help="Re-run migration even if already migrated (for testing idempotency). "
            "Config transforms and supervisor regeneration are re-applied, but the "
            "runtime environment is only rebuilt when Python/Node versions change.",
        ),
    ] = False,
    on_failure: Annotated[
        MigrationFailureAction | None,
        typer.Option(
            "--on-failure",
            help="What to do if migration fails: prompt (ask user), archive (save failed benches), rollback (revert all)",
        ),
    ] = None,
):
    """
    Migrate Frappe Manager to current version.

    Migration operates at two levels:
    - FM Infrastructure: CLI config + global database services (always checked and migrated if needed)
    - Benches: Individual bench environments (you choose which ones to migrate)

    Without arguments, migrates only FM infrastructure. Specify a benchname to migrate that bench, or use --all-benches to migrate all benches. Use --auto-proceed to skip confirmation prompts. Control failure handling with --on-failure: prompt (ask), archive (save failed), or rollback (revert all). Use --rerun to test idempotency — re-applies all migration steps even when already up to date.
    """
    fm_config_manager: FMConfigManager = ctx.obj["fm_config_manager"]
    output = get_global_output_handler()

    failure_action = on_failure.value if on_failure else "prompt"

    if benchname and all_benches:
        output.display_error("Cannot specify both <benchname> and --all-benches")
        output.stop()
        typer.echo(ctx.get_help())
        raise typer.Exit(1)

    if exclude_bench and not all_benches:
        output.display_error("--exclude-bench can only be used with --all-benches")
        output.stop()
        typer.echo(ctx.get_help())
        raise typer.Exit(1)

    current_version = Version(get_current_fm_version())

    skip_backup_list = []
    if skip_backup_for:
        skip_backup_list = [b.strip() for b in skip_backup_for.split(",")]

    exclude_bench_list = []
    if exclude_bench:
        exclude_bench_list = [b.strip() for b in exclude_bench.split(",")]

    target_benches = None
    if benchname:
        bench_path = CLI_BENCHES_DIRECTORY / benchname
        if not bench_path.exists():
            output.display_error(f"Bench '{benchname}' does not exist")
            raise typer.Exit(1)
        target_benches = [benchname]
    elif all_benches:
        target_benches = []
        if CLI_BENCHES_DIRECTORY.exists():
            for bench_path in CLI_BENCHES_DIRECTORY.iterdir():
                if bench_path.is_dir() and (bench_path / CLI_BENCH_CONFIG_FILE_NAME).exists():
                    if bench_path.name not in exclude_bench_list:
                        target_benches.append(bench_path.name)

    fm_infrastructure_version = fm_config_manager.get_system_migration_version()
    fm_infrastructure_needs_migration = rerun or (fm_infrastructure_version < current_version)

    benches_checked = []
    benches_migrated = []
    benches_skipped = []
    benches_failed = []

    if not fm_infrastructure_needs_migration and not target_benches:
        output.print("✓ FM infrastructure already up to date (no benches specified)")
        raise typer.Exit(0)

    if target_benches:
        for bench_name in target_benches:
            bench_path = CLI_BENCHES_DIRECTORY / bench_name
            if bench_path.exists():
                bench_version = get_bench_migration_version(bench_path)
                benches_checked.append((bench_name, bench_version))

    output_handler = get_global_output_handler()

    migrations = MigrationExecutor(
        fm_config_manager,
        skip_backup=skip_backup,
        skip_backup_for=skip_backup_list,
        exclude_benches=exclude_bench_list,
        auto_proceed=auto_proceed,
        rerun=rerun,
        on_failure=failure_action,
        target_benches=target_benches,
        migrate_fm_infrastructure=fm_infrastructure_needs_migration,
        output_handler=output_handler,
    )

    with spinner(output_handler, "Starting migration..."):
        migration_status = migrations.execute()

    if not migration_status:
        raise typer.Exit(1)

    if target_benches:
        for bench_name in target_benches:
            if bench_name in migrations.migrate_benches:
                bench_data = migrations.migrate_benches[bench_name]
                last_migrated = bench_data["last_migration_version"]

                # Compare base versions to handle dev releases (0.19.0.dev0 matches 0.19.0)
                versions_match = (
                    last_migrated is not None and last_migrated.base_version == current_version.base_version
                )

                if versions_match and not bench_data["exception"]:
                    bench_path = CLI_BENCHES_DIRECTORY / bench_name
                    if bench_path.exists() and (bench_path / CLI_BENCH_CONFIG_FILE_NAME).exists():
                        set_bench_migration_version(bench_path, current_version)
                        benches_migrated.append(bench_name)
                elif bench_data["exception"]:
                    benches_failed.append(bench_name)
            else:
                benches_skipped.append(bench_name)

    if fm_infrastructure_needs_migration:
        fm_config_manager.set_system_migration_version(current_version)
        fm_config_manager.export_to_toml()

    table = Table(show_header=False, box=None, padding=(0, 2, 0, 0))

    show_infrastructure_status = fm_infrastructure_needs_migration or target_benches is None

    if show_infrastructure_status:
        if fm_infrastructure_needs_migration:
            table.add_row(
                "✅",
                "[fm.info]FM Infrastructure[/fm.info]",
                f"[fm.warn]v{fm_infrastructure_version}[/fm.warn] → [fm.ok]v{current_version}[/fm.ok]",
            )
        else:
            table.add_row(
                "⏭️ ",
                "[fm.info]FM Infrastructure[/fm.info]",
                f"[fm.warn]v{fm_infrastructure_version}[/fm.warn] (already up to date)",
            )

    if benches_migrated:
        for bench_name in benches_migrated:
            orig_version = next((v for n, v in benches_checked if n == bench_name), None)
            table.add_row(
                "✅",
                f"[fm.info]{bench_name}[/fm.info]",
                f"[fm.warn]v{orig_version}[/fm.warn] → [fm.ok]v{current_version}[/fm.ok]",
            )

    if benches_skipped:
        for bench_name in benches_skipped:
            orig_version = next((v for n, v in benches_checked if n == bench_name), None)
            table.add_row("⏭️ ", f"[fm.info]{bench_name}[/fm.info]", f"[fm.warn]v{orig_version}[/fm.warn] (already up to date)")

    if benches_failed:
        for bench_name in benches_failed:
            table.add_row("❌", f"[fm.info]{bench_name}[/fm.info]", "[fm.error]Migration failed[/fm.error]")

    output.print_data(table)

    if benches_failed:
        output.display_error("Check logs for details", emoji_code=":warning:")
