import atexit

from frappe_manager.display_manager.DisplayManager import richprint
from frappe_manager.logger import log
from frappe_manager.utils.helpers import capture_and_format_exception, remove_zombie_subprocess_process
from frappe_manager import CLI_LOG_DIRECTORY
from frappe_manager.utils.docker import process_opened
from frappe_manager.commands import app


def cli_entrypoint():
    try:
        app()
    except Exception as e:
        logger = log.get_logger()

        richprint.error(f'[red]Error Occured[/red]  {str(e).strip()}')
        richprint.error(f"More info about error is logged in {CLI_LOG_DIRECTORY/'fm.log'}", emoji_code=':mag:')
        richprint.stop()

        exception_traceback: str = capture_and_format_exception()
        logger.error(f"Exception Occured:  : \n{exception_traceback}")

    finally:
        atexit.register(exit_cleanup)


def exit_cleanup():
    """
    This function is used to perform cleanup at the exit.
    """
    remove_zombie_subprocess_process(process_opened)
    richprint.stop()
