import importlib
import requests
import json
from frappe_manager.logger import log
from frappe_manager.docker_wrapper.utils import process_opened
from frappe_manager.site_manager.Richprint import richprint

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

def check_update():
    url = "https://pypi.org/pypi/frappe-manager/json"
    try:
        update_info = requests.get(url, timeout=0.1)
        update_info = json.loads(update_info.text)
        fm_version = importlib.metadata.version("frappe-manager")
        latest_version = update_info["info"]["version"]
        if not fm_version == latest_version:
            richprint.warning(
                f'Ready for an update? Run "pip install --upgrade frappe-manager" to update to the latest version {latest_version}.',
                emoji_code=":arrows_counterclockwise:️",
            )
    except Exception as e:
        pass
