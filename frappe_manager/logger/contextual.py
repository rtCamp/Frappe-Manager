"""
Contextual logger adapter.

This module provides ContextualLogger which wraps a standard Python logger
to automatically add context information (bench name, operation, component)
to all log messages.
"""

import logging
from typing import Any, Dict, Optional

from frappe_manager.logger.context import LoggerContext


class ContextualLogger:
    """
    Adapter that wraps logging.Logger to add context to all messages.

    Automatically prefixes all log messages with formatted context information.
    Supports creating child loggers with extended context for nested operations.

    Example:
        >>> import logging
        >>> base_logger = logging.getLogger("fm")
        >>> context = LoggerContext(bench="mybench", operation="create")
        >>> logger = ContextualLogger(base_logger, context)
        >>> logger.info("Starting operation")
        # Logs: "[bench=mybench] [op=create] Starting operation"

        >>> child = logger.child(component="docker")
        >>> child.info("Building containers")
        # Logs: "[bench=mybench] [op=create] [component=docker] Building containers"
    """

    def __init__(self, logger: logging.Logger, context: Optional[LoggerContext] = None):
        """
        Initialize contextual logger.

        Args:
            logger: Base Python logger to wrap
            context: Optional context to add to messages (defaults to empty context)
        """
        self.logger = logger
        self.context = context or LoggerContext()

    def _format_message(self, msg: str, extra: Optional[Dict[str, Any]] = None) -> str:
        prefix = self.context.format()
        extra_suffix = self._format_extra_fields(extra) if extra else ""

        if prefix and extra_suffix:
            return f"{prefix} {msg}{extra_suffix}"
        elif prefix:
            return f"{prefix} {msg}"
        elif extra_suffix:
            return f"{msg}{extra_suffix}"
        return msg

    def _format_extra_fields(self, extra: Dict[str, Any]) -> str:
        if not extra:
            return ""

        parts = []
        for key, value in extra.items():
            if value is not None:
                parts.append(f"{key}={value}")

        if parts:
            return " | " + " | ".join(parts)
        return ""

    def debug(self, msg: str, extra: Optional[Dict[str, Any]] = None, *args, **kwargs):
        self.logger.debug(self._format_message(msg, extra), *args, **kwargs)

    def info(self, msg: str, extra: Optional[Dict[str, Any]] = None, *args, **kwargs):
        self.logger.info(self._format_message(msg, extra), *args, **kwargs)

    def warning(self, msg: str, extra: Optional[Dict[str, Any]] = None, *args, **kwargs):
        self.logger.warning(self._format_message(msg, extra), *args, **kwargs)

    def error(self, msg: str, extra: Optional[Dict[str, Any]] = None, *args, **kwargs):
        self.logger.error(self._format_message(msg, extra), *args, **kwargs)

    def exception(self, msg: str, extra: Optional[Dict[str, Any]] = None, *args, **kwargs):
        self.logger.exception(self._format_message(msg, extra), *args, **kwargs)

    def child(self, **context_overrides) -> 'ContextualLogger':
        """
        Create child logger with extended context.

        The child inherits all context from parent and can override or extend it.
        Useful for nested operations that need additional context.

        Args:
            **context_overrides: Context fields to add/override (bench, operation, component, extra)

        Returns:
            New ContextualLogger with extended context

        Example:
            >>> parent = ContextualLogger(logger, LoggerContext(bench="mybench"))
            >>> child = parent.child(operation="create", component="docker")
            >>> child.info("Starting containers")
            # Logs: "[bench=mybench] [op=create] [component=docker] Starting containers"
        """
        new_context = self.context.child(**context_overrides)
        return ContextualLogger(self.logger, new_context)

    # Pass-through properties and methods for compatibility

    @property
    def level(self) -> int:
        """Get the logger's current level."""
        return self.logger.level

    def setLevel(self, level: int):
        """
        Set the logger's level.

        Args:
            level: Logging level (logging.DEBUG, logging.INFO, etc.)
        """
        self.logger.setLevel(level)

    def isEnabledFor(self, level: int) -> bool:
        """
        Check if logger is enabled for a given level.

        Args:
            level: Logging level to check

        Returns:
            True if logger would emit a message at this level
        """
        return self.logger.isEnabledFor(level)

    @property
    def name(self) -> str:
        """Get the logger's name."""
        return self.logger.name

    def get_context(self) -> LoggerContext:
        """
        Get the current context.

        Returns:
            Current LoggerContext
        """
        return self.context
