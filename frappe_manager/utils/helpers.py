import importlib
import sys
from typing import Optional
import requests
import json
import subprocess
import platform
import time
import secrets
import grp

from pathlib import Path
from frappe_manager.utils.site import is_fqdn
from frappe_manager.logger import log
from frappe_manager.display_manager.DisplayManager import richprint
from frappe_manager.site_manager import PREBAKED_SITE_APPS
from frappe_manager import CLI_SITES_DIRECTORY


def remove_zombie_subprocess_process(process):
    """
    This function iterates over a list of process IDs and terminates each process.

    Args:
        process (list): A list of process IDs to be terminated.

    Returns:
        None
    """
    if process:
        logger = log.get_logger()
        logger.cleanup("-" * 20)
        logger.cleanup(f"PROCESS: USED PROCESS {process}")

        import psutil

        for pid in process:
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
    """
    Retrieves the latest version of the frappe-manager package from PyPI and compares it with the currently installed version.
    If a newer version is available, it displays a warning message suggesting to update the package.
    """
    url = "https://pypi.org/pypi/frappe-manager/json"
    try:
        update_info = requests.get(url, timeout=0.1)
        update_info = json.loads(update_info.text)
        fm_version = importlib.metadata.version("frappe-manager")
        latest_version = update_info["info"]["version"]
        if not fm_version == latest_version:
            richprint.warning(
                f"[dim]Update available v{latest_version}.[/dim]",
                emoji_code=":arrows_counterclockwise:️",
            )
    except Exception as e:
        pass


def is_port_in_use(port):
    """
    Check if a port is in use or not.

    Args:
        port (int): The port number to check.

    Returns:
        bool: True if the port is in use, False otherwise.
    """
    import psutil

    for conn in psutil.net_connections():
        if conn.laddr.port == port and conn.status == "LISTEN":
            return True
    return False


def check_ports(ports):
    """
    Checks if the ports are in use.

    Args:
        ports (list): List of ports to be checked.

    Returns:
        list: List of binded ports (can be empty).
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
                output = subprocess.run(cmd, check=True, shell=True, capture_output=True)
                if output.returncode == 0:
                    already_binded.append(port)
            except subprocess.CalledProcessError as e:
                pass
        else:
            # Linux or any other machines
            if is_port_in_use(port):
                already_binded.append(port)

    return already_binded


def check_and_display_port_status(ports_to_check: list, exclude=[]):
    """
    Check if the specified ports are already binded and display a message if they are.

    Args:
        ports_to_check (list): List of ports to check.
        exclude (list, optional): List of ports to exclude from checking. Defaults to [].
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


def generate_random_text(length=50):
    """
    Generate a random text of specified length.

    Parameters:
    length (int): The length of the random text to be generated. Default is 50.

    Returns:
    str: The randomly generated text.
    """
    import random, string

    alphanumeric_chars = string.ascii_letters + string.digits
    return "".join(random.choice(alphanumeric_chars) for _ in range(length))


def is_cli_help_called(ctx):
    """
    Checks if the help is called for the CLI command.

    Args:
        ctx (object): The context object representing the CLI command.

    Returns:
        bool: True if the help command is called, False otherwise.
    """
    help_called = False
    # is called command is sub command group
    try:
        for subtyper_command in ctx.command.commands[ctx.invoked_subcommand].commands.keys():
            check_command = " ".join(sys.argv[2:])
            if check_command == subtyper_command:
                if ctx.command.commands[ctx.invoked_subcommand].commands[subtyper_command].params:
                    help_called = True

    except AttributeError:
        help_called = False

    if not help_called:
        # is called command is sub command
        check_command = " ".join(sys.argv[1:])

        if check_command == ctx.invoked_subcommand:
            # is called command supports arguments then help called
            if ctx.command.commands[ctx.invoked_subcommand].params:
                help_called = True

            if not ctx.command.commands[ctx.invoked_subcommand].no_args_is_help:
                help_called = False

    return help_called


