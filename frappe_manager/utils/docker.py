import os
from pathlib import Path
from queue import Queue
from subprocess import PIPE, Popen, run
from threading import Thread
from typing import Dict, Iterable, Tuple, Union, Optional
from frappe_manager.logger import log
from frappe_manager.docker_wrapper.DockerException import DockerException
from frappe_manager.display_manager.DisplayManager import richprint
from frappe_manager.docker_wrapper.subprocess_output import SubprocessOutput

process_opened = []


def reader(pipe, pipe_name, queue):
    """
    Reads lines from a pipe and puts them into a queue.

    Args:
        pipe (file-like object): The pipe to read from.
        pipe_name (str): The name of the pipe.
        queue (Queue): The queue to put the lines into.
    """
    logger = log.get_logger()
    try:
        with pipe:
            for line in iter(pipe.readline, b""):
                queue_line = line.decode().strip('\n')
                logger.debug(queue_line)
                queue.put((pipe_name, str(queue_line).encode()))
    finally:
        queue.put(None)


def stream_stdout_and_stderr(
    full_cmd: list,
    env: Optional[Dict[str, str]] = None,
) -> Iterable[Tuple[str, bytes]]:
    """
    Executes a command in Docker and streams the stdout and stderr outputs.

    Args:
        full_cmd (list): The command to be executed in Docker.
        env (Dict[str, str], optional): Environment variables to be passed to the Docker container. Defaults to None.

    Yields:
        Tuple[str, bytes]: A tuple containing the source ("stdout" or "stderr") and the output line.

    Raises:
        DockerException: If the Docker command returns a non-zero exit code.

    Returns:
        Iterable[Tuple[str, bytes]]: An iterable of tuples containing the source and output line.
    """
    logger = log.get_logger()
    logger.debug('- -' * 10)
    logger.debug(f"DOCKER COMMAND: {' '.join(full_cmd)}")
    if env is None:
        subprocess_env = None
    else:
        subprocess_env = dict(os.environ)
        subprocess_env.update(env)

    full_cmd = list(map(str, full_cmd))
    process = Popen(full_cmd, stdout=PIPE, stderr=PIPE, env=subprocess_env)

    process_opened.append(process.pid)

    q = Queue()

    # we use deamon threads to avoid hanging if the user uses ctrl+c
    th = Thread(target=reader, args=[process.stdout, "stdout", q])
    th.daemon = True
    th.start()
    th = Thread(target=reader, args=[process.stderr, "stderr", q])
    th.daemon = True
    th.start()

    output = []
    for _ in range(2):
        for source, line in iter(q.get, None):
            output.append((source, line))
            yield source, line

    exit_code = process.wait()

    logger.debug(f"RETURN CODE: {exit_code}")
    logger.debug('- -' * 10)

    output.append(('exit_code', str(exit_code).encode()))
    yield ("exit_code", str(exit_code).encode())

    if exit_code != 0:
        raise DockerException(full_cmd, SubprocessOutput.from_output(output))


def run_command_with_exit_code(
    full_cmd: list,
    stream: bool = True,
    capture_output: bool = True,
    env: Optional[Dict[str, str]] = None,
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
            run_output = run(full_cmd)
            exit_code = run_output.returncode
            if exit_code != 0:
                raise DockerException(full_cmd, SubprocessOutput([], [], [], exit_code))
            return

        stream_output: SubprocessOutput = SubprocessOutput.from_output(stream_stdout_and_stderr(full_cmd))
        return stream_output

    output: Iterable[Tuple[str, bytes]] = stream_stdout_and_stderr(full_cmd)
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

    del temp_param["self"]

    for key in exclude:
        del temp_param[key]

    # remove all parameters which are not booleans
    params: list = []

    for key in temp_param.keys():
        value = temp_param[key]
        key = "--" + key.replace("_", "-")
        if type(value) == bool:
            if value:
                params.append(key)
        if type(value) == int:
            params.append(key)
            params.append(value)
        if type(value) == str:
            if value:
                params.append(key)
                params.append(value)
        if type(value) == list:
            if value:
                params.append(key)
                params += value

    return params


def is_current_user_in_group(group_name) -> bool:
    """Check if the current user is in the given group.

    Args:
        group_name (str): The name of the group to check.

    Returns:
        bool: True if the current user is in the group, False otherwise.
    """

    from frappe_manager.display_manager.DisplayManager import richprint

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
                richprint.error(
                    f"Your current user [blue][b] {current_user} [/b][/blue] is not in the 'docker' group. Please add it and restart your terminal."
                )
                return False
        except KeyError:
            richprint.error(
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

    source_container_name = generate_random_text(10)
    dest_path = Path(destination)
    richprint.change_head(f"Populating {dest_path.name} directory.")

    failed: Optional[int] = None

    try:
        output = docker.run(
            image=image,
            name=source_container_name,
            detach=True,
            stream=False,
            entrypoint='bash',
            command="tail -f /dev/null",
        )
    except DockerException as e:
        print(e)
        failed = 0

    if not failed:
        # cp from the container
        try:
            output = docker.cp(
                source=source,
                destination=destination,
                source_container=source_container_name,
                stream=False,
            )
        except DockerException as e:
            print(e)
            failed = 1

    if not failed:
        # rm the container
        try:
            output = docker.rm(container=source_container_name, force=True, stream=False)
        except DockerException as e:
            print(e)
            failed = 2

    # check if the destination file exists
    if failed:
        if failed > 1:
            if dest_path.exists():
                import shutil

                shutil.rmtree(dest_path)
        if failed == 2:
            try:
                output = docker.rm(container=source_container_name, force=True, stream=False)
            except DockerException as e:
                pass
        # TODO introuduce custom exception to handle this type of cases where if the flow is not completed then it should raise exception which is handled by caller and then site creation check is done
        raise Exception(f"Failed to copy files from {source} to {destination}.")

    elif not Path(destination).exists():
        raise Exception(f"{destination} not found.")

    richprint.change_head(f"Populated {dest_path.name} directory.")
