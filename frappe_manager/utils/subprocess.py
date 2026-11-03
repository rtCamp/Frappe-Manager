"""
Generic subprocess streaming utilities.

This module provides generic command execution with real-time output streaming
that works for any subprocess (Docker, acme.sh, git, npm, etc.).
"""

import contextvars
import os
import uuid
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


_command_event_failed = False


def _command_event(kind: str, **fields: object) -> None:
    """Hand a command event to whatever sinks are attached, if any.

    Silent when no handler exists: this module runs in tests and in paths that never build one,
    and a missing display must never stop a subprocess.
    """
    global _command_event_failed  # noqa: PLW0603 -- once-per-process latch; the alternative is a line of noise per output line
    try:
        from frappe_manager.output_manager import get_global_output_handler

        emit = getattr(get_global_output_handler(), "emit_command_event", None)
        if emit is not None:
            emit(kind, **fields)
    except Exception as e:
        # Swallowed entirely, one bad sink drops every command.* event for the REST of the run
        # while the file keeps all of them, so the two surfaces disagree with nothing saying so.
        # Recorded ONCE: this runs per output line, and per-line logging would bury the very
        # file a reader would compare the stream against.
        if not _command_event_failed:
            _command_event_failed = True
            from frappe_manager.logger import get_logger

            get_logger(component="subprocess").warning(
                f"Command events stopped reaching their sink ({e}); the --events stream is "
                "incomplete from here, though fm.log still has every line."
            )


def reader(pipe, pipe_name: str, queue: Queue, cmd_id: str = "----"):
    """Read a pipe into the queue, logging each line under the command that produced it.

    `cmd_id` is what joins a line to its command: one run opens dozens of processes, and without
    it the file holds hundreds of output lines with nothing saying which wrote any of them.

    Args:
        pipe: The pipe to read from (stdout or stderr)
        pipe_name: Name identifier ("stdout" or "stderr")
        queue: Queue to put the lines into
        cmd_id: Four-character id of the command being read, shared with its COMMAND line
    """
    tag = {"cmd": f"{cmd_id} {'out' if pipe_name == 'stdout' else 'err'}"}
    try:
        buf = b""
        with pipe:
            while True:
                chunk = pipe.read(4096)
                if not chunk:
                    if buf:
                        line = buf.decode(errors="replace").strip("\r\n")
                        if line:
                            logger.debug(line, extra_fields=tag)
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
                                logger.debug(line, extra_fields=tag)
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
    input_data: bytes | None = None,
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
        input_data: Bytes written to the child's standard input, which is then closed. A child
            reading stdin hangs forever without that close, which is why the pipe is opened only
            when there is something to put in it.

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
    # Minted HERE, where the process is about to be opened, so the command line and every line it
    # produces carry the same id. Anywhere else and the two could not be joined.
    cmd_id = uuid.uuid4().hex[:4]
    cmd_tag = {"cmd": cmd_id}
    # The argv can carry a credential -- `bench reinstall --admin-password ...`, a db root
    # password, a token -- and this line goes to fm.log, which operators paste into issues.
    from frappe_manager.utils.helpers import redact_command_line  # local: helpers imports this module

    logger.debug(f"COMMAND: {redact_command_line(cmd)}", extra_fields=cmd_tag)

    if env is not None:
        subprocess_env = dict(os.environ)
        subprocess_env.update(env)
    else:
        subprocess_env = None

    cmd = list(map(str, cmd))

    process = Popen(
        cmd,
        stdout=PIPE,
        stderr=PIPE,
        stdin=PIPE if input_data is not None else None,
        env=subprocess_env,
        cwd=cwd,
    )

    if input_data is not None and process.stdin is not None:
        process.stdin.write(input_data)
        process.stdin.close()

    q = Queue()

    _command_event("command.started", cmd=cmd_id, argv=redact_command_line(cmd))

    # Copy the ambient logging context into the daemon reader threads so their
    # per-line debug traces stay corr/bench/op-tagged (contextvars don't cross
    # thread boundaries by themselves).
    ctx = contextvars.copy_context()
    stdout_thread = Thread(target=ctx.run, args=[reader, process.stdout, "stdout", q, cmd_id])
    stdout_thread.daemon = True
    stdout_thread.start()

    stderr_thread = Thread(target=contextvars.copy_context().run, args=[reader, process.stderr, "stderr", q, cmd_id])
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
        # Emitted HERE, on the thread draining the queue, rather than in the reader threads that
        # write the log line: the machine sink holds no lock, and this is the one place both a
        # streamed and a captured command's output passes through.
        _command_event("command.output", cmd=cmd_id, stream="out" if source == "stdout" else "err", text=line.decode(errors="replace"))
        yield source, line

    exit_code = process.wait()

    logger.debug(f"RETURN CODE: {exit_code}", extra_fields=cmd_tag)
    _command_event("command.finished", cmd=cmd_id, exit=exit_code)

    yield ("exit_code", str(exit_code).encode())
