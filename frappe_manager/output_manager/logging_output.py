"""
Logging output handler wrapper.

This wrapper automatically logs all OutputHandler calls to a file logger,
providing a single source of truth for debugging.
"""

import logging
from collections.abc import Iterable
from typing import Any, Union

from frappe_manager.logger.contextual import ContextualLogger
from frappe_manager.output_manager.base import OutputHandler


class LoggingOutputHandler(OutputHandler):
    """
    Output handler wrapper that logs all operations.

    This handler wraps another OutputHandler and logs all method calls
    to a Python logger, providing automatic logging of user-facing messages.

    Supports both standard logging.Logger and ContextualLogger for automatic
    context inclusion in log messages.

    Usage:
        # With standard logger
        logger = get_logger()
        rich = RichOutputHandler(verbose=True)
        output = LoggingOutputHandler(rich, logger)

        # With contextual logger
        context = LoggerContext(bench="mybench", operation="create")
        contextual_logger = ContextualLogger(logger, context)
        output = LoggingOutputHandler(rich, contextual_logger)

        # Now all output calls are automatically logged with context
        output.print("Creating bench..")
        # User sees: ⚡ Creating bench...
        # Log file: [2024-01-07 12:00:00] INFO: [bench=mybench] [op=create] [OUTPUT] Creating bench...
    """

    def __init__(
        self, delegate: OutputHandler, logger: Union[logging.Logger, ContextualLogger], log_prefix: str = "[OUTPUT]"
    ):
        """
        Initialize logging wrapper.

        Args:
            delegate: OutputHandler to wrap and delegate to
            logger: Python logger or ContextualLogger for file logging
            log_prefix: Prefix for log messages (default: "[OUTPUT]")
        """
        super().__init__(delegate.verbose)
        self.delegate = delegate

        if isinstance(logger, logging.Logger):
            self.logger = ContextualLogger(logger)
        else:
            self.logger = logger

        self.log_prefix = log_prefix

    def _log_message(self, level: int, message: str) -> None:
        """
        Helper to log with prefix.

        Args:
            level: Log level (logging.DEBUG, INFO, WARNING, ERROR)
            message: Message to log
        """
        prefixed_message = f"{self.log_prefix} {message}"

        if level == logging.DEBUG:
            self.logger.debug(prefixed_message)
        elif level == logging.INFO:
            self.logger.info(prefixed_message)
        elif level == logging.WARNING:
            self.logger.warning(prefixed_message)
        elif level == logging.ERROR:
            self.logger.error(prefixed_message)
        else:
            self.logger.info(prefixed_message)

    def start(self, text: str) -> None:
        """
        Start operation (logged at INFO level).

        Args:
            text: Initial status message
        """
        self._log_message(logging.INFO, f"START: {text}")
        self.delegate.start(text)
        super().start(text)

    def change_head(self, text: str, style: str | None = None) -> None:
        """
        Change head (logged at DEBUG level).

        Args:
            text: New status message
            style: Optional style hint
        """
        self._log_message(logging.DEBUG, f"CHANGE_HEAD: {text}")
        self.delegate.change_head(text, style)

    def update_head(self, text: str) -> None:
        """
        Update head (logged at DEBUG level).

        Args:
            text: New head text
        """
        self._log_message(logging.DEBUG, f"UPDATE_HEAD: {text}")
        self.delegate.update_head(text)

    def stop(self) -> None:
        """
        Stop operation (logged at DEBUG level).
        """
        self._log_message(logging.DEBUG, "STOP")
        self.delegate.stop()
        super().stop()

    def debug(self, text: str, emoji_code: str = ":bug:", **kwargs) -> None:
        """
        Debug message (logged at DEBUG level).

        Args:
            text: Debug message
            emoji_code: Optional emoji code
            **kwargs: Additional arguments
        """
        self._log_message(logging.DEBUG, text)
        self.delegate.debug(text, emoji_code, **kwargs)

    def info(self, text: str, emoji_code: str = ":information:", **kwargs) -> None:
        """
        Info message (logged at INFO level).

        Args:
            text: Info message
            emoji_code: Optional emoji code
            **kwargs: Additional arguments
        """
        self._log_message(logging.INFO, text)
        self.delegate.info(text, emoji_code, **kwargs)

    def print(self, text: str, emoji_code: str = ":zap:", prefix: str | None = None, **kwargs) -> None:
        """
        Print message (logged at INFO level).

        Args:
            text: Message to print
            emoji_code: Optional emoji code
            prefix: Optional prefix
            **kwargs: Additional arguments
        """
        full_text = f"{prefix} {text}" if prefix else text
        self._log_message(logging.INFO, full_text)
        self.delegate.print(text, emoji_code, prefix, **kwargs)

    def display_error(self, text: str, emoji_code: str = ":no_entry:") -> None:
        """
        Display error (logged at ERROR level).

        Args:
            text: Error message
            emoji_code: Optional emoji code
        """
        self._log_message(logging.ERROR, text)
        self.delegate.display_error(text, emoji_code)

    def error(self, text: str, exception: Exception, emoji_code: str = ":no_entry:") -> None:
        """
        Display error and raise the exception (logged at ERROR level with exception details).

        This method always raises the provided exception after logging and displaying.

        Args:
            text: Error message
            exception: Exception to raise (required)
            emoji_code: Optional emoji code

        Raises:
            Exception: Always raises the provided exception
        """
        self._log_message(logging.ERROR, f"{text} | Exception: {type(exception).__name__}: {str(exception)}")
        self.logger.exception(f"{self.log_prefix} Exception details:", exc_info=exception)
        self.delegate.error(text, exception, emoji_code)

    def warning(self, text: str, emoji_code: str = ":warning:") -> None:
        """
        Display warning (logged at WARNING level).

        Args:
            text: Warning message
            emoji_code: Optional emoji code
        """
        self._log_message(logging.WARNING, text)
        self.delegate.warning(text, emoji_code)

    def live_lines(
        self,
        data: Iterable[tuple[str, bytes]],
        stdout: bool = True,
        stderr: bool = True,
        lines: int = 4,
        padding: tuple[int, int, int, int] = (0, 0, 0, 2),
        stop_string: str | None = None,
        log_prefix: str = "=>",
    ) -> None:
        """
        Display live lines (logs start/stop, not individual lines for performance).

        Note: Individual lines are not logged to avoid performance overhead.
        Only the start and completion of live output is logged.

        Args:
            data: Iterator yielding (source, line) tuples
            stdout: Whether to display stdout lines
            stderr: Whether to display stderr lines
            lines: Maximum number of lines to display
            padding: Padding around displayed lines
            stop_string: String that stops display when found
            log_prefix: Prefix for each line
        """
        self._log_message(logging.DEBUG, f"LIVE_LINES: Starting (stdout={stdout}, stderr={stderr})")
        self.delegate.live_lines(data, stdout, stderr, lines, padding, stop_string, log_prefix)
        self._log_message(logging.DEBUG, "LIVE_LINES: Completed")

    def update_live(self, renderable: Any = None, padding: tuple[int, int, int, int] = (0, 0, 0, 0)) -> None:
        """
        Update live display (not logged for performance).

        Args:
            renderable: Content to display
            padding: Padding around content
        """
        self.delegate.update_live(renderable, padding)

    def prompt_ask(
        self,
        prompt: str = "",
        choices: list | None = None,
        default: str | None = None,
        force_yes: bool = False,
        required_flag: str | None = None,
        **kwargs,
    ) -> str:
        self._log_message(logging.INFO, f"PROMPT: {prompt}")

        response = self.delegate.prompt_ask(
            prompt=prompt, choices=choices, default=default, force_yes=force_yes, required_flag=required_flag, **kwargs
        )

        if "password" not in prompt.lower():
            self._log_message(logging.INFO, f"PROMPT_RESPONSE: {response}")
        else:
            self._log_message(logging.INFO, "PROMPT_RESPONSE: [password hidden]")

        return response

    def prompt_fuzzy(
        self,
        prompt: str,
        choices: list[str],
        default: str | None = None,
        required_flag: str | None = None,
        **kwargs,
    ) -> str:
        self.logger.info(f"[OUTPUT] FUZZY_PROMPT: {prompt} (choices: {len(choices)})")

        result = self.delegate.prompt_fuzzy(
            prompt=prompt,
            choices=choices,
            default=default,
            required_flag=required_flag,
            **kwargs,
        )

        self.logger.info(f"[OUTPUT] FUZZY_RESPONSE: {result}")
        return result

    def set_interactive_mode(self, non_interactive_flag: bool) -> None:
        """
        Set interactive mode on wrapper AND delegate.

        This ensures the delegate (RichOutputHandler) respects the non-interactive flag
        when deciding whether to show spinners.

        Args:
            non_interactive_flag: True to disable interactive features (spinners, prompts)
        """
        # Set on wrapper itself
        super().set_interactive_mode(non_interactive_flag)

        # Forward to delegate so it respects the flag too
        if hasattr(self.delegate, 'set_interactive_mode'):
            self.delegate.set_interactive_mode(non_interactive_flag)

    @property
    def should_stream_docker(self) -> bool:
        return self.delegate.should_stream_docker

    def print_data(self, data: Any, **kwargs) -> None:
        self._log_message(logging.INFO, f"DATA: {data}")
        self.delegate.print_data(data, **kwargs)

    def print_status(self, text: str, emoji_code: str = ":zap:", **kwargs) -> None:
        self._log_message(logging.INFO, f"STATUS: {text}")
        self.delegate.print_status(text, emoji_code, **kwargs)
