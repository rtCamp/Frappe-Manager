import gzip
import logging
import logging.handlers
import os
import re
import shutil
from pathlib import Path

from rich.logging import RichHandler

from frappe_manager import CLI_LOG_DIRECTORY
from frappe_manager.exceptions import ConfigurationError

CLEANUP = 25


def namer(name):
    return name + ".gz"


def rotator(source, dest):
    with open(source, "rb") as f_in, gzip.open(dest, "wb") as f_out:
        shutil.copyfileobj(f_in, f_out)
    os.remove(source)


loggers: dict[str, logging.Logger] = {}

logging.addLevelName(CLEANUP, "CLEANUP")


class FMLOGGER(logging.Logger):
    def cleanup(self, msg, *args, **kwargs):
        if self.isEnabledFor(CLEANUP):
            self._log(CLEANUP, msg, args, **kwargs)


class FMLogger(logging.LoggerAdapter):
    """Context-aware logger facade.

    ``component`` is the only static axis (who is logging); bench / operation /
    correlation id are ambient (see logger.ambient) and stamped at emit time by
    ``ContextInjectFilter``. Per-call ``extra_fields`` ride along the record.
    """

    def __init__(self, logger: logging.Logger, component: str | None = None):
        super().__init__(logger, {})
        self.component = component

    def process(self, msg, kwargs):
        extra_fields = kwargs.pop("extra_fields", None)
        extra = dict(kwargs.get("extra") or {})
        if self.component:
            extra["fm_component"] = self.component
        if extra_fields:
            extra["fm_extra"] = extra_fields
        kwargs["extra"] = extra
        return msg, kwargs

    def cleanup(self, msg, *args, **kwargs):
        """Custom CLEANUP level pass-through (utils/helpers.py process-kill traces)."""
        if self.isEnabledFor(CLEANUP):
            self.log(CLEANUP, msg, *args, **kwargs)


class ContextInjectFilter(logging.Filter):
    """Stamp the ambient LoggerContext onto every record as ``fm_ctx``.

    Handler-level: ANY record passing the handler gets the token -- including
    records from bare ``logging.getLogger()`` users -- so the file formatter's
    ``%(fm_ctx)s`` never KeyErrors.
    """

    def filter(self, record: logging.LogRecord) -> bool:
        from frappe_manager.logger.ambient import current_context

        ctx = current_context()
        overrides = {}
        component = getattr(record, "fm_component", None)
        extra_fields = getattr(record, "fm_extra", None)
        if component:
            overrides["component"] = component
        if extra_fields:
            overrides["extra"] = extra_fields
        rendered = (ctx.child(**overrides) if overrides else ctx).format()
        record.fm_ctx = f" {rendered}" if rendered else ""
        return True


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
        msg = str(record.getMessage())

        if msg.strip() == "- -- -- -- -- -- -- -- -- -- -":
            record.msg = "[dim]---[/dim]"
            record.args = ()
            return True

        if msg.startswith("COMMAND:"):
            simplified = self._simplify_command(msg)
            record.msg = f"[dim]{simplified}[/dim]"
            record.args = ()
            return True

        if msg.startswith("RETURN CODE:"):
            if "RETURN CODE: 0" in msg:
                return False
            record.msg = f"[yellow]{msg}[/yellow]"
            record.args = ()
            return True

        if msg.startswith("{") and len(msg) > self.MAX_JSON_LENGTH:
            if '"Name":' in msg or '"Image":' in msg:
                truncated = msg[: self.MAX_JSON_LENGTH] + "... [dim][see log file][/dim]"
                record.msg = truncated
                record.args = ()
            else:
                truncated = msg[: self.MAX_JSON_LENGTH] + "... [dim][truncated][/dim]"
                record.msg = truncated
                record.args = ()
            return True

        if len(msg) > self.MAX_LINE_LENGTH and not msg.startswith("["):
            truncated = msg[: self.MAX_LINE_LENGTH] + "... [dim][truncated][/dim]"
            record.msg = truncated
            record.args = ()
            return True

        record.msg = msg
        record.args = ()
        return True

    def _simplify_command(self, cmd_line: str) -> str:
        """
        Simplify docker compose command output to show only the essential operation.

        Args:
            cmd_line: The full COMMAND: line from logger

        Returns:
            Simplified command string
        """
        cmd = cmd_line.replace("COMMAND: ", "")

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
                    if len(simplified) > 100:
                        simplified = simplified[:97] + "..."
                    return f"COMMAND: {simplified}"
                except Exception:
                    continue

        if len(cmd) > 80:
            return f"COMMAND: {cmd[:77]}..."

        return f"COMMAND: {cmd}"