def get_current_fm_version():
    """
    Get the current version of the frappe-manager package.

    Returns:
        str: The current version of the frappe-manager package.
    """
    return importlib.metadata.version("frappe-manager")


def check_repo_exists(app_url: str, branch_name: str | None = None, exclude_dict: dict[str, str] = PREBAKED_SITE_APPS):
    """
    Check if a Frappe app exists on GitHub.

    Args:
        appname (str): The name of the Frappe app.
        branchname (str | None, optional): The name of the branch to check. Defaults to None.

    Returns:
        dict: A dictionary containing the existence status of the app and branch (if provided).
    """
    try:
        if app_url in exclude_dict:
            app = 200
        else:
            app = requests.get(app_url).status_code

        if branch_name:
            if branch_name in exclude_dict.values():
                branch = 200
            else:
                branch_url = f"{app_url}/tree/{branch_name}"
                branch = requests.get(branch_url).status_code

            return {
                "app": True if app == 200 else False,
                "branch": True if branch == 200 else False,
            }
        return {"app": True if app == 200 else False}

    except Exception as e:
        richprint.error(f"Not able to validate app {app_url} for branch [blue]{branch_name}[/blue]", e)


def check_frappe_app_exists(app: str, branch_name: Optional[str] = None):
    if "github.com" not in app:
        app = f"https://github.com/frappe/{app}"

    return check_repo_exists(app_url=app, branch_name=branch_name)


def represent_null_empty(string_null):
    """
    Replaces the string "null" with an empty string.

    Args:
        string_null (str): The input string.

    Returns:
        str: The modified string with "null" replaced by an empty string.
    """
    return string_null.replace("null", "")


def log_file(file, refresh_time: float = 0.1, follow: bool = False):
    """
    Generator function that yields new lines in a file

    Parameters:
    - file: The file object to read from
    - refresh_time: The time interval (in seconds) to wait before checking for new lines in the file (default: 0.1)
    - follow: If True, the function will continue to yield new lines as they are added to the file (default: False)

    Returns:
    - A generator that yields each new line in the file
    """
    file.seek(0)

    # start infinite loop
    while True:
        # read last line of file
        line = file.readline()
        if not line:
            if not follow:
                break
            # sleep if file hasn't been updated
            time.sleep(refresh_time)
            continue
        line = line.strip("\n")
        yield line


def get_container_name_prefix(site_name):
    """
    Returns the container name prefix by removing dots from the site name.

    Args:
        site_name (str): The name of the site.

    Returns:
        str: The container name prefix.
    """
    return site_name.replace(".", "")


def random_password_generate(password_length=13, symbols=False):
    # Define the character set to include symbols
    # symbols = "!@#$%^&*()_-+=[]{}|;:,.<>?`~"
    symbols = "!@%_-+?"

    # Generate a password without symbols using token_urlsafe

    generated_password = secrets.token_urlsafe(password_length)

    # Replace some characters with symbols in the generated password
    if symbols:
        password = "".join(c if secrets.choice([True, False]) else secrets.choice(symbols) for c in generated_password)
        return password

    return generated_password


# Retrieve Unix groups and their corresponding integer mappings
def get_unix_groups():
    groups = {}
    for group_entry in grp.getgrall():
        group_name = group_entry.gr_name
        groups[group_name] = group_entry.gr_gid
    return groups


def install_package(package_name, version):
    subprocess.check_call([sys.executable, "-m", "pip", "install", f"{package_name}=={version}"])


def get_sitename_from_current_path() -> Optional[str]:
    current_path = Path().absolute()
    sites_path = CLI_SITES_DIRECTORY.absolute()

    if not current_path.is_relative_to(sites_path):
        return None

    sitename_list = list(current_path.relative_to(sites_path).parts)

    if not sitename_list:
        return None

    sitename = sitename_list[0]
    if is_fqdn(sitename):
        return sitename
