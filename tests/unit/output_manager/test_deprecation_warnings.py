"""Tests for deprecation warnings on direct richprint usage."""

import os
import warnings
from unittest.mock import patch

import pytest

from frappe_manager.display_manager.DisplayManager import DisplayManager


class TestDeprecationWarnings:
    """Test deprecation warnings when using direct richprint.start()/stop()."""

    @pytest.fixture(autouse=True)
    def setup(self):
        """Reset warnings filter before each test."""
        warnings.simplefilter("always", DeprecationWarning)
        yield
        warnings.resetwarnings()

    @pytest.fixture
    def display_manager(self):
        """Create DisplayManager instance."""
        return DisplayManager()

    def test_start_warns_when_context_managers_enabled(self, display_manager):
        """start() should emit DeprecationWarning when FM_USE_SPINNER_CONTEXT=true."""
        with patch.dict(os.environ, {"FM_USE_SPINNER_CONTEXT": "true"}):
            with warnings.catch_warnings(record=True) as w:
                warnings.simplefilter("always", DeprecationWarning)
                display_manager.start("Working")
                display_manager.stop()

                # Should have warning
                assert len(w) >= 1
                assert issubclass(w[0].category, DeprecationWarning)
                assert "richprint.start()" in str(w[0].message)
                assert "context managers" in str(w[0].message)

    def test_stop_warns_when_context_managers_enabled(self, display_manager):
        """stop() should emit DeprecationWarning when FM_USE_SPINNER_CONTEXT=true."""
        with patch.dict(os.environ, {"FM_USE_SPINNER_CONTEXT": "true"}):
            with warnings.catch_warnings(record=True) as w:
                warnings.simplefilter("always", DeprecationWarning)
                display_manager.start("Working")
                display_manager.stop()

                # Should have warnings for both start and stop
                assert len(w) >= 2
                stop_warnings = [warning for warning in w if "richprint.stop()" in str(warning.message)]
                assert len(stop_warnings) >= 1
                assert "context managers" in str(stop_warnings[0].message)

    def test_start_no_warning_when_flag_disabled(self, display_manager):
        """start() should NOT warn when FM_USE_SPINNER_CONTEXT=false."""
        with patch.dict(os.environ, {"FM_USE_SPINNER_CONTEXT": "false"}):
            with warnings.catch_warnings(record=True) as w:
                warnings.simplefilter("always", DeprecationWarning)
                display_manager.start("Working")
                display_manager.stop()

                # Should have no DeprecationWarnings
                deprecation_warnings = [warning for warning in w if issubclass(warning.category, DeprecationWarning)]
                assert len(deprecation_warnings) == 0

    def test_stop_no_warning_when_flag_disabled(self, display_manager):
        """stop() should NOT warn when FM_USE_SPINNER_CONTEXT=false."""
        with patch.dict(os.environ, {"FM_USE_SPINNER_CONTEXT": "false"}):
            with warnings.catch_warnings(record=True) as w:
                warnings.simplefilter("always", DeprecationWarning)
                display_manager.start("Working")
                display_manager.stop()

                # Should have no DeprecationWarnings
                deprecation_warnings = [warning for warning in w if issubclass(warning.category, DeprecationWarning)]
                assert len(deprecation_warnings) == 0

    def test_start_raises_in_strict_mode(self, display_manager):
        """start() should raise RuntimeError when FM_STRICT_OUTPUT=true."""
        with patch.dict(os.environ, {"FM_USE_SPINNER_CONTEXT": "true", "FM_STRICT_OUTPUT": "true"}):
            with pytest.raises(RuntimeError, match="Direct richprint.start\\(\\) is not allowed in strict mode"):
                display_manager.start("Working")

    def test_stop_raises_in_strict_mode(self, display_manager):
        """stop() should raise RuntimeError when FM_STRICT_OUTPUT=true."""
        with patch.dict(os.environ, {"FM_USE_SPINNER_CONTEXT": "false", "FM_STRICT_OUTPUT": "false"}):
            # Start without strict mode
            display_manager.start("Working")

        with patch.dict(os.environ, {"FM_USE_SPINNER_CONTEXT": "true", "FM_STRICT_OUTPUT": "true"}):
            # Stop with strict mode should raise
            with pytest.raises(RuntimeError, match="Direct richprint.stop\\(\\) is not allowed in strict mode"):
                display_manager.stop()

    def test_warning_message_includes_migration_guide(self, display_manager):
        """Warning message should reference migration guide."""
        with patch.dict(os.environ, {"FM_USE_SPINNER_CONTEXT": "true"}):
            with warnings.catch_warnings(record=True) as w:
                warnings.simplefilter("always", DeprecationWarning)
                display_manager.start("Working")

                assert len(w) >= 1
                message = str(w[0].message)
                assert "output-migration-guide.md" in message

    def test_warning_includes_correct_usage_example(self, display_manager):
        """Warning message should show correct usage pattern."""
        with patch.dict(os.environ, {"FM_USE_SPINNER_CONTEXT": "true"}):
            with warnings.catch_warnings(record=True) as w:
                warnings.simplefilter("always", DeprecationWarning)
                display_manager.start("Working")

                assert len(w) >= 1
                message = str(w[0].message)
                assert "from frappe_manager.output_manager import spinner" in message
                assert "with spinner(output," in message

    def test_default_behavior_no_warnings(self, display_manager):
        """By default (no env vars), should work without warnings."""
        # Clear all FM_ env vars
        env_backup = os.environ.copy()
        for key in list(os.environ.keys()):
            if key.startswith("FM_"):
                del os.environ[key]

        try:
            with warnings.catch_warnings(record=True) as w:
                warnings.simplefilter("always", DeprecationWarning)
                display_manager.start("Working")
                display_manager.stop()

                # Should have no DeprecationWarnings
                deprecation_warnings = [warning for warning in w if issubclass(warning.category, DeprecationWarning)]
                assert len(deprecation_warnings) == 0
        finally:
            # Restore environment
            os.environ.clear()
            os.environ.update(env_backup)

    def test_warning_stacklevel_points_to_caller(self, display_manager):
        """Warning should point to the caller, not DisplayManager internals."""
        with patch.dict(os.environ, {"FM_USE_SPINNER_CONTEXT": "true"}):
            with warnings.catch_warnings(record=True) as w:
                warnings.simplefilter("always", DeprecationWarning)
                # This line should be reported in the warning
                display_manager.start("Working")  # noqa

                assert len(w) >= 1
                # stacklevel=2 should point to this test file, not DisplayManager.py
                assert "test_deprecation_warnings.py" in w[0].filename