def _add_console_handler(logger: logging.Logger, console_level: str) -> None:
    """
    Add a RichHandler to the logger for console output to stderr.

    When a Rich output handler is active, the handler shares its stderr
    Console: Rich's Console coordinates Live displays and log lines on its
    own, so no dedicated handler subclass is needed.

    Args:
        logger: The logger instance to add the handler to
        console_level: The logging level name (DEBUG, INFO, WARNING, ERROR)
    """
    for handler in logger.handlers[:]:
        if isinstance(handler, RichHandler):
            logger.removeHandler(handler)

    from frappe_manager.output_manager import get_global_output_handler, has_global_output_handler
    from frappe_manager.output_manager.logging_output import LoggingOutputHandler
    from frappe_manager.output_manager.rich_output import RichOutputHandler

    if has_global_output_handler():
        output = get_global_output_handler()

        if isinstance(output, LoggingOutputHandler):
            underlying_output = output.delegate
        else:
            underlying_output = output

        if isinstance(underlying_output, RichOutputHandler):
            console_handler = RichHandler(
                level=getattr(logging, console_level),
                rich_tracebacks=True,
                tracebacks_show_locals=True,
                show_time=False,
                show_path=False,
                show_level=True,
                markup=True,
                console=underlying_output.stderr,
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
    console_handler.addFilter(ContextInjectFilter())
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
            if isinstance(handler, RichHandler):
                logger.removeHandler(handler)


def _resolve_level(name: str) -> int:
    """Level name -> stdlib constant; loud failure on typos in fm_config.toml."""
    level = getattr(logging, name.upper(), None)
    if not isinstance(level, int):
        raise ConfigurationError(
            f"Invalid log level: {name!r}",
            details={"valid": "DEBUG, INFO, WARNING, ERROR, CRITICAL"},
        )
    return level


class _LazyRotatingFileHandler(logging.handlers.RotatingFileHandler):
    """A rotating file handler that touches the disk only when a record is actually written.

    Four modules acquire a logger at MODULE scope, so creating the file eagerly meant importing
    `frappe_manager.commands` created ~/frappe/logs/fm.log -- on a machine that had never run fm,
    for a command that only printed help, and even when the command was about to be refused. With
    `delay=True` the directory and the file appear on the first record instead, which is also the
    first moment an unwritable home is worth complaining about.
    """

    def _open(self):
        Path(self.baseFilename).parent.mkdir(parents=True, exist_ok=True)
        return super()._open()


def get_logger(
    log_dir=CLI_LOG_DIRECTORY,
    log_file_name="fm",
    console_level: str | None = None,
    file_level: str | None = None,
) -> FMLOGGER:
    """
    Creates a Log File and returns Logger object.

    Args:
        log_dir: Directory to store log files (default: CLI_LOG_DIRECTORY)
        log_file_name: Name of the log file without extension (default: 'fm')
        console_level: If specified, enables console logging at this level (DEBUG, INFO, WARNING, ERROR)
        file_level: File handler level. Applied on creation (default DEBUG) AND
            on the cached logger when explicitly passed -- so the config value
            loaded after logger creation still takes effect.

    Returns:
        FMLOGGER instance configured with file handler and optional console handler
    """
    logPath = log_dir / f"{log_file_name}.log"


    logger_exists = loggers.get(log_file_name) is not None
    if logger_exists:
        logger: logging.Logger | None = loggers.get(log_file_name)
        if file_level is not None:
            for handler in logger.handlers:
                if isinstance(handler, logging.handlers.RotatingFileHandler):
                    handler.setLevel(_resolve_level(file_level))
    else:
        logging.setLoggerClass(FMLOGGER)
        logger: logging.Logger | None = logging.getLogger(log_file_name)
        logger.setLevel(logging.DEBUG)
        # configured to rotate after 10 mb; backups are gzipped (namer adds .gz)

        handler = _LazyRotatingFileHandler(logPath, "a+", maxBytes=10485760, backupCount=3, delay=True)
        handler.setFormatter(logging.Formatter("[%(asctime)s] %(levelname)s:%(fm_ctx)s %(message)s"))
        handler.setLevel(_resolve_level(file_level or "DEBUG"))
        handler.rotator = rotator
        handler.namer = namer
        handler.addFilter(ContextInjectFilter())
        logger.addHandler(handler)

        loggers[log_file_name] = logger

    # Add or update console handler only if:
    # 1. Logger is being created for the first time (not logger_exists), OR
    # 2. console_level is explicitly provided (not None)
    # This prevents removing the console handler when business logic calls get_logger() without parameters
    if logger and (not logger_exists or console_level is not None):
        _update_console_handler(logger, console_level)

    return logger
