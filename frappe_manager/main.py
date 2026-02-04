import atexit

from frappe_manager import CLI_LOG_DIRECTORY
from frappe_manager.commands import app
from frappe_manager.exceptions import FrappeManagerException
from frappe_manager.logger import log
from frappe_manager.output_manager.globals import get_global_output_handler, set_global_output_handler
from frappe_manager.output_manager.rich_output import RichOutputHandler
from frappe_manager.utils.docker import process_opened
from frappe_manager.utils.helpers import capture_and_format_exception, remove_zombie_subprocess_process


def cli_entrypoint():
    """
    Main CLI entry point.

    Initializes a basic RichOutputHandler early, which will be upgraded
    to LoggingOutputHandler in app_callback (commands/__init__.py) after
    CLI arguments are parsed. Exception handling uses bare richprint for
    backward compatibility.
    """
    # Initialize basic output handler early (before app() runs)
    # This will be upgraded to LoggingOutputHandler in app_callback after CLI args are parsed
    basic_handler = RichOutputHandler()
    set_global_output_handler(basic_handler)

    try:
        app()
    except FrappeManagerException as e:
        try:
            from frappe_manager.metadata_manager import FMConfigManager

            fm_config = FMConfigManager.import_from_toml()
            file_level = fm_config.logs.file_level
        except:
            file_level = "DEBUG"

        logger = log.get_logger(file_level=file_level)
        output = get_global_output_handler()

        output.display_error(f'[red]Error Occurred[/red] {str(e).strip()}')

        # Show details if available
        if e.details:
            output.display_error(f"Details: {e.details}")

        output.print(f"More info about error is logged in {CLI_LOG_DIRECTORY / 'fm.log'}", emoji_code=':mag:')
        output.stop()

        exception_traceback: str = capture_and_format_exception()
        logger.error(f"FM Exception: {e.__class__.__name__}: {str(e)}\n{exception_traceback}")
        exit(1)

    except Exception as e:
        try:
            from frappe_manager.metadata_manager import FMConfigManager

            fm_config = FMConfigManager.import_from_toml()
            file_level = fm_config.logs.file_level
        except:
            file_level = "DEBUG"

        logger = log.get_logger(file_level=file_level)
        output = get_global_output_handler()

        output.display_error(f'[red]Unexpected Error[/red] {str(e).strip()}')
        output.print(f"More info about error is logged in {CLI_LOG_DIRECTORY / 'fm.log'}", emoji_code=':mag:')
        output.stop()

        exception_traceback: str = capture_and_format_exception()
        logger.error(f"Unexpected Exception:\n{exception_traceback}")
        exit(1)

    finally:
        atexit.register(exit_cleanup)


def exit_cleanup():
    """
    This function is used to perform cleanup at the exit.
    """
    remove_zombie_subprocess_process(process_opened)
    output = get_global_output_handler()
    output.stop()
