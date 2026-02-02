from pathlib import Path
from rich.panel import Panel
from frappe_manager.site_manager.exceptions import BenchNotRunning
from frappe_manager.utils.site import pull_docker_images, validate_sitename
from frappe_manager.site_manager.domain_conflict import validate_domains_unique, DomainConflictError
import typer
import os
import sys
import shutil
import secrets
from typing import Annotated, List, Optional, cast
from frappe_manager.site_manager.bench_config import BenchConfig, FMBenchEnvType, AppConfig, RestartPolicyEnum
from frappe_manager.docker import ComposeFile, DockerClient
from frappe_manager.ngrok import create_tunnel
from frappe_manager.services_manager.services_exceptions import ServicesNotCreated
from frappe_manager.site_manager.bench_service import BenchService
from frappe_manager.site_manager.modules.app_cloner import AppCloner
from frappe_manager.output_manager import spinner, temporary_stop
from frappe_manager import (
    CLI_BENCH_CONFIG_FILE_NAME,
    CLI_DIR,
    DEFAULT_EXTENSIONS,
    STABLE_APP_BRANCH_MAPPING_LIST,
    EnableDisableOptionsEnum,
    SiteServicesEnum,
    CLI_BENCHES_DIRECTORY,
    CLI_FM_CONFIG_PATH,
)
from frappe_manager.docker import DockerClient
from frappe_manager.logger import log
from frappe_manager.logger.context import LoggerContext
from frappe_manager.services_manager.services import ServicesManager
from frappe_manager.migration_manager.migration_executor import (
    MigrationExecutor,
    needs_migration,
    needs_fm_infrastructure_migration,
    get_benches_needing_migration,
)
from frappe_manager.site_manager.site import Bench
from frappe_manager.utils.callbacks import (
    apps_list_validation_callback,
    create_command_sitename_callback,
    sites_autocompletion_callback,
    version_callback,
    sitename_callback,
    code_command_extensions_callback,
    alias_domains_validation_callback,
)
from frappe_manager.utils.helpers import (
    is_cli_help_called,
    get_current_fm_version,
)
from frappe_manager.output_manager import OutputHandler, get_global_output_handler, spinner
from frappe_manager.output_manager.rich_output import RichOutputHandler
from frappe_manager.output_manager.logging_output import LoggingOutputHandler

from frappe_manager.commands import check_bench_migration_required


def start(
    ctx: typer.Context,
    benchname: Annotated[
        Optional[str],
        typer.Argument(
            help="Name of the bench.", autocompletion=sites_autocompletion_callback, callback=sitename_callback
        ),
    ] = None,
    force: Annotated[bool, typer.Option("--force", "-f", help="Recreate containers")] = False,
    sync_bench_config_changes: Annotated[bool, typer.Option("--sync-config", help="Sync config changes")] = False,
    reconfigure_supervisor: Annotated[
        bool, typer.Option("--reconfigure-supervisor", help="Reconfigure supervisor")
    ] = False,
    reconfigure_common_site_config: Annotated[
        bool, typer.Option("--reconfigure-common-site-config", help="Reconfigure site config")
    ] = False,
    reconfigure_workers: Annotated[bool, typer.Option("--reconfigure-workers", help="Reconfigure workers")] = False,
    include_default_workers: Annotated[bool, typer.Option(help="Include default workers")] = True,
    include_custom_workers: Annotated[bool, typer.Option(help="Include custom workers")] = True,
    sync_dev_packages: Annotated[bool, typer.Option("--sync-dev-packages", help="Sync dev packages")] = False,
):
    """
    Start a bench.

    Examples:

        fm start mybench
        fm start mybench --force
        fm start mybench --sync-config
    """

    check_bench_migration_required(benchname)

    services_manager = ctx.obj["services"]
    verbose = ctx.obj['verbose']

    output = get_global_output_handler()
    bench = Bench.get_object(benchname, services_manager, output_handler=output)

    with spinner(output, f"Starting {benchname}"):
        bench.start(
            force=force,
            sync_bench_config_changes=sync_bench_config_changes,
            reconfigure_workers=reconfigure_workers,
            include_default_workers=include_default_workers,
            include_custom_workers=include_custom_workers,
            reconfigure_common_site_config=reconfigure_common_site_config,
            reconfigure_supervisor=reconfigure_supervisor,
            sync_dev_packages=sync_dev_packages,
        )
