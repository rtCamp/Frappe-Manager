"""
Generic subprocess streaming utilities.

This module provides generic command execution with real-time output streaming
that works for any subprocess (Docker, acme.sh, git, npm, etc.).
"""

import os
from collections.abc import Iterator
from queue import Queue
from subprocess import PIPE, Popen
from threading import Thread

from frappe_manager.logger import log


def reader(pipe, pipe_name: str, queue: Queue):
    """
    Reads lines from a pipe and puts them into a queue.

    This function runs in a daemon thread to continuously read from
    stdout or stderr without blocking the main process.

    Args:
        pipe: The pipe to read from (stdout or stderr)
        pipe_name: Name identifier ("stdout" or "stderr")
        queue: Queue to put the lines into
    """
    logger = log.get_logger()
    try:
        with pipe:
            for line in iter(pipe.readline, b""):
                queue_line = line.decode().strip("\n")
                logger.debug(queue_line)
                queue.put((pipe_name, str(queue_line).encode()))
    finally:
        queue.put(None)


def stream_command_output(
    cmd: list,
    env: dict[str, str] | None = None,
    cwd: str | None = None,
) -> Iterator[tuple[str, bytes]]:
    """
    Execute a command and stream stdout/stderr output in real-time.

    This is a generic subprocess streaming function that works for any command.
    It yields (source, line) tuples as output is produced, then yields
    ("exit_code", code) when the process completes.

    Unlike stream_stdout_and_stderr in utils/docker.py, this function does NOT
    raise exceptions on non-zero exit codes. It simply yields the exit code,
    allowing the caller to decide how to handle failures.

    Args:
        cmd: Command to execute as list of strings
        env: Environment variables (merged with os.environ if provided)
        cwd: Working directory for command execution

    Yields:
        Tuple[str, bytes]:
            - ("stdout", line) for stdout output
            - ("stderr", line) for stderr output
            - ("exit_code", code) when process completes

    Example:
        >>> for source, line in stream_command_output(["echo", "hello"]):
        ...     if source == "exit_code":
        ...         exit_code = int(line.decode())
        ...         print(f"Exited with: {exit_code}")
        ...     else:
        ...         print(f"{source}: {line.decode()}")

    Note:
        This function uses daemon threads to read from stdout/stderr pipes,
        preventing deadlocks when the process produces large amounts of output.
    """
    logger = log.get_logger()
    logger.debug("- -" * 10)
    logger.debug(f"COMMAND: {' '.join(cmd)}")

    # Prepare environment
    if env is not None:
        subprocess_env = dict(os.environ)
        subprocess_env.update(env)
    else:
        subprocess_env = None

    # Convert all elements to strings
    cmd = list(map(str, cmd))

    # Start process with pipes
    process = Popen(cmd, stdout=PIPE, stderr=PIPE, env=subprocess_env, cwd=cwd)

    # Setup queue and reader threads
    q = Queue()

    # Use daemon threads to avoid hanging on ctrl+c
    stdout_thread = Thread(target=reader, args=[process.stdout, "stdout", q])
    stdout_thread.daemon = True
    stdout_thread.start()

    stderr_thread = Thread(target=reader, args=[process.stderr, "stderr", q])
    stderr_thread.daemon = True
    stderr_thread.start()

    # Yield output as it arrives
    for _ in range(2):  # Wait for both threads to finish
        for source, line in iter(q.get, None):
            yield source, line

    # Wait for process to complete and yield exit code
    exit_code = process.wait()

    logger.debug(f"RETURN CODE: {exit_code}")
    logger.debug("- -" * 10)

    yield ("exit_code", str(exit_code).encode())
