"""
Context information for logging.

This module provides LoggerContext for adding contextual information
(bench name, operation, component) to log messages.
"""

from dataclasses import dataclass, field
from typing import Any, Dict, Optional


@dataclass
class LoggerContext:
    """
    Immutable context information for logging.
    
    Provides structured context (bench, operation, component) that can be
    formatted as prefixes in log messages or converted to dictionaries for
    structured logging.
    
    Example:
        >>> ctx = LoggerContext(bench="mybench", operation="create")
        >>> ctx.format()
        '[bench=mybench] [op=create]'
        
        >>> child = ctx.child(component="docker")
        >>> child.format()
        '[bench=mybench] [op=create] [component=docker]'
    """
    
    bench: Optional[str] = None
    operation: Optional[str] = None
    component: Optional[str] = None
    extra: Dict[str, Any] = field(default_factory=dict)
    
    def child(self, **overrides) -> 'LoggerContext':
        """
        Create a child context with inherited values and overrides.
        
        The child context inherits all values from the parent unless
        explicitly overridden. This is useful for propagating context
        through nested operations.
        
        Args:
            **overrides: Context fields to override (bench, operation, component, extra)
        
        Returns:
            New LoggerContext with inherited and overridden values
        
        Example:
            >>> parent = LoggerContext(bench="mybench", operation="create")
            >>> child = parent.child(component="docker")
            >>> child.bench  # Inherited
            'mybench'
            >>> child.component  # Overridden
            'docker'
        """
        # Extract extra overrides if provided
        extra_overrides = overrides.pop('extra', {})
        
        return LoggerContext(
            bench=overrides.get('bench', self.bench),
            operation=overrides.get('operation', self.operation),
            component=overrides.get('component', self.component),
            extra={**self.extra, **extra_overrides}
        )
    
    def format(self) -> str:
        """
        Format context as a prefix string for log messages.
        
        Formats all non-None context values as [key=value] pairs.
        Returns empty string if no context values are set.
        
        Returns:
            Formatted context string (e.g., "[bench=mybench] [op=create]")
        
        Example:
            >>> LoggerContext().format()
            ''
            >>> LoggerContext(bench="mybench").format()
            '[bench=mybench]'
            >>> LoggerContext(bench="mybench", operation="create").format()
            '[bench=mybench] [op=create]'
        """
        parts = []
        
        if self.bench:
            parts.append(f"bench={self.bench}")
        if self.operation:
            parts.append(f"op={self.operation}")
        if self.component:
            parts.append(f"component={self.component}")
        
        # Add extra fields
        for key, value in self.extra.items():
            if value is not None:  # Skip None values
                parts.append(f"{key}={value}")
        
        if parts:
            return "[" + "] [".join(parts) + "]"
        return ""
    
    def to_dict(self) -> Dict[str, Any]:
        """
        Convert context to dictionary for structured logging.
        
        Includes all context fields (even if None) plus any extra fields.
        Useful for JSON logging or structured log systems.
        
        Returns:
            Dictionary with all context values
        
        Example:
            >>> ctx = LoggerContext(bench="mybench", operation="create")
            >>> ctx.to_dict()
            {'bench': 'mybench', 'operation': 'create', 'component': None}
        """
        return {
            "bench": self.bench,
            "operation": self.operation,
            "component": self.component,
            **self.extra
        }
    
    def __bool__(self) -> bool:
        """
        Check if context has any values set.
        
        Returns True if any field (bench, operation, component, or extra) is non-None.
        
        Returns:
            True if context has values, False if empty
        
        Example:
            >>> bool(LoggerContext())
            False
            >>> bool(LoggerContext(bench="mybench"))
            True
        """
        return any([
            self.bench is not None,
            self.operation is not None,
            self.component is not None,
            bool(self.extra)
        ])
