"""
Unit tests for global output handler singleton.

Tests the globals.py module which provides set/get/has functions
for managing the singleton OutputHandler instance.
"""

import pytest

from frappe_manager.output_manager.base import OutputHandler
from frappe_manager.output_manager.globals import (
    get_global_output_handler,
    has_global_output_handler,
    set_global_output_handler,
)
from frappe_manager.output_manager.rich_output import RichOutputHandler
from frappe_manager.output_manager.silent_output import SilentOutputHandler


@pytest.fixture(autouse=True)
def reset_global_handler():
    """
    Reset global handler before each test.

    Uses module-level manipulation to clear the singleton state.
    """
    import frappe_manager.output_manager.globals as globals_module

    original_handler = globals_module._global_output_handler
    globals_module._global_output_handler = None
    yield
    globals_module._global_output_handler = original_handler


class TestGlobalHandlerInitialization:
    def test_initial_state_no_handler(self):
        assert not has_global_output_handler()

    def test_get_before_set_raises_error(self):
        with pytest.raises(RuntimeError, match="Global output handler not initialized"):
            get_global_output_handler()

    def test_set_and_get_handler(self):
        handler = RichOutputHandler()
        set_global_output_handler(handler)

        assert has_global_output_handler()
        retrieved = get_global_output_handler()
        assert retrieved is handler

    def test_replace_existing_handler(self):
        first_handler = RichOutputHandler()
        second_handler = SilentOutputHandler()

        set_global_output_handler(first_handler)
        assert get_global_output_handler() is first_handler

        set_global_output_handler(second_handler)
        assert get_global_output_handler() is second_handler
        assert get_global_output_handler() is not first_handler


class TestGlobalHandlerSingleton:
    def test_same_instance_returned(self):
        handler = RichOutputHandler()
        set_global_output_handler(handler)

        first_get = get_global_output_handler()
        second_get = get_global_output_handler()

        assert first_get is second_get
        assert first_get is handler

    def test_multiple_set_operations(self):
        handlers = [RichOutputHandler(), SilentOutputHandler(), RichOutputHandler()]

        for i, handler in enumerate(handlers):
            set_global_output_handler(handler)
            retrieved = get_global_output_handler()
            assert retrieved is handler


class TestGlobalHandlerTypeChecking:
    def test_accepts_rich_output_handler(self):
        handler = RichOutputHandler()
        set_global_output_handler(handler)
        assert isinstance(get_global_output_handler(), RichOutputHandler)

    def test_accepts_silent_output_handler(self):
        handler = SilentOutputHandler()
        set_global_output_handler(handler)
        assert isinstance(get_global_output_handler(), SilentOutputHandler)

    def test_accepts_any_output_handler_subclass(self):
        handler = RichOutputHandler()
        set_global_output_handler(handler)
        assert isinstance(get_global_output_handler(), OutputHandler)


class TestGlobalHandlerErrorMessages:
    def test_runtime_error_message_content(self):
        """
        Verify error message guides developer to initialization location.
        """
        with pytest.raises(RuntimeError) as exc_info:
            get_global_output_handler()

        error_msg = str(exc_info.value)
        assert "Global output handler not initialized" in error_msg
        assert "main.py:cli_entrypoint()" in error_msg

    def test_error_raised_immediately(self):
        """
        get_global_output_handler() must fail fast, not return None.
        """
        with pytest.raises(RuntimeError):
            get_global_output_handler()


class TestGlobalHandlerStateChecking:
    def test_has_handler_false_initially(self):
        assert not has_global_output_handler()

    def test_has_handler_true_after_set(self):
        set_global_output_handler(RichOutputHandler())
        assert has_global_output_handler()

    def test_has_handler_true_after_replacement(self):
        set_global_output_handler(RichOutputHandler())
        set_global_output_handler(SilentOutputHandler())
        assert has_global_output_handler()


class TestGlobalHandlerRealUsage:
    def test_simulates_cli_lifecycle(self):
        """
        Simulate main.py initialization -> app_callback upgrade flow.
        """
        # Phase 1: Early initialization (main.py)
        assert not has_global_output_handler()
        basic_handler = RichOutputHandler()
        set_global_output_handler(basic_handler)
        assert has_global_output_handler()

        # Phase 2: Upgrade in app_callback (commands/__init__.py)
        upgraded_handler = SilentOutputHandler()
        set_global_output_handler(upgraded_handler)

        # Verify upgrade worked
        final_handler = get_global_output_handler()
        assert final_handler is upgraded_handler
        assert final_handler is not basic_handler

    def test_simulates_command_execution(self):
        """
        Simulate multiple commands accessing the same global handler.
        """
        handler = RichOutputHandler()
        set_global_output_handler(handler)

        # Simulate multiple commands calling get_global_output_handler()
        for _ in range(10):
            retrieved = get_global_output_handler()
            assert retrieved is handler
