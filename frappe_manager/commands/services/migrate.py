from typing import Annotated

import typer
from typer_examples import example

from frappe_manager.metadata_manager import FMConfigManager
from frappe_manager.migration_manager.migration_executor import MigrationExecutor
from frappe_manager.migration_manager.version import Version
from frappe_manager.output_manager import get_global_output_handler, spinner
from frappe_manager.utils.helpers import get_current_fm_version


@example(
    "Migrate after a CLI update",
    "",
    detail="Updates the shared services (mariadb, nginx-proxy) and fm's own config. No bench version is touched; run fm migrate BENCH or fm migrate all afterwards.",
)
@example(
    "Migrate unattended",
    "--auto-proceed",
)
def migrate_services(
    ctx: typer.Context,
    auto_proceed: Annotated[
        bool,
        typer.Option("--auto-proceed", help="Migrate without asking for confirmation."),
    ] = False,
    rerun: Annotated[
        bool,
        typer.Option("--rerun", help="Re-run the migration steps even when already up to date."),
    ] = False,
):
    """
    Bring fm's global services & configuration up to the current version.

    This is the host-wide half of a migration: the shared services every bench depends on (mariadb, nginx-proxy) and fm's own configuration. Benches are never migrated here; fm migrate refuses to run while this half is behind, so after a CLI update this command comes first.

    A migration here can briefly take every bench on the host down, because the shared services are every bench's database and only route in.
    """
    fm_config_manager: FMConfigManager = ctx.obj["fm_config_manager"]
    output = get_global_output_handler()

    current_version = Version(get_current_fm_version())
    global_services_version = fm_config_manager.get_system_migration_version()

    if not rerun and not global_services_version < current_version:
        output.print(f"✓ Global services & configuration already at v{global_services_version}")
        raise typer.Exit(0)

    migrations = MigrationExecutor(
        fm_config_manager,
        auto_proceed=auto_proceed,
        rerun=rerun,
        # Benches deliberately untargeted: this command is the services tier. A migration
        # may still rewrite bench FILES where the cutover is atomic (v0.21.0 renames the
        # addresses benches dial), but bench versions are stamped only by fm migrate.
        target_benches=None,
        migrate_global_services=True,
        output_handler=output,
    )

    with spinner(output, "Starting migration..."):
        migration_status = migrations.execute()

    if not migration_status:
        raise typer.Exit(1)

    fm_config_manager.set_system_migration_version(current_version)
    fm_config_manager.export_to_toml()

    output.print(
        f"Global services & configuration: [fm.warn]v{global_services_version}[/fm.warn] → [fm.ok]v{current_version}[/fm.ok]"
    )
