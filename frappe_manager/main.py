import atexit
from frappe_manager.display_manager.DisplayManager import richprint
from frappe_manager.logger import log
from frappe_manager.utils.helpers import check_update,  remove_zombie_subprocess_process
from frappe_manager.utils.docker import process_opened
from frappe_manager.commands import app


def cli_entrypoint():
    try:
        app()
    except Exception as e:
        logger = log.get_logger()
        logger.exception(f"Exception:  : {e}")
        raise e
    finally:
        atexit.register(exit_cleanup)

def exit_cleanup():
    """
    This function is used to perform cleanup at the exit.
    """
    remove_zombie_subprocess_process(process_opened)
    #check_update()
    richprint.stop()
