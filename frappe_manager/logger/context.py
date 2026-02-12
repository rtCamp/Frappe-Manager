"""
Context information for logging.

This module provides LoggerContext for adding contextual information
(bench name, operation, component) to log messages.
"""

from dataclasses import dataclass, field
from typing import Any


@dataclass
class LoggerContext:
    """
    Immutable context information for logging.

    Provides structured context (bench, operation, component, correlation_id) that can be
    formatted as prefixes in log messages or converted to dictionaries for
    structured logging.

    Example:
        >>> ctx = LoggerContext(bench="mybench", operation="create", correlation_id="550e8400-...")
        >>> ctx.format()
        '[corr=550e8400] [bench=mybench] [op=create]'

        >>> child = ctx.child(component="docker")
        >>> child.format()
        '[corr=550e8400] [bench=mybench] [op=create] [component=docker]'
    """

    bench: str | None = None
    operation: str | None = None
    component: str | None = None
    correlation_id: str | None = None
    extra: dict[str, Any] = field(default_factory=dict)

    def child(self, **overrides) -> "LoggerContext":
        """
        Create a child context with inherited values and overrides.

        The child context inherits all values from the parent unless
        explicitly overridden. This is useful for propagating context
        through nested operations.

        Args:
            **overrides: Context fields to override (bench, operation, component, correlation_id, extra)

        Returns:
            New LoggerContext with inherited and overridden values

        Example:
            >>> parent = LoggerContext(bench="mybench", operation="create", correlation_id="550e8400-...")
            >>> child = parent.child(component="docker")
            >>> child.bench  # Inherited
            'mybench'
            >>> child.component  # Overridden
            'docker'
            >>> child.correlation_id  # Inherited
            '550e8400-...'
        """
        # Extract extra overrides if provided
        extra_overrides = overrides.pop("extra", {})

        return LoggerContext(
            bench=overrides.get("bench", self.bench),
            operation=overrides.get("operation", self.operation),
            component=overrides.get("component", self.component),
            correlation_id=overrides.get("correlation_id", self.correlation_id),
            extra={**self.extra, **extra_overrides},
        )

    def format(self) -> str:
        """
        Format context as a prefix string for log messages.

        Formats all non-None context values as [key=value] pairs.
        Correlation ID is shortened to first 8 characters for readability.
        Returns empty string if no context values are set.

        Returns:
            Formatted context string (e.g., "[corr=550e8400] [bench=mybench] [op=create]")

        Example:
            >>> LoggerContext().format()
            ''
            >>> LoggerContext(correlation_id="550e8400-e29b-41d4-a716-446655440000").format()
            '[corr=550e8400]'
            >>> LoggerContext(bench="mybench", operation="create", correlation_id="550e8400-...").format()
            '[corr=550e8400] [bench=mybench] [op=create]'
        """
        parts = []

        # Correlation ID comes first (shortened to 8 chars for readability)
        if self.correlation_id:
            short_corr = self.correlation_id[:8] if len(self.correlation_id) >= 8 else self.correlation_id
            parts.append(f"corr={short_corr}")

        if self.bench:
            parts.append(f"bench={self.bench}")
        if self.operation:
            parts.append(f"op={self.operation}")
        if self.component:
            parts.append(f"component={self.component}")

        for key, value in self.extra.items():
            if value is not None:  # Skip None values
                parts.append(f"{key}={value}")

        if parts:
            return "[" + "] [".join(parts) + "]"
        return ""

    def to_dict(self) -> dict[str, Any]:
        """
        Convert context to dictionary for structured logging.

        Includes all context fields (even if None) plus any extra fields.
        Useful for JSON logging or structured log systems.

        Returns:
            Dictionary with all context values

        Example:
            >>> ctx = LoggerContext(bench="mybench", operation="create", correlation_id="550e8400-...")
            >>> ctx.to_dict()
            {'bench': 'mybench', 'operation': 'create', 'component': None, 'correlation_id': '550e8400-...'}
        """
        return {
            "bench": self.bench,
            "operation": self.operation,
            "component": self.component,
            "correlation_id": self.correlation_id,
            **self.extra,
        }

    def __bool__(self) -> bool:
        """
        Check if context has any values set.

        Returns True if any field (bench, operation, component, correlation_id, or extra) is non-None.

        Returns:
            True if context has values, False if empty

        Example:
            >>> bool(LoggerContext())
            False
            >>> bool(LoggerContext(bench="mybench"))
            True
            >>> bool(LoggerContext(correlation_id="550e8400-..."))
            True
        """
        return any(
            [
                self.bench is not None,
                self.operation is not None,
                self.component is not None,
                self.correlation_id is not None,
                bool(self.extra),
            ],
        )
