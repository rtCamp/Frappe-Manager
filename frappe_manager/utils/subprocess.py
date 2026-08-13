"""
Generic subprocess streaming utilities.

This module provides generic command execution with real-time output streaming
that works for any subprocess (Docker, acme.sh, git, npm, etc.).
"""

import contextvars
import os
from collections.abc import Iterator
from queue import Empty, Queue
from subprocess import PIPE, Popen
from threading import Thread
from time import monotonic

from frappe_manager.logger import get_logger

logger = get_logger(component="subprocess")

# How long the drain loop keeps waiting for the reader threads' sentinels AFTER
# the child process has already exited. Reader threads normally post their
# sentinel within milliseconds of the pipes closing, but one can wedge for good
# (e.g. blocked inside logging's flush while the rotating handler gzips a
# rotated log file under its lock), and then its `finally: queue.put(None)`
# never runs. Capping the post-exit wait means a wedged reader costs us at most
# a tail of log lines instead of hanging fm forever.
DRAIN_GRACE_PERIOD_AFTER_EXIT = 10.0

# How long a single blocking queue read may park before we re-check whether the
# child process is still alive. Only bounds responsiveness, never total wait.
DRAIN_POLL_INTERVAL = 0.5


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
    try:
        buf = b""
        with pipe:
            while True:
                chunk = pipe.read(4096)
                if not chunk:
                    if buf:
                        line = buf.decode(errors="replace").strip("\r\n")
                        if line:
                            logger.debug(line)
                            queue.put((pipe_name, line.encode()))
                    break
                buf += chunk
                while True:
                    for sep in (b"\n", b"\r"):
                        idx = buf.find(sep)
                        if idx != -1:
                            line = buf[:idx].decode(errors="replace").strip("\r\n")
                            buf = buf[idx + 1 :]
                            if line:
                                logger.debug(line)
                                queue.put((pipe_name, line.encode()))
                            break
                    else:
                        break
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
        The drain loop is bounded: while the child process is still running it
        waits indefinitely (a quiet long-running command is never cut off), but
        once the child has exited the reader threads only get
        DRAIN_GRACE_PERIOD_AFTER_EXIT seconds to finish. A wedged reader thread
        can therefore cost trailing output, never the exit code.
    """
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
    # Copy the ambient logging context into the daemon reader threads so their
    # per-line debug traces stay corr/bench/op-tagged (contextvars don't cross
    # thread boundaries by themselves).
    ctx = contextvars.copy_context()
    stdout_thread = Thread(target=ctx.run, args=[reader, process.stdout, "stdout", q])
    stdout_thread.daemon = True
    stdout_thread.start()

    stderr_thread = Thread(target=contextvars.copy_context().run, args=[reader, process.stderr, "stderr", q])
    stderr_thread.daemon = True
    stderr_thread.start()

    # Yield output as it arrives.
    #
    # Each reader thread posts a `None` sentinel when it is done, so we drain
    # until both sentinels have arrived. Reads are bounded so a wedged reader
    # can never hold the pipeline open forever: while the child is alive we keep
    # waiting (silence is not failure), and once it has exited the readers only
    # get a bounded grace period to drain.
    outstanding_sentinels = 2
    grace_deadline: float | None = None

    while outstanding_sentinels > 0:
        try:
            item = q.get(timeout=DRAIN_POLL_INTERVAL)
        except Empty:
            if process.poll() is None:
                # Child is still running and simply quiet; keep waiting.
                continue
            if grace_deadline is None:
                grace_deadline = monotonic() + DRAIN_GRACE_PERIOD_AFTER_EXIT
                continue
            if monotonic() < grace_deadline:
                continue
            logger.warning(
                f"Stopped draining output after {DRAIN_GRACE_PERIOD_AFTER_EXIT}s:"
                f" process already exited but {outstanding_sentinels} reader thread(s) appear wedged."
                " Trailing output may be missing."
            )
            break

        if item is None:
            outstanding_sentinels -= 1
            continue

        source, line = item
        yield source, line

    # Wait for process to complete and yield exit code
    exit_code = process.wait()

    logger.debug(f"RETURN CODE: {exit_code}")
    logger.debug("- -" * 10)

    yield ("exit_code", str(exit_code).encode())
