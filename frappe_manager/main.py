import atexit
import signal

from frappe_manager import CLI_LOG_DIRECTORY
from frappe_manager.commands import app
from frappe_manager.exceptions import FrappeManagerException
from frappe_manager.logger import get_logger, log
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
    if hasattr(signal, "SIGPIPE"):
        signal.signal(signal.SIGPIPE, signal.SIG_DFL)

    # Initialize basic output handler early (before app() runs)
    # This will be upgraded to LoggingOutputHandler in app_callback after CLI args are parsed
    basic_handler = RichOutputHandler()
    set_global_output_handler(basic_handler)

    # Apply output theme/style early (env/default) so even pre-config output is
    # themed; re-applied with fm_config values in app_callback.
    from frappe_manager.output_manager.style import set_output_style
    from frappe_manager.output_manager.theme import apply_output_theme

    try:
        apply_output_theme()
        set_output_style()
    except Exception as e:  # cosmetic subsystem: NEVER brick the CLI
        basic_handler.warning(f"Output theme/style: {e} -- using defaults.")
        import os

        os.environ.pop("FM_THEME", None)
        os.environ.pop("FM_STYLE", None)
        apply_output_theme()
        set_output_style()

    try:
        app()
    except FrappeManagerException as e:
        try:
            from frappe_manager.metadata_manager import FMConfigManager

            fm_config = FMConfigManager.import_from_toml()
            file_level = fm_config.logs.file_level
        except Exception:
            file_level = "DEBUG"

        log.get_logger(file_level=file_level)  # apply configured file log level
        logger = get_logger(component="main")
        output = get_global_output_handler()

        output.display_error(f"[fm.error]Error Occurred[/fm.error] {str(e).strip()}")

        # Show details if available
        if e.details:
            output.display_error(f"Details: {e.details}")

        output.print(f"More info about error is logged in {CLI_LOG_DIRECTORY / 'fm.log'}", emoji_code=":mag:")
        output.stop()

        exception_traceback: str = capture_and_format_exception()
        logger.error(f"FM Exception: {e.__class__.__name__}: {e!s}\n{exception_traceback}")
        exit(1)

    except Exception as e:
        try:
            from frappe_manager.metadata_manager import FMConfigManager

            fm_config = FMConfigManager.import_from_toml()
            file_level = fm_config.logs.file_level
        except Exception:
            file_level = "DEBUG"

        log.get_logger(file_level=file_level)  # apply configured file log level
        logger = get_logger(component="main")
        output = get_global_output_handler()

        output.display_error(f"[fm.error]Unexpected Error[/fm.error] {str(e).strip()}")
        output.print(f"More info about error is logged in {CLI_LOG_DIRECTORY / 'fm.log'}", emoji_code=":mag:")
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
