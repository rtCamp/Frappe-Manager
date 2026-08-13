import grp
import importlib
import importlib.resources as pkg_resources
import json
import secrets
import sys
import time
from datetime import datetime
from io import StringIO
from pathlib import Path

import requests
from cryptography import x509
from cryptography.hazmat.backends import default_backend
from rich.console import Console
from rich.traceback import Traceback

from frappe_manager import CLI_DEFAULT_DELIMETER, CLI_SITE_NAME_DELIMETER
from frappe_manager.docker import DOCKER_LINE_NOISE
from frappe_manager.logger import get_logger
from frappe_manager.output_manager import get_global_output_handler
from frappe_manager.site_manager import PREBAKED_SITE_APPS
from frappe_manager.utils.docker import run_command_with_exit_code

logger = get_logger(component="helpers")


def remove_zombie_subprocess_process(process):
    """
    This function iterates over a list of process IDs and terminates each process.

    Args:
        process (list): A list of process IDs to be terminated.

    Returns:
        None
    """
    if process:
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


def generate_random_text(length=50):
    """
    Generate a random text of specified length.

    Parameters:
    length (int): The length of the random text to be generated. Default is 50.

    Returns:
    str: The randomly generated text.
    """
    import random
    import string

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

    if "--help" in " ".join(sys.argv[1:]):
        return True

    try:
        subcommand = ctx.command.commands.get(ctx.invoked_subcommand)
        if not subcommand:
            return False

        if hasattr(subcommand, "commands"):
            check_command = " ".join(sys.argv[2:])
            if check_command in subcommand.commands:
                sub_sub_command = subcommand.commands[check_command]
                if sub_sub_command.params and sub_sub_command.no_args_is_help:
                    help_called = True
        elif subcommand.params and ctx.invoked_subcommand == " ".join(sys.argv[1:]):
            if subcommand.no_args_is_help:
                help_called = True

    except (AttributeError, KeyError):
        help_called = False

    return help_called


def get_current_fm_version():
    """
    Get the current version of the frappe-manager package.

    Returns:
        str: The current version of the frappe-manager package.
    """
    from frappe_manager.__about__ import __version__

    return __version__


def get_docker_image_tag():
    """
    Get the Docker image tag to use based on FM version.

    Returns version with 'v' prefix for Docker image tags.
    Examples:
        - '0.19.0' -> 'v0.19.0'
        - '0.19.1.dev0' -> 'v0.19.1.dev0'
        - '0.20.0.dev1' -> 'v0.20.0.dev1'

    Environment variable FM_DOCKER_IMAGE_TAG can override for testing.

    Returns:
        str: The Docker image tag (e.g., 'v0.19.0' or 'v0.19.1.dev0').
    """
    import os

    # Allow environment variable override for testing
    if override_tag := os.getenv('FM_DOCKER_IMAGE_TAG'):
        return override_tag

    version = get_current_fm_version()

    # Always prepend 'v' if not already present
    if not version.startswith('v'):
        return f'v{version}'

    return version


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
        output = get_global_output_handler()
        output.error(f"Not able to validate app {app_url} for branch [blue]{branch_name}[/blue]", e)


def check_frappe_app_exists(app: str, branch_name: str | None = None):
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
    return "fm" + CLI_DEFAULT_DELIMETER + site_name.replace(".", CLI_SITE_NAME_DELIMETER)


def get_redis_cache_addr(container_prefix: str) -> tuple[str, int]:
    return f"{container_prefix}{CLI_DEFAULT_DELIMETER}redis-cache", 6379


def get_redis_queue_addr(container_prefix: str) -> tuple[str, int]:
    return f"{container_prefix}{CLI_DEFAULT_DELIMETER}redis-queue", 6379


def get_bench_connection_config(
    container_prefix: str, redis_cache: str | None = None, redis_queue: str | None = None
) -> dict:
    """
    Mint the bench-wide keys of `common_site_config.json`.

    The database is per site and redis is per bench, which is why no `db_host`/`db_port` is minted
    here. `bench worker` and `bench schedule` run with no `--site`, so `common_site_config.json` is
    the only config those processes ever see and redis has to live in it. Nothing connects to the
    database without a site in hand, so the endpoint belongs in `sites/<site>/site_config.json`.

    Args:
        container_prefix: Container name prefix, used to derive the fm-managed redis addresses.
        redis_cache: External cache URL; when absent the per-bench redis-cache container is used.
        redis_queue: External queue URL; when absent the per-bench redis-queue container is used.
    """
    if redis_cache is None:
        cache_host, cache_port = get_redis_cache_addr(container_prefix)
        redis_cache = f"redis://{cache_host}:{cache_port}"

    if redis_queue is None:
        queue_host, queue_port = get_redis_queue_addr(container_prefix)
        redis_queue = f"redis://{queue_host}:{queue_port}"

    return {
        "bench_id": "workspace-frappe-bench",
        "redis_cache": redis_cache,
        "redis_queue": redis_queue,
        # The framework ignores this on v16, but bench tooling still expects the key.
        "redis_socketio": redis_cache,
    }


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
    output_lines = run_command_with_exit_code(
        [sys.executable, "-m", "pip", "install", f"{package_name}=={version}"],
        stream=True,
    )
    output = get_global_output_handler()
    output.live_lines(output_lines, line_filters=DOCKER_LINE_NOISE)


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

    if dest.exists() or dest.is_symlink():
        dest.unlink()

    dest.symlink_to(source)


def get_template_path(file_name: str, template_dir: str = "templates") -> Path:
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

    capture_buffer = StringIO()

    fake_console = Console(force_terminal=False, file=capture_buffer)
    fake_console.print(obj, crop=False, overflow="ignore")

    captured_str = capture_buffer.getvalue()  # Retrieve the captured output as a string
    capture_buffer.close()
    return captured_str


def capture_and_format_exception(traceback_max_frames: int = 100) -> str:
    """Capture the current exception and return a formatted traceback string."""

    exc_type, exc_value, exc_traceback = sys.exc_info()

    traceback = Traceback.from_exception(
        exc_type,
        exc_value,
        exc_traceback,
        show_locals=True,
        max_frames=traceback_max_frames,
    )

    # Convert the Traceback object to a formatted string
    formatted_traceback = rich_object_to_string(traceback)

    return formatted_traceback


def pluralise(singular, count):
    return "{} {}{}".format(count, singular, "" if count == 1 else "s")


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

    return "{} {} {}".format(pluralise("day", day_count), pluralise("hour", hours), pluralise("min", minutes))


def get_certificate_expiry_date(fullchain_path: Path) -> datetime:
    cert_content = fullchain_path.read_bytes()
    cert = x509.load_pem_x509_certificate(cert_content, default_backend())
    if hasattr(cert, "not_valid_after_utc"):
        expiry_date: datetime = cert.not_valid_after_utc
    else:
        expiry_date: datetime = cert.not_valid_after
    return expiry_date


def save_dict_to_file(config: dict, json_file_path: Path):
    """
    Sets the config value in the json_file_path file.

    Args:
        config (dict): A dictionary containing the key-value pairs.
    """

    final_config = {}
    with open(json_file_path) as f:
        final_config = json.load(f)
    for key, value in config.items():
        final_config[key] = value
    with open(json_file_path, "w") as f:
        json.dump(final_config, f)
