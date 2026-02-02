from logging import Logger
import os
from pathlib import Path
from subprocess import run
from typing import Dict, Iterable, Tuple, Union, Optional
from frappe_manager.logger import log
from frappe_manager.docker.docker_exceptions import DockerException
from frappe_manager.output_manager import get_global_output_handler
from frappe_manager.docker.subprocess_output import SubprocessOutput
from frappe_manager.utils.subprocess import stream_command_output

process_opened = []


def stream_stdout_and_stderr(
    full_cmd: list,
    cwd: Optional[str] = None,
    logger: Optional[Logger] = None,
    env: Optional[Dict[str, str]] = None,
) -> Iterable[Tuple[str, bytes]]:
    """
    Executes a Docker command and streams the stdout and stderr outputs.

    This is a Docker-specific wrapper around the generic stream_command_output()
    that adds DockerException handling for failed commands.

    Args:
        full_cmd (list): The Docker command to be executed.
        cwd (Optional[str]): Working directory for command execution.
        logger (Optional[Logger]): Logger instance (unused, kept for compatibility).
        env (Dict[str, str], optional): Environment variables. Defaults to None.

    Yields:
        Tuple[str, bytes]: A tuple containing the source ("stdout" or "stderr") and the output line.

    Raises:
        DockerException: If the Docker command returns a non-zero exit code.

    Returns:
        Iterable[Tuple[str, bytes]]: An iterable of tuples containing the source and output line.
    """
    output = []

    # Use generic subprocess streaming
    for source, line in stream_command_output(full_cmd, env=env, cwd=cwd):
        output.append((source, line))

        # Check for exit code and raise DockerException if command failed
        if source == "exit_code":
            exit_code = int(line.decode())
            if exit_code != 0:
                raise DockerException(full_cmd, SubprocessOutput.from_output(output))

        yield source, line


def run_command_with_exit_code(
    full_cmd: list,
    stream: bool = True,
    capture_output: bool = True,
    env: Optional[Dict[str, str]] = None,
    cwd: Optional[str] = None,
) -> Union[Iterable[Tuple[str, bytes]], SubprocessOutput]:
    """
    Run a command and return the exit code.

    Args:
        full_cmd (list): The command to be executed as a list of strings.
        env (Dict[str, str], optional): Environment variables to be set for the command. Defaults to None.
        stream (bool, optional): Flag indicating whether to stream the command output. Defaults to True.
    """
    if not stream:
        if not capture_output:
            logger = log.get_logger()
            logger.debug('- -' * 10)
            logger.debug(f"COMMAND: {' '.join(full_cmd)}")

            run_output = run(full_cmd, cwd=cwd, env=env)
            exit_code = run_output.returncode

            logger.debug(f"RETURN CODE: {exit_code}")
            logger.debug('- -' * 10)

            if exit_code != 0:
                raise DockerException(full_cmd, SubprocessOutput([], [], [], exit_code))
            return

        stream_output: SubprocessOutput = SubprocessOutput.from_output(
            stream_stdout_and_stderr(full_cmd, cwd=cwd, env=env)
        )
        return stream_output

    output: Iterable[Tuple[str, bytes]] = stream_stdout_and_stderr(full_cmd, cwd=cwd, env=env)
    return output


def parameter_to_option(param: str) -> str:
    """Converts a parameter to an option.

    Args:
        param (str): The parameter to be converted.

    Returns:
        str: The converted option.
    """
    option = "--" + param.replace("_", "-")
    return option


def parameters_to_options(param: dict, exclude: list = []) -> list:
    """
    Convert a dictionary of parameters to a list of options for a command.

    Args:
        param (dict): The dictionary of parameters.
        exclude (list, optional): A list of keys to exclude from the options. Defaults to [].

    Returns:
        list: The list of options for the command.
    """
    # remove the self parameter
    temp_param: dict = dict(param)

    if "self" in temp_param:
        del temp_param["self"]

    for key in exclude:
        if key in temp_param:
            del temp_param[key]

    params: list = []

    for key in temp_param.keys():
        value = temp_param[key]
        key = "--" + key.replace("_", "-")

        if type(value) == bool:
            if value:
                params.append(key)

        elif type(value) == int:
            params.append(key)
            params.append(value)

        elif type(value) == str:
            if value:
                params.append(key)
                params.append(value)

        elif type(value) == list:
            if value:
                # For each item in the list, add the key and value separately
                for item in value:
                    params.append(key)
                    params.append(item)

    return params


def is_current_user_in_group(group_name) -> bool:
    """Check if the current user is in the given group.

    Args:
        group_name (str): The name of the group to check.

    Returns:
        bool: True if the current user is in the group, False otherwise.
    """

    import platform

    if platform.system() == 'Linux':
        import grp
        import pwd
        import os

        current_user = pwd.getpwuid(os.getuid()).pw_name
        try:
            docker_gid = grp.getgrnam(group_name).gr_gid
            docker_group_members = grp.getgrgid(docker_gid).gr_mem
            if current_user in docker_group_members:
                return True
            else:
                output = get_global_output_handler()
                output.error(
                    f"Your current user [blue][b] {current_user} [/b][/blue] is not in the 'docker' group. Please add it and restart your terminal."
                )
                return False
        except KeyError:
            output = get_global_output_handler()
            output.error(
                f"The group '{group_name}' does not exist. Please create it and add your current user [blue][b] {current_user} [/b][/blue] to it."
            )
            return False
    else:
        return True


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


def host_run_cp(image: str, source: str, destination: str, docker):
    """Copy files from source to destination using Docker.

    Args:
        image (str): The Docker image to run.
        source (str): The source file or directory path.
        destination (str): The destination file or directory path.
        docker: The Docker client object.
        verbose (bool, optional): Whether to display verbose output. Defaults to False.
    """

    dest_path = Path(destination)
    output = get_global_output_handler()
    output.change_head(f"Populating {dest_path.name} directory")

    try:
        # Use context manager for automatic cleanup
        with docker.create_temp_container(image) as container:
            # Copy from the container
            docker.cp(
                source=source,
                destination=destination,
                source_container=container.name,
                stream=False,
            )

        # Check if the destination file exists
        if not Path(destination).exists():
            raise Exception(f"{destination} not found.")

        output.change_head(f"Populated {dest_path.name} directory")

    except DockerException as e:
        # Clean up destination if copy failed
        if dest_path.exists():
            import shutil

            shutil.rmtree(dest_path)
        raise Exception(f"Failed to copy files from {source} to {destination}.") from e
