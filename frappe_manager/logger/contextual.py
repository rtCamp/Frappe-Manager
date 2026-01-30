"""
Contextual logger adapter.

This module provides ContextualLogger which wraps a standard Python logger
to automatically add context information (bench name, operation, component)
to all log messages.
"""

import logging
from typing import Optional

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
    
    def __init__(
        self, 
        logger: logging.Logger, 
        context: Optional[LoggerContext] = None
    ):
        """
        Initialize contextual logger.
        
        Args:
            logger: Base Python logger to wrap
            context: Optional context to add to messages (defaults to empty context)
        """
        self.logger = logger
        self.context = context or LoggerContext()
    
    def _format_message(self, msg: str) -> str:
        """
        Add context prefix to message if context exists.
        
        Args:
            msg: Original log message
        
        Returns:
            Message with context prefix (or original if no context)
        """
        prefix = self.context.format()
        if prefix:
            return f"{prefix} {msg}"
        return msg
    
    def debug(self, msg: str, *args, **kwargs):
        """
        Log at DEBUG level with context.
        
        Args:
            msg: Message to log
            *args: Format arguments for message
            **kwargs: Additional keyword arguments for logger
        """
        self.logger.debug(self._format_message(msg), *args, **kwargs)
    
    def info(self, msg: str, *args, **kwargs):
        """
        Log at INFO level with context.
        
        Args:
            msg: Message to log
            *args: Format arguments for message
            **kwargs: Additional keyword arguments for logger
        """
        self.logger.info(self._format_message(msg), *args, **kwargs)
    
    def warning(self, msg: str, *args, **kwargs):
        """
        Log at WARNING level with context.
        
        Args:
            msg: Message to log
            *args: Format arguments for message
            **kwargs: Additional keyword arguments for logger
        """
        self.logger.warning(self._format_message(msg), *args, **kwargs)
    
    def error(self, msg: str, *args, **kwargs):
        """
        Log at ERROR level with context.
        
        Args:
            msg: Message to log
            *args: Format arguments for message
            **kwargs: Additional keyword arguments for logger
        """
        self.logger.error(self._format_message(msg), *args, **kwargs)
    
    def exception(self, msg: str, *args, **kwargs):
        """
        Log exception with context at ERROR level.
        
        Includes stack trace information. Should be called from exception handlers.
        
        Args:
            msg: Message to log
            *args: Format arguments for message
            **kwargs: Additional keyword arguments for logger
        """
        self.logger.exception(self._format_message(msg), *args, **kwargs)
    
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
