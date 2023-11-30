import os
import shlex
import shutil
import signal
import subprocess
import sys
from datetime import datetime, timedelta
from importlib.metadata import version
from pathlib import Path
from queue import Queue
from subprocess import PIPE, Popen, run
from threading import Thread
from typing import Any, Dict, Iterable, List, Optional, Tuple, Union, overload
from frappe_manager.logger import log
from rich import control
from frappe_manager.docker_wrapper.DockerException import DockerException

logger = log.get_logger()

def reader(pipe, pipe_name, queue):
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
    env: Dict[str, str] = None,
) -> Iterable[Tuple[str, bytes]]:
    logger.debug('- -'*10)
    logger.debug(f"DOCKER COMMAND: {' '.join(full_cmd)}")
    if env is None:
        subprocess_env = None
    else:
        subprocess_env = dict(os.environ)
        subprocess_env.update(env)

    full_cmd = list(map(str, full_cmd))
    process = Popen(full_cmd, stdout=PIPE, stderr=PIPE, env=subprocess_env)
    q = Queue()
    full_stderr = b""  # for the error message
    # we use deamon threads to avoid hanging if the user uses ctrl+c
    th = Thread(target=reader, args=[process.stdout, "stdout", q])
    th.daemon = True
    th.start()
    th = Thread(target=reader, args=[process.stderr, "stderr", q])
    th.daemon = True
    th.start()

    for _ in range(2):
        for source, line in iter(q.get, None):
            yield source, line
            if source == "stderr":
                full_stderr += line

    exit_code = process.wait()

    logger.debug(f"RETURN CODE: {exit_code}")
    logger.debug('- -'*10)
    if exit_code != 0:
        raise DockerException(full_cmd, exit_code, stderr=full_stderr)

    yield ("exit_code", str(exit_code).encode())

def run_command_with_exit_code(
    full_cmd: list,
    env: Dict[str, str] = None,
    stream: bool = True,
    quiet: bool = False
):
    if stream:
        if quiet:
            try:
                for source ,line in stream_stdout_and_stderr(full_cmd):
                    if source == 'exit_code':
                        exit_code: int = int(line.decode())
                        return(exit_code)
            except Exception as e:
                pass
        else:
            return stream_stdout_and_stderr(full_cmd)
    else:
        from frappe_manager.site_manager.Richprint import richprint
        output = run(full_cmd)
        exit_code = output.returncode
        if exit_code != 0:
            raise DockerException(full_cmd,exit_code)

def parameter_to_option(param: str) -> str:
    """changes parameter's to option"""
    option = "--" + param.replace("_", "-")
    return option

def parameters_to_options(param: dict, exclude: list = []) -> list:
    # remove the self parameter
    temp_param: dict = dict(param)

    del temp_param["self"]

    for key in exclude:
        del temp_param[key]

    # remove all parameters which are not booleans
    params: list = []

    for key in temp_param.keys():
        value = temp_param[key]
        key = "--" + key.replace("_","-")
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
