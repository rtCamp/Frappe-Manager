from typing import Annotated, Optional
from pathlib import Path

import typer

from frappe_manager.utils.helpers import get_current_fm_version, CLI_BENCHES_DIRECTORY
from frappe_manager import CLI_BENCH_CONFIG_FILE_NAME
from frappe_manager.metadata_manager import FMConfigManager
from frappe_manager.migration_manager.migration_executor import MigrationExecutor
from frappe_manager.migration_manager.bench_migration_state import (
    get_bench_migration_version,
    set_bench_migration_version,
)
from frappe_manager.migration_manager.version import Version
from frappe_manager.output_manager.context_managers import temporary_stop
from frappe_manager.display_manager.DisplayManager import richprint
from rich.table import Table


def migrate(
    ctx: typer.Context,
    benchname: Annotated[
        Optional[str],
        typer.Argument(help="Bench name to migrate"),
    ] = None,
    system: Annotated[
        bool,
        typer.Option("--system", help="Migrate system (FM config and global services)"),
    ] = False,
    all_benches: Annotated[
        bool,
        typer.Option("--all-benches", help="Migrate all benches"),
    ] = False,
    skip_backup: Annotated[
        bool,
        typer.Option("--skip-backup", help="Skip all backups (DANGEROUS - use only if backups fail)"),
    ] = False,
    skip_backup_for: Annotated[
        Optional[str],
        typer.Option("--skip-backup-for", help="Skip backup for specific benches (comma-separated)"),
    ] = None,
    exclude_bench: Annotated[
        Optional[str],
        typer.Option("--exclude-bench", help="Exclude specific benches from migration (only with --all-benches)"),
    ] = None,
    force: Annotated[
        bool,
        typer.Option("--force", help="Skip all confirmation prompts"),
    ] = False,
):
    """
    Migrate Frappe Manager to current version.

    Migration operates at two levels:
    - System: FM config and global services (use --system)
    - Benches: Individual bench environments (specify explicitly)
    """
    fm_config_manager: FMConfigManager = ctx.obj["fm_config_manager"]

    if benchname and all_benches:
        richprint.error("Cannot specify both <benchname> and --all-benches")
        richprint.stop()
        typer.echo(ctx.get_help())
        raise typer.Exit(1)

    if exclude_bench and not all_benches:
        richprint.error("--exclude-bench can only be used with --all-benches")
        richprint.stop()
        typer.echo(ctx.get_help())
        raise typer.Exit(1)

    if not system and not benchname and not all_benches:
        # Show help when no migration target specified
        richprint.stop()
        typer.echo(ctx.get_help())
        raise typer.Exit(0)

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
            richprint.error(f"Bench '{benchname}' does not exist")
            raise typer.Exit(1)
        target_benches = [benchname]
    elif all_benches:
        target_benches = []
        if CLI_BENCHES_DIRECTORY.exists():
            for bench_path in CLI_BENCHES_DIRECTORY.iterdir():
                if bench_path.is_dir() and (bench_path / CLI_BENCH_CONFIG_FILE_NAME).exists():
                    if bench_path.name not in exclude_bench_list:
                        target_benches.append(bench_path.name)

    # Track what was checked and what happened
    system_checked = system
    system_version = fm_config_manager.get_system_migration_version()
    system_needed_migration = system and system_version < current_version

    benches_checked = []
    benches_migrated = []
    benches_skipped = []
    benches_failed = []

    if system_needed_migration or (target_benches is not None and len(target_benches) > 0):
        # Check each bench version before migration
        if target_benches:
            for bench_name in target_benches:
                bench_path = CLI_BENCHES_DIRECTORY / bench_name
                if bench_path.exists():
                    bench_version = get_bench_migration_version(bench_path)
                    benches_checked.append((bench_name, bench_version))

        migrations = MigrationExecutor(
            fm_config_manager,
            skip_backup=skip_backup,
            skip_backup_for=skip_backup_list,
            exclude_benches=exclude_bench_list,
            force=force,
            target_benches=target_benches,
            migrate_system=system,
        )

        with temporary_stop(richprint):  # type: ignore[arg-type]  # DisplayManager duck types as OutputHandler
            migration_status = migrations.execute()

        if not migration_status:
            richprint.print(f"Rolled back to previous version of fm {migrations.prev_version}")
            raise typer.Exit(1)

        if target_benches:
            for bench_name in target_benches:
                if bench_name in migrations.migrate_benches:
                    bench_data = migrations.migrate_benches[bench_name]
                    if bench_data['last_migration_version'] == current_version and not bench_data['exception']:
                        bench_path = CLI_BENCHES_DIRECTORY / bench_name
                        if bench_path.exists() and (bench_path / CLI_BENCH_CONFIG_FILE_NAME).exists():
                            set_bench_migration_version(bench_path, current_version)
                            benches_migrated.append(bench_name)
                    elif bench_data['exception']:
                        benches_failed.append(bench_name)
                else:
                    # Bench was skipped (already at target version)
                    benches_skipped.append(bench_name)

        if system_needed_migration:
            fm_config_manager.set_system_migration_version(current_version)
            fm_config_manager.export_to_toml()

    # Show clean, aligned output using borderless table
    if system_checked or benches_checked:
        table = Table(show_header=False, box=None, padding=(0, 2, 0, 0))

        if system_checked:
            if system_needed_migration:
                table.add_row(
                    "✅",
                    "[cyan]System[/cyan]",
                    f"[yellow]v{system_version}[/yellow] → [green]v{current_version}[/green]",
                )
            else:
                table.add_row("⏭️ ", "[cyan]System[/cyan]", f"[yellow]v{system_version}[/yellow] (already up to date)")

        if benches_migrated:
            for bench_name in benches_migrated:
                orig_version = next((v for n, v in benches_checked if n == bench_name), None)
                table.add_row(
                    "✅",
                    f"[cyan]{bench_name}[/cyan]",
                    f"[yellow]v{orig_version}[/yellow] → [green]v{current_version}[/green]",
                )

        if benches_skipped:
            for bench_name in benches_skipped:
                orig_version = next((v for n, v in benches_checked if n == bench_name), None)
                table.add_row(
                    "⏭️ ", f"[cyan]{bench_name}[/cyan]", f"[yellow]v{orig_version}[/yellow] (already up to date)"
                )

        if benches_failed:
            for bench_name in benches_failed:
                table.add_row("❌", f"[cyan]{bench_name}[/cyan]", "[red]Migration failed[/red]")

        richprint.stdout.print(table)

        if benches_failed:
            richprint.error("Check logs for details", emoji_code=":warning:")
