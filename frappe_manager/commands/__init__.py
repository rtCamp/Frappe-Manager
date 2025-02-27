import logging
import shutil
import sys
from typing import Annotated, Optional

import typer

from frappe_manager import CLI_BENCHES_DIRECTORY, CLI_DIR
from frappe_manager.display_manager.DisplayManager import richprint
from frappe_manager.docker_wrapper.DockerClient import DockerClient
from frappe_manager.logger import log
from frappe_manager.metadata_manager import FMConfigManager
from frappe_manager.migration_manager.migration_executor import MigrationExecutor
from frappe_manager.migration_manager.version import Version
from frappe_manager.services_manager.services import ServicesManager
from frappe_manager.services_manager.services_exceptions import ServicesNotCreated
from frappe_manager.utils.callbacks import version_callback
from frappe_manager.utils.helpers import get_current_fm_version, is_cli_help_called
from frappe_manager.utils.site import pull_docker_images

app = typer.Typer(no_args_is_help=True, rich_markup_mode="rich")

def _initialize_directories() -> bool:
    """Initialize FM directories if they don't exist"""
    if not CLI_DIR.exists():
        CLI_DIR.mkdir(parents=True, exist_ok=True)
        CLI_BENCHES_DIRECTORY.mkdir(parents=True, exist_ok=True)
        richprint.print(f"fm directory doesn't exists! Created at -> {str(CLI_DIR)}")
        return True

    if not CLI_DIR.is_dir():
        richprint.exit("Sites directory is not a directory! Aborting!")

    return False

def _setup_logging() -> logging.Logger:
    """Initialize and setup logging"""
    logger = log.get_logger()
    logger.info("")
    logger.info(f"{':' * 20}FM Invoked{':' * 20}")
    logger.info("")
    logger.info(f"RUNNING COMMAND: {' '.join(sys.argv[1:])}")
    logger.info("-" * 20)
    return logger

def _check_docker_daemon():
    """Verify Docker daemon is running"""
    if not DockerClient().server_running():
        richprint.exit("Docker daemon not running. Please start docker service.")

def _handle_first_install(is_first_install: bool, fm_config_manager: FMConfigManager):
    """Handle first time installation tasks"""
    if is_first_install:
        if not fm_config_manager.root_path.exists():
            richprint.print("It seems like the first installation. Pulling docker images...️", "🔍")

            if not pull_docker_images():
                shutil.rmtree(CLI_DIR)
                richprint.exit("Aborting. Not able to pull all required Docker images.")

            current_version = Version(get_current_fm_version())
            fm_config_manager.version = current_version
            fm_config_manager.export_to_toml()

def _initialize_services(verbose: bool, ctx: typer.Context) -> ServicesManager:
    """Initialize and setup services"""
    services_manager = ServicesManager(verbose=verbose)
    services_manager.set_typer_context(ctx)
    services_manager.init()

    try:
        services_manager.entrypoint_checks(start=True)
    except ServicesNotCreated as e:
        services_manager.remove_itself()
        richprint.exit(f"Not able to create services. {e}")

    return services_manager

@app.callback()
def app_callback(
    ctx: typer.Context,
    verbose: Annotated[bool, typer.Option("--verbose", "-v", help="Enable verbose output.")] = False,
    version: Annotated[
        Optional[bool], typer.Option("--version", "-V", help="Show Version.", callback=version_callback)
    ] = None,
):
    """
    Frappe-Manager for creating frappe development environments.
    """
    ctx.obj = {}
    help_called = is_cli_help_called(ctx)
    ctx.obj["is_help_called"] = help_called

    if not help_called:
        richprint.start("Working")

        # Initialize directories
        is_first_install = _initialize_directories()

        # Setup logging
        global logger
        logger = _setup_logging()

        # Check Docker daemon
        _check_docker_daemon()

        # Load config and handle first install
        fm_config_manager = FMConfigManager.import_from_toml()
        _handle_first_install(is_first_install, fm_config_manager)

        # Handle migrations
        migrations = MigrationExecutor(fm_config_manager)
        if not migrations.execute():
            richprint.exit(f"Rollbacked to previous version of fm {migrations.prev_version}.")

        # Initialize services
        services_manager = _initialize_services(verbose, ctx)

        # Set context objects
        ctx.obj["services"] = services_manager
        ctx.obj["verbose"] = verbose
        ctx.obj['fm_config_manager'] = fm_config_manager


def load_commands():
    """
    Dynamically load all command modules and register them with the app.
    This is done after app initialization to avoid circular imports.
    """
    command_modules = [
        'code', 'create', 'delete', 'info', 'list',
        'logs', 'ngrok', 'reset', 'restart', 'shell',
        'start', 'stop', 'update', 'self', 'services', 'ssl'
    ]
    
    for module_name in command_modules:
        __import__(f'frappe_manager.commands.{module_name}')

# Load all commands after app is initialized
load_commands()
