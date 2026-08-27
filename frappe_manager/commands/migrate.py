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
    "Migrate FM itself after a CLI update",
    "",
    detail="Updates FM's own config and global services. No bench is touched.",
)
@example(
    "Migrate one bench",
    "{benchname}",
    benchname="mybench",
)
@example(
    "Migrate every bench",
    "--all-benches",
)
@example(
    "Migrate every bench unattended",
    "--all-benches --auto-proceed --on-failure=archive",
    detail="The combination for CI and large fleets: no prompts, and one bad bench does not undo the others.",
)
@example(
    "Leave some benches behind",
    "--all-benches --exclude-bench mybench1,mybench2",
)
def migrate(
    ctx: typer.Context,
    benchname: Annotated[
        str | None,
        typer.Argument(help="Bench name to migrate"),
    ] = None,
    all_benches: Annotated[
        bool,
        typer.Option("--all-benches", help="Migrate every bench FM manages."),
    ] = False,
    skip_backup: Annotated[
        bool,
        typer.Option(
            "--skip-all-backup",
            help="Migrate without taking a pre-migration backup (DANGEROUS; use only when the backups themselves fail).",
        ),
    ] = False,
    skip_backup_for: Annotated[
        str | None,
        typer.Option(
            "--skip-backup-for", help="Skip the pre-migration backup for these benches only (comma-separated)."
        ),
    ] = None,
    exclude_bench: Annotated[
        str | None,
        typer.Option("--exclude-bench", help="Benches to leave alone (comma-separated). Only with --all-benches."),
    ] = None,
    auto_proceed: Annotated[
        bool,
        typer.Option("--auto-proceed", help="Migrate without asking for confirmation."),
    ] = False,
    rerun: Annotated[
        bool,
        typer.Option(
            "--rerun",
            help="Re-run the migration steps on a bench that is already up to date.",
        ),
    ] = False,
    on_failure: Annotated[
        MigrationFailureAction | None,
        typer.Option(
            "--on-failure",
            help="What to do when a bench fails: prompt (ask, the default), archive (set failed benches aside and keep the rest migrated), rollback (revert every bench). A single-bench run always rolls back.",
        ),
    ] = None,
):
    """
    Bring Frappe Manager and its benches up to the current version.

    Benches are never migrated implicitly: a bare fm migrate updates only FM's own config and global services. Name a bench, or pass --all-benches, to migrate benches themselves.

    Every other bench command refuses to run against a bench that is behind, so migrate first.
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
            table.add_row(
                "⏭️ ", f"[fm.info]{bench_name}[/fm.info]", f"[fm.warn]v{orig_version}[/fm.warn] (already up to date)"
            )

    if benches_failed:
        for bench_name in benches_failed:
            table.add_row("❌", f"[fm.info]{bench_name}[/fm.info]", "[fm.error]Migration failed[/fm.error]")

    output.print_data(table)

    if benches_failed:
        output.display_error("Check logs for details", emoji_code=":warning:")
        raise typer.Exit(1)
