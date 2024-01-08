import importlib
import sys
import requests
import json
import subprocess
import platform

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

def is_port_in_use(port):
    """
    Check if port is in use or not.

    :param port: The port which will be checked if it's in use or not.
    :return: Bool In use then True and False when not in use.
    """
    import psutil

    for conn in psutil.net_connections():
        if conn.laddr.port == port and conn.status == "LISTEN":
            return True
    return False


def check_ports(ports):
    """
    This function checks if the ports is in use.
    :param ports: list of ports to be checked
    returns: list of binded port(can be empty)
    """

    # TODO handle if ports are open using docker

    current_system = platform.system()
    already_binded = []
    for port in ports:
        if current_system == "Darwin":
            # Mac Os
            # check port using lsof command
            cmd = f"lsof -iTCP:{port} -sTCP:LISTEN -P -n"
            try:
                output = subprocess.run(
                    cmd, check=True, shell=True, capture_output=True
                )
                if output.returncode == 0:
                    already_binded.append(port)
            except subprocess.CalledProcessError as e:
                pass
        else:
            # Linux or any other machines
            if is_port_in_use(port):
                already_binded.append(port)

    return already_binded


def check_ports_with_msg(ports_to_check: list, exclude=[]):
    """
    The `check_ports` function checks if certain ports are already bound by another process using the
    `lsof` command.
    """
    richprint.change_head("Checking Ports")
    if exclude:
        # Removing elements present in remove_array from original_array
        ports_to_check = [x for x in exclude if x not in ports_to_check]
    if ports_to_check:
        already_binded = check_ports(ports_to_check)
        if already_binded:
            richprint.exit(
                f"Whoa there! Looks like the {' '.join([ str(x) for x in already_binded ])} { 'ports are' if len(already_binded) > 1 else 'port is' } having a party already! Can you do us a solid and free up those ports?"
            )
    richprint.print("Ports Check : Passed")
