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
from frappe_manager.display_manager.DisplayManager import richprint
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
    needs_system_migration,
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
from frappe_manager.output_manager import OutputHandler
from frappe_manager.output_manager.rich_output import RichOutputHandler
from frappe_manager.output_manager.logging_output import LoggingOutputHandler

# Import helpers from parent __init__.py
from frappe_manager.commands import check_bench_migration_required, get_output_handler


def logs(
    ctx: typer.Context,
    benchname: Annotated[
        Optional[str],
        typer.Argument(
            help="Name of the bench.", autocompletion=sites_autocompletion_callback, callback=sitename_callback
        ),
    ] = None,
    service: Annotated[Optional[str], typer.Option(help="Service name (frappe, nginx, redis-cache, etc.)")] = None,
    follow: Annotated[bool, typer.Option("--follow", "-f", help="Follow logs in real-time")] = False,
):
    """Show bench logs (server or container)"""

    check_bench_migration_required(benchname)

    services_manager = ctx.obj["services"]
    verbose = ctx.obj['verbose']

    # Create output handler with context for logging
    context = LoggerContext(bench=benchname, operation="logs")
    output = get_output_handler(ctx, context=context)
    bench = Bench.get_object(benchname, services_manager, output_handler=output)

    if service:
        available_services = bench.get_available_services()
        if service not in available_services:
            output.display_error(f"Service '{service}' not found")
            output.print(f"Available services: {', '.join(sorted(available_services))}")
            raise typer.Exit(1)

    bench.logs(follow, service)
