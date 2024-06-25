import importlib
from cryptography.hazmat.backends import default_backend
from datetime import datetime
from cryptography import x509
from io import StringIO
import sys
from typing import Optional
from frappe_manager.utils.docker import run_command_with_exit_code
import requests
import subprocess
import platform
import time
import secrets
import grp
from pathlib import Path
import importlib.resources as pkg_resources
from rich.console import Console
from rich.traceback import Traceback
from frappe_manager.utils.site import is_fqdn
from frappe_manager.logger import log
from frappe_manager.display_manager.DisplayManager import richprint
from frappe_manager.site_manager import PREBAKED_SITE_APPS
from frappe_manager import CLI_BENCHES_DIRECTORY


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
    if exclude:
        # Removing elements present in remove_array from original_array
        ports_to_check = [x for x in exclude if x not in ports_to_check]

    if ports_to_check:
        already_binded = check_ports(ports_to_check)
        if already_binded:
            richprint.exit(
                f"Ports {', '.join(map(str, already_binded))} {'are' if len(already_binded) > 1 else 'is'} currently in use. Please free up these ports."
            )

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
    # --help check

    if '--help' in " ".join(sys.argv[1:]):
        # is called command is sub command group
        return True

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
    output = run_command_with_exit_code(
        [sys.executable, "-m", "pip", "install", f"{package_name}=={version}"], stream=True
    )
    richprint.live_lines(output)


def get_sitename_from_current_path() -> Optional[str]:
    current_path = Path().absolute()
    sites_path = CLI_BENCHES_DIRECTORY.absolute()

    if not current_path.is_relative_to(sites_path):
        return None

    sitename_list = list(current_path.relative_to(sites_path).parts)

    if not sitename_list:
        return None

    sitename = sitename_list[0]
    if is_fqdn(sitename):
        return sitename


def create_class_from_dict(class_name, attributes_dict):
    """
    Dynamically creates a class with properties based on the provided attributes dictionary.

    Parameters:
    class_name (str): The name of the class to be created.
    attributes_dict (dict): A dictionary where keys are the names of the properties and values are their default values.

    Returns:
    A new class with the specified properties and their default values.
    """
    return type(class_name, (object,), attributes_dict)


def create_symlink(source: Path, dest: Path):
    """
    Create a symbolic link pointing from dest to source.

    Parameters:
    - source (str): The source path that the symlink will point to.
    - dest (str): The destination path where the symlink will be created.

    Note: The function will overwrite the destination if a symlink already exists there.
    """

    # Convert the source and destination to Path objects

    # Remove the destination symlink/file/directory if it already exists
    if dest.exists() or dest.is_symlink():
        dest.unlink()

    # Create a symlink at the destination pointing to the source
    dest.symlink_to(source)


def get_template_path(file_name: str, template_dir: str = 'templates') -> Path:
    """
    Get the file path of a template.

    Args:
        file_name (str): The name of the template file.
        template_directory (str, optional): The directory where the templates are located. Defaults to "templates".

    Returns:
        Optional[str]: The file path of the template, or None if the template is not found.
    """
    template_path: str = f"{template_dir}/{file_name}"
    return get_frappe_manager_own_files(template_path)


def get_frappe_manager_own_files(file_path: str):
    return Path(str(pkg_resources.files("frappe_manager").joinpath(file_path)))


def rich_object_to_string(obj) -> str:
    """Convert a rich Traceback object to a string."""

    # Initialize a 'fake' console with StringIO to capture output
    capture_buffer = StringIO()

    fake_console = Console(force_terminal=False, file=capture_buffer)
    fake_console.print(obj, crop=False, overflow='ignore')

    captured_str = capture_buffer.getvalue()  # Retrieve the captured output as a string
    capture_buffer.close()
    return captured_str


def capture_and_format_exception(traceback_max_frames: int = 100) -> str:
    """Capture the current exception and return a formatted traceback string."""

    exc_type, exc_value, exc_traceback = sys.exc_info()  # Capture current exception info
    # Create a Traceback object with rich formatting
    #
    traceback = Traceback.from_exception(
        exc_type, exc_value, exc_traceback, show_locals=True, max_frames=traceback_max_frames
    )

    # Convert the Traceback object to a formatted string
    formatted_traceback = rich_object_to_string(traceback)

    return formatted_traceback


def pluralise(singular, count):
    return '{} {}{}'.format(count, singular, '' if count == 1 else 's')


def format_ssl_certificate_time_remaining(expiry_date: datetime):
    today_date = datetime.now(expiry_date.tzinfo)
    time_remaining = expiry_date - today_date
    day_count = time_remaining.days
    seconds_per_minute = 60
    seconds_per_hour = seconds_per_minute * 60
    seconds_unaccounted_for = time_remaining.seconds

    hours = int(seconds_unaccounted_for / seconds_per_hour)
    seconds_unaccounted_for -= hours * seconds_per_hour

    minutes = int(seconds_unaccounted_for / seconds_per_minute)

    return '{} {} {}'.format(pluralise('day', day_count), pluralise('hour', hours), pluralise('min', minutes))


def get_certificate_expiry_date(fullchain_path: Path) -> datetime:
    cert_content = fullchain_path.read_bytes()
    cert = x509.load_pem_x509_certificate(cert_content, default_backend())
    if hasattr(cert, 'not_valid_after_utc'):
        expiry_date: datetime = cert.not_valid_after_utc
    else:
        expiry_date: datetime = cert.not_valid_after
    return expiry_date
