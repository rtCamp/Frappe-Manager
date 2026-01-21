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
    def enforce_output_handler() -> bool:
        """
        Block direct richprint access in favor of OutputHandler.

        When enabled, direct imports of richprint will trigger warnings or errors.

        Environment Variable: FM_ENFORCE_OUTPUT_HANDLER
        Values: "true" to enable, "false" to disable (default: false)

        Returns:
            True if OutputHandler should be enforced
        """
        return os.getenv("FM_ENFORCE_OUTPUT_HANDLER", "false").lower() == "true"

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

    @staticmethod
    def skip_migrations() -> bool:
        """
        Skip migration execution (escape hatch).

        CRITICAL ESCAPE HATCH: If migrations break the CLI, set this to true
        to bypass migration execution and recover CLI functionality.

        Environment Variable: FM_SKIP_MIGRATIONS
        Values: "true" to skip, "false" to run normally (default: false)

        Returns:
            True if migrations should be skipped
        """
        return os.getenv("FM_SKIP_MIGRATIONS", "false").lower() == "true"

    @staticmethod
    def enable_migration_backups() -> bool:
        """
        Enable automatic backups before migrations.

        When enabled, creates backup before running migrations so rollback
        is possible if migrations fail.

        Environment Variable: FM_MIGRATION_BACKUPS
        Values: "true" to enable, "false" to disable (default: true)

        Returns:
            True if migration backups should be created
        """
        return os.getenv("FM_MIGRATION_BACKUPS", "true").lower() == "true"

    @staticmethod
    def log_output_calls() -> bool:
        """
        Log all output handler calls for debugging.

        When enabled, logs every call to output.start(), output.stop(), output.print()
        etc. Useful for debugging output flow and identifying issues.

        Environment Variable: FM_LOG_OUTPUT_CALLS
        Values: "true" to enable, "false" to disable (default: false)

        Returns:
            True if output calls should be logged
        """
        return os.getenv("FM_LOG_OUTPUT_CALLS", "false").lower() == "true"

    @staticmethod
    def get_all_flags() -> dict[str, bool | str]:
        """
        Get current state of all feature flags.

        Useful for diagnostics and debugging.

        Returns:
            Dictionary of all flag names and their current values
        """
        return {
            "use_context_managers": OutputRefactoringFlags.use_context_managers(),
            "enforce_output_handler": OutputRefactoringFlags.enforce_output_handler(),
            "strict_mode": OutputRefactoringFlags.strict_mode(),
            "stream_separation_mode": OutputRefactoringFlags.stream_separation_mode(),
            "skip_migrations": OutputRefactoringFlags.skip_migrations(),
            "enable_migration_backups": OutputRefactoringFlags.enable_migration_backups(),
            "log_output_calls": OutputRefactoringFlags.log_output_calls(),
        }
