"""
Feature flags for gradual output architecture refactoring rollout.

These flags allow incremental migration and provide escape hatches if issues arise in production.
"""

import os
from typing import Literal


class OutputRefactoringFlags:
    """
    Feature flags for output architecture refactoring.

    Control via environment variables to enable gradual rollout and provide
    rollback capability if issues arise.
    """

    @staticmethod
    def use_context_managers() -> bool:
        """
        Enable context manager pattern for spinner lifecycle.

        When enabled, new code uses `with spinner(output, "text"):` pattern
        instead of manual start()/stop() calls.

        Environment Variable: FM_USE_SPINNER_CONTEXT
        Values: "true" to enable, "false" to disable (default: false)

        Returns:
            True if context managers should be used
        """
        return os.getenv("FM_USE_SPINNER_CONTEXT", "false").lower() == "true"

    @staticmethod
    def strict_mode() -> bool:
        """
        Raise errors on deprecated output patterns.

        When enabled, use of deprecated patterns (direct richprint, unprotected start/stop)
        will raise errors instead of just warnings.

        Environment Variable: FM_STRICT_OUTPUT
        Values: "true" to enable, "false" to disable (default: false)

        Returns:
            True if strict mode is enabled
        """
        return os.getenv("FM_STRICT_OUTPUT", "false").lower() == "true"

    @staticmethod
    def stream_separation_mode() -> Literal["legacy", "transition", "strict"]:
        """
        Control stream separation behavior (stdout vs stderr).

        - legacy: All output to stderr (current behavior)
        - transition: Gradual migration, both patterns work
        - strict: Enforce stdout for data, stderr for diagnostics

        Environment Variable: FM_STREAM_SEPARATION
        Values: "legacy" | "transition" | "strict" (default: legacy)

        Returns:
            Current stream separation mode
        """
        mode = os.getenv("FM_STREAM_SEPARATION", "legacy").lower()
        if mode in ("legacy", "transition", "strict"):
            return mode  # type: ignore
        return "legacy"
