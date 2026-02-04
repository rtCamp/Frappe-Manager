import gzip
import logging
import logging.handlers
import os
import re
import shutil

from rich.logging import RichHandler

from frappe_manager import CLI_LOG_DIRECTORY
from frappe_manager.exceptions import ConfigurationError
from frappe_manager.logger.live_aware_handler import LiveAwareRichHandler

# Define MESSAGE log level
CLEANUP = 25


def namer(name):
    return name + ".gz"


def rotator(source, dest):
    with open(source, "rb") as f_in:
        with gzip.open(dest, "wb") as f_out:
            shutil.copyfileobj(f_in, f_out)
    os.remove(source)


loggers: dict[str, logging.Logger] = {}

# "Register" new loggin level
logging.addLevelName(CLEANUP, "CLEANUP")


class FMLOGGER(logging.Logger):
    def cleanup(self, msg, *args, **kwargs):
        if self.isEnabledFor(CLEANUP):
            self._log(CLEANUP, msg, args, **kwargs)


class ConsoleLogFilter(logging.Filter):
    """
    Filter to clean up log messages for console display.

    This filter improves readability by:
    - Truncating long JSON outputs
    - Shortening repetitive separators
    - Simplifying docker commands to show only the key operation
    - Dimming less important debug information

    Note: File logs remain unchanged with full details.
    """

    MAX_JSON_LENGTH = 120
    MAX_LINE_LENGTH = 150

    def filter(self, record: logging.LogRecord) -> bool:
        """
        Process and clean up log messages before display.

        Args:
            record: The log record to filter

        Returns:
            True (always allow the message through, just modify it)
        """
        msg = str(record.getMessage())

        # Shorten long separator lines
        if msg.strip() == "- -- -- -- -- -- -- -- -- -- -":
            record.msg = "[dim]---[/dim]"
            record.args = ()
            return True

        if msg.startswith("COMMAND:"):
            simplified = self._simplify_command(msg)
            record.msg = f"[dim]{simplified}[/dim]"
            record.args = ()
            return True

        # Dim RETURN CODE
        if msg.startswith("RETURN CODE:"):
            # Only show return code if it's non-zero (error)
            if "RETURN CODE: 0" in msg:
                # Suppress successful return codes to reduce noise
                return False
            # Highlight non-zero return codes
            record.msg = f"[yellow]{msg}[/yellow]"
            record.args = ()
            return True

        # Truncate long JSON blobs (likely from Docker commands)
        if msg.startswith("{") and len(msg) > self.MAX_JSON_LENGTH:
            # Try to extract useful summary from JSON if it's docker container info
            if '"Name":' in msg or '"Image":' in msg:
                # Docker container/image JSON - just show truncated version
                truncated = msg[: self.MAX_JSON_LENGTH] + "... [dim][see log file][/dim]"
                record.msg = truncated
                record.args = ()
            else:
                # Generic JSON
                truncated = msg[: self.MAX_JSON_LENGTH] + "... [dim][truncated][/dim]"
                record.msg = truncated
                record.args = ()
            return True

        # Truncate other excessively long lines
        if len(msg) > self.MAX_LINE_LENGTH and not msg.startswith("["):
            # Don't truncate if it already has Rich markup
            truncated = msg[: self.MAX_LINE_LENGTH] + "... [dim][truncated][/dim]"
            record.msg = truncated
            record.args = ()
            return True

        return True

    def _simplify_command(self, cmd_line: str) -> str:
        """
        Simplify docker compose command output to show only the essential operation.

        Args:
            cmd_line: The full COMMAND: line from logger

        Returns:
            Simplified command string
        """
        # Remove "COMMAND: " prefix
        cmd = cmd_line.replace("COMMAND: ", "")

        # Common patterns to simplify
        simplifications = [
            # Docker compose exec commands - show only the actual command
            (r"docker compose -f [^\s]+ exec (?:--user \w+ )?(?:--workdir [^\s]+ )?(\w+) (.+)", r"[\1] \2"),
            # Docker compose up/down/ps - show operation and service
            (r"docker compose -f [^\s]+ (up|down|ps|start|stop|restart) (.+)", r"compose \1 \2"),
            # Docker commands - show just the operation
            (r"docker (\w+) (.+)", r"docker \1 ..."),
        ]

        for pattern, replacement in simplifications:
            match = re.search(pattern, cmd)
            if match:
                try:
                    simplified = re.sub(pattern, replacement, cmd)
                    # Further trim if still too long
                    if len(simplified) > 100:
                        simplified = simplified[:97] + "..."
                    return f"COMMAND: {simplified}"
                except Exception:
                    # If regex replacement fails, continue to next pattern
                    continue

        # Fallback: just truncate long commands
        if len(cmd) > 80:
            return f"COMMAND: {cmd[:77]}..."

        return f"COMMAND: {cmd}"


