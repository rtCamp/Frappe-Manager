from frappe_manager.logger import log
from frappe_manager.docker_wrapper.utils import process_opened

def remove_zombie_subprocess_process():
    """
    Terminates any zombie process
    """
    if process_opened:
        logger = log.get_logger()
        logger.cleanup("-" * 20)
        logger.cleanup(f"PROCESS: USED PROCESS {process_opened}")

        # terminate zombie docker process
        import psutil
        for pid in process_opened:
            try:
                process = psutil.Process(pid)
                process.terminate()
                logger.cleanup(f"Terminated Process {process.cmdline}:{pid}")
            except psutil.NoSuchProcess:
                logger.cleanup(f"{pid} Process not found")
            except psutil.AccessDenied:
                logger.cleanup(f"{pid} Permission denied")
        logger.cleanup("-" * 20)
