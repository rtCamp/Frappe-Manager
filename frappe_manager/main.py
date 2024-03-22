import atexit
from frappe_manager.display_manager.DisplayManager import richprint
from frappe_manager.logger import log
from frappe_manager.utils.helpers import check_update, remove_zombie_subprocess_process
from frappe_manager import CLI_LOG_DIRECTORY
from frappe_manager.utils.docker import process_opened
from frappe_manager.commands import app

def cli_entrypoint():
    try:
        app()
    except Exception as e:
        logger = log.get_logger()
        richprint.error(f'[red]Exception :[/red] {str(e).strip()}')
        richprint.error(f"More info about error is logged in {CLI_LOG_DIRECTORY/'fm.log'}")
        richprint.stop()
        with richprint.stdout.capture() as capture:
            richprint.stdout.print_exception(show_locals=True)
        excep = capture.get()
        logger.error(f"Exception Occured:  : \n{excep}")

    finally:
        atexit.register(exit_cleanup)

def exit_cleanup():
    """
    This function is used to perform cleanup at the exit.
    """
    remove_zombie_subprocess_process(process_opened)
    #check_update()
    richprint.stop()