def _add_console_handler(logger: logging.Logger, console_level: str) -> None:
    """
    Add a LiveAwareRichHandler to the logger for console output to stderr.

    This handler coordinates with the Live spinner display to prevent
    output corruption and visual artifacts.

    Args:
        logger: The logger instance to add the handler to
        console_level: The logging level name (DEBUG, INFO, WARNING, ERROR)
    """
    for handler in logger.handlers[:]:
        if isinstance(handler, (RichHandler, LiveAwareRichHandler)):
            logger.removeHandler(handler)

    from frappe_manager.output_manager import get_global_output_handler, has_global_output_handler
    from frappe_manager.output_manager.rich_output import RichOutputHandler

    if has_global_output_handler():
        output = get_global_output_handler()
        if isinstance(output, RichOutputHandler):
            console_handler = LiveAwareRichHandler(
                level=getattr(logging, console_level),
                rich_tracebacks=True,
                tracebacks_show_locals=True,
                show_time=False,
                show_path=False,
                show_level=True,
                markup=True,
                console=output.stderr,
                live_display=output.live,
            )
        else:
            console_handler = RichHandler(
                level=getattr(logging, console_level),
                rich_tracebacks=True,
                tracebacks_show_locals=True,
                show_time=False,
                show_path=False,
                show_level=True,
                markup=True,
            )
    else:
        console_handler = RichHandler(
            level=getattr(logging, console_level),
            rich_tracebacks=True,
            tracebacks_show_locals=True,
            show_time=False,
            show_path=False,
            show_level=True,
            markup=True,
        )

    console_handler.setFormatter(logging.Formatter("%(message)s"))
    console_handler.addFilter(ConsoleLogFilter())
    logger.addHandler(console_handler)


def _update_console_handler(logger: logging.Logger, console_level: str | None) -> None:
    """
    Update or remove the console handler based on console_level.

    Args:
        logger: The logger instance to update
        console_level: The logging level name (DEBUG, INFO, WARNING, ERROR) or None to remove
    """
    if console_level:
        _add_console_handler(logger, console_level)
    else:
        for handler in logger.handlers[:]:
            if isinstance(handler, (RichHandler, LiveAwareRichHandler)):
                logger.removeHandler(handler)


def get_logger(log_dir=CLI_LOG_DIRECTORY, log_file_name="fm", console_level: str | None = None) -> FMLOGGER:
    """
    Creates a Log File and returns Logger object.

    Args:
        log_dir: Directory to store log files (default: CLI_LOG_DIRECTORY)
        log_file_name: Name of the log file without extension (default: 'fm')
        console_level: If specified, enables console logging at this level (DEBUG, INFO, WARNING, ERROR)

    Returns:
        FMLOGGER instance configured with file handler and optional console handler
    """
    # Build Log File Full Path
    logPath = log_dir / f"{log_file_name}.log"

    try:
        log_dir.mkdir(parents=True, exist_ok=True)
    except PermissionError as e:
        # Use print since logger hasn't been initialized yet
        print(f"FATAL: Logging not working. {e}")
        raise ConfigurationError(f"Logging not working: {e}", details={"log_dir": str(log_dir)})

    # Create logger object and set the format for logging and other attributes
    logger_exists = loggers.get(log_file_name) is not None
    if logger_exists:
        logger: logging.Logger | None = loggers.get(log_file_name)
    else:
        logging.setLoggerClass(FMLOGGER)
        logger: logging.Logger | None = logging.getLogger(log_file_name)
        logger.setLevel(logging.DEBUG)

        # configured to roatate after 10 mb
        handler = logging.handlers.RotatingFileHandler(logPath, "a+", maxBytes=10485760, backupCount=3)
        handler.setFormatter(logging.Formatter("[%(asctime)s] %(levelname)s: %(message)s"))
        handler.rotator = rotator
        logger.addHandler(handler)

        # save logger to dict loggers
        loggers[log_file_name] = logger

    # Add or update console handler only if:
    # 1. Logger is being created for the first time (not logger_exists), OR
    # 2. console_level is explicitly provided (not None)
    # This prevents removing the console handler when business logic calls get_logger() without parameters
    if logger and (not logger_exists or console_level is not None):
        _update_console_handler(logger, console_level)

    return logger
