"""
Integration tests for contextual logging.

This module tests the end-to-end flow of contextual logging through the
CLI commands, verifying that context information (bench name, operation,
component) properly flows through to log files.
"""

import logging

import pytest

from frappe_manager.logger import log
from frappe_manager.logger.context import LoggerContext
from frappe_manager.logger.contextual import ContextualLogger
from frappe_manager.output_manager.logging_output import LoggingOutputHandler
from frappe_manager.output_manager.silent_output import SilentOutputHandler


@pytest.fixture
def temp_log_dir(tmp_path):
    """Create a temporary log directory for testing."""
    log_dir = tmp_path / "logs"
    log_dir.mkdir()

    # Clear logger cache to ensure fresh logger creation
    log.loggers.clear()

    return log_dir


@pytest.fixture
def test_logger(temp_log_dir):
    """Create a real logger instance that writes to a file."""
    logger = logging.getLogger("test_contextual_integration")
    logger.setLevel(logging.DEBUG)
    logger.handlers.clear()

    log_file = temp_log_dir / "test.log"
    handler = logging.FileHandler(log_file)
    handler.setLevel(logging.DEBUG)
    formatter = logging.Formatter("[%(asctime)s] [%(levelname)s] %(message)s", datefmt="%Y-%m-%d %H:%M:%S")
    handler.setFormatter(formatter)
    logger.addHandler(handler)

    return logger, log_file


class TestContextualLoggingEndToEnd:
    """Test end-to-end contextual logging flow."""

    def test_context_flows_from_logger_to_file(self, test_logger):
        """Test that context flows from ContextualLogger to log file."""
        logger, log_file = test_logger

        context = LoggerContext(bench="mybench", operation="create")
        contextual = ContextualLogger(logger, context)

        contextual.info("Starting operation")
        contextual.info("Processing step 1")
        contextual.info("Processing step 2")

        log_contents = log_file.read_text()

        # All messages should have context
        assert log_contents.count("[bench=mybench]") == 3
        assert log_contents.count("[op=create]") == 3
        assert "Starting operation" in log_contents
        assert "Processing step 1" in log_contents
        assert "Processing step 2" in log_contents

    def test_context_with_output_handler_integration(self, test_logger):
        """Test context integration through LoggingOutputHandler."""
        logger, log_file = test_logger

        context = LoggerContext(bench="testbench", operation="delete")
        contextual = ContextualLogger(logger, context)

        # Use LoggingOutputHandler with contextual logger
        silent = SilentOutputHandler()
        output = LoggingOutputHandler(silent, contextual)

        output.print("Deleting bench")
        output.info("Stopping containers")
        output.warning("Removing volumes")

        log_contents = log_file.read_text()

        # All output should have context
        assert "[bench=testbench]" in log_contents
        assert "[op=delete]" in log_contents
        assert "[OUTPUT] Deleting bench" in log_contents
        assert "[OUTPUT] Stopping containers" in log_contents
        assert "[OUTPUT] Removing volumes" in log_contents

    def test_child_context_inheritance_in_workflow(self, test_logger):
        """Test that child contexts inherit properly in a workflow."""
        logger, log_file = test_logger

        # Parent context for overall operation
        parent = ContextualLogger(logger, LoggerContext(bench="mybench", operation="create"))
        parent.info("Starting bench creation")

        # Child context for Docker operations
        docker_logger = parent.child(component="docker")
        docker_logger.info("Building containers")
        docker_logger.info("Starting services")

        # Child context for database operations
        db_logger = parent.child(component="database")
        db_logger.info("Initializing database")
        db_logger.info("Running migrations")

        # Back to parent
        parent.info("Bench creation complete")

        log_contents = log_file.read_text()

        # Verify structure
        lines = [line for line in log_contents.split("\n") if line]

        # First and last should be parent only
        assert "[bench=mybench] [op=create]" in lines[0]
        assert "[component=" not in lines[0]

        # Docker operations should have docker component
        assert "[bench=mybench] [op=create] [component=docker] Building containers" in log_contents
        assert "[bench=mybench] [op=create] [component=docker] Starting services" in log_contents

        # Database operations should have database component
        assert "[bench=mybench] [op=create] [component=database] Initializing database" in log_contents
        assert "[bench=mybench] [op=create] [component=database] Running migrations" in log_contents

    def test_multiple_benches_distinguished_by_context(self, test_logger):
        """Test that operations on different benches are distinguished by context."""
        logger, log_file = test_logger

        # Simulate operations on bench1
        bench1_logger = ContextualLogger(logger, LoggerContext(bench="bench1", operation="start"))
        bench1_logger.info("Starting bench1")
        bench1_logger.info("Bench1 started successfully")

        # Simulate operations on bench2
        bench2_logger = ContextualLogger(logger, LoggerContext(bench="bench2", operation="stop"))
        bench2_logger.info("Stopping bench2")
        bench2_logger.info("Bench2 stopped successfully")

        log_contents = log_file.read_text()

        # Can grep for specific bench
        bench1_lines = [line for line in log_contents.split("\n") if "[bench=bench1]" in line]
        bench2_lines = [line for line in log_contents.split("\n") if "[bench=bench2]" in line]

        assert len(bench1_lines) == 2
        assert len(bench2_lines) == 2

        assert "[op=start]" in bench1_lines[0]
        assert "[op=stop]" in bench2_lines[0]


class TestContextualLoggingWithExtraFields:
    """Test contextual logging with extra custom fields."""

    def test_extra_fields_logged_correctly(self, test_logger):
        """Test that extra context fields are logged."""
        logger, log_file = test_logger

        context = LoggerContext(
            bench="mybench",
            operation="create",
            extra={"user": "admin", "step": 1},
        )
        contextual = ContextualLogger(logger, context)

        contextual.info("Step 1 complete")

        log_contents = log_file.read_text()

        assert "[bench=mybench]" in log_contents
        assert "[op=create]" in log_contents
        assert "[user=admin]" in log_contents
        assert "[step=1]" in log_contents

    def test_child_can_add_extra_fields_to_workflow(self, test_logger):
        """Test that child contexts can add extra fields."""
        logger, log_file = test_logger

        parent = ContextualLogger(logger, LoggerContext(bench="mybench", operation="build"))
        parent.info("Starting build")

        # Child adds step information
        child = parent.child(extra={"step": "compile"})
        child.info("Compiling code")

        # Another child with different step
        child2 = parent.child(extra={"step": "package"})
        child2.info("Creating package")

        log_contents = log_file.read_text()

        assert "[step=compile]" in log_contents
        assert "[step=package]" in log_contents
        assert log_contents.count("[bench=mybench]") == 3  # All messages


class TestContextualLoggingEmptyContext:
    """Test contextual logging with empty or partial contexts."""

    def test_empty_context_no_prefix(self, test_logger):
        """Test that empty context doesn't add prefix to logs."""
        logger, log_file = test_logger

        contextual = ContextualLogger(logger)  # Empty context
        contextual.info("Message without context")

        log_contents = log_file.read_text()

        # Should not have context brackets
        assert "[bench=" not in log_contents
        assert "[op=" not in log_contents
        assert "[component=" not in log_contents
        assert "Message without context" in log_contents

    def test_partial_context_only_includes_set_fields(self, test_logger):
        """Test that only set fields are included in logs."""
        logger, log_file = test_logger

        # Only bench, no operation
        contextual = ContextualLogger(logger, LoggerContext(bench="mybench"))
        contextual.info("Partial context message")

        log_contents = log_file.read_text()

        assert "[bench=mybench]" in log_contents
        assert "[op=" not in log_contents
        assert "[component=" not in log_contents


class TestContextualLoggingDifferentLevels:
    """Test contextual logging at different log levels."""

    def test_all_log_levels_include_context(self, test_logger):
        """Test that context is included at all log levels."""
        logger, log_file = test_logger

        context = LoggerContext(bench="mybench", operation="test")
        contextual = ContextualLogger(logger, context)

        contextual.debug("Debug message")
        contextual.info("Info message")
        contextual.warning("Warning message")
        contextual.error("Error message")

        log_contents = log_file.read_text()

        # All levels should have context
        assert log_contents.count("[bench=mybench]") == 4
        assert log_contents.count("[op=test]") == 4

        # Verify all levels present
        assert "[DEBUG]" in log_contents
        assert "[INFO]" in log_contents
        assert "[WARNING]" in log_contents
        assert "[ERROR]" in log_contents


class TestContextualLoggingComplexWorkflow:
    """Test contextual logging in complex real-world workflows."""

    def test_bench_creation_workflow_with_context(self, test_logger):
        """Test a realistic bench creation workflow with proper context."""
        logger, log_file = test_logger

        # Main operation
        main_logger = ContextualLogger(logger, LoggerContext(
            bench="production-bench",
            operation="create",
        ))

        main_logger.info("Starting bench creation")

        # Validation phase
        validation_logger = main_logger.child(component="validation")
        validation_logger.info("Validating bench name")
        validation_logger.info("Checking prerequisites")

        # Docker phase
        docker_logger = main_logger.child(component="docker")
        docker_logger.info("Creating compose configuration")
        docker_logger.info("Building images")
        docker_logger.info("Starting containers")

        # Database phase
        db_logger = main_logger.child(component="database")
        db_logger.info("Initializing database")
        db_logger.info("Creating tables")
        db_logger.info("Running migrations")

        # Completion
        main_logger.info("Bench created successfully")

        log_contents = log_file.read_text()

        # Verify workflow structure
        lines = [line for line in log_contents.split("\n") if line and "production-bench" in line]

        # Should have proper context throughout
        assert all("[bench=production-bench]" in line for line in lines)
        assert all("[op=create]" in line for line in lines)

        # Components should be in their sections
        validation_lines = [line for line in lines if "[component=validation]" in line]
        docker_lines = [line for line in lines if "[component=docker]" in line]
        db_lines = [line for line in lines if "[component=database]" in line]

        assert len(validation_lines) == 2
        assert len(docker_lines) == 3
        assert len(db_lines) == 3

    def test_parallel_operations_distinguished_by_context(self, test_logger):
        """Test that parallel operations are distinguished by context."""
        logger, log_file = test_logger

        # Simulate two operations happening in parallel
        create_logger = ContextualLogger(logger, LoggerContext(
            bench="new-bench",
            operation="create",
        ))

        delete_logger = ContextualLogger(logger, LoggerContext(
            bench="old-bench",
            operation="delete",
        ))

        # Interleaved operations
        create_logger.info("Creating new bench")
        delete_logger.info("Deleting old bench")
        create_logger.info("Building containers")
        delete_logger.info("Stopping containers")
        create_logger.info("New bench ready")
        delete_logger.info("Old bench removed")

        log_contents = log_file.read_text()

        # Extract lines for each operation
        create_lines = [line for line in log_contents.split("\n") if "[bench=new-bench]" in line]
        delete_lines = [line for line in log_contents.split("\n") if "[bench=old-bench]" in line]

        assert len(create_lines) == 3
        assert len(delete_lines) == 3

        # Verify operations are distinct
        assert all("[op=create]" in line for line in create_lines)
        assert all("[op=delete]" in line for line in delete_lines)


class TestContextualLoggingGreppability:
    """Test that contextual logs are easy to grep and filter."""

    def test_can_grep_by_bench_name(self, test_logger):
        """Test that logs can be filtered by bench name."""
        logger, log_file = test_logger

        # Multiple operations on different benches
        bench1 = ContextualLogger(logger, LoggerContext(bench="bench1", operation="create"))
        bench2 = ContextualLogger(logger, LoggerContext(bench="bench2", operation="start"))
        bench3 = ContextualLogger(logger, LoggerContext(bench="bench3", operation="stop"))

        bench1.info("Creating bench1")
        bench2.info("Starting bench2")
        bench3.info("Stopping bench3")
        bench1.info("Bench1 created")

        log_contents = log_file.read_text()

        # Simulate grep for specific bench
        bench1_lines = [line for line in log_contents.split("\n") if "bench=bench1" in line]
        bench2_lines = [line for line in log_contents.split("\n") if "bench=bench2" in line]
        bench3_lines = [line for line in log_contents.split("\n") if "bench=bench3" in line]

        assert len(bench1_lines) == 2
        assert len(bench2_lines) == 1
        assert len(bench3_lines) == 1

    def test_can_grep_by_operation(self, test_logger):
        """Test that logs can be filtered by operation type."""
        logger, log_file = test_logger

        # Multiple operations
        create1 = ContextualLogger(logger, LoggerContext(bench="bench1", operation="create"))
        create2 = ContextualLogger(logger, LoggerContext(bench="bench2", operation="create"))
        delete1 = ContextualLogger(logger, LoggerContext(bench="bench3", operation="delete"))

        create1.info("Creating bench1")
        create2.info("Creating bench2")
        delete1.info("Deleting bench3")

        log_contents = log_file.read_text()

        # Simulate grep for specific operation
        create_lines = [line for line in log_contents.split("\n") if "op=create" in line]
        delete_lines = [line for line in log_contents.split("\n") if "op=delete" in line]

        assert len(create_lines) == 2
        assert len(delete_lines) == 1

    def test_can_grep_by_component(self, test_logger):
        """Test that logs can be filtered by component."""
        logger, log_file = test_logger

        parent = ContextualLogger(logger, LoggerContext(bench="mybench", operation="create"))

        docker = parent.child(component="docker")
        database = parent.child(component="database")
        nginx = parent.child(component="nginx")

        docker.info("Docker operation 1")
        docker.info("Docker operation 2")
        database.info("Database operation")
        nginx.info("Nginx operation")

        log_contents = log_file.read_text()

        # Simulate grep for specific component
        docker_lines = [line for line in log_contents.split("\n") if "component=docker" in line]
        db_lines = [line for line in log_contents.split("\n") if "component=database" in line]
        nginx_lines = [line for line in log_contents.split("\n") if "component=nginx" in line]

        assert len(docker_lines) == 2
        assert len(db_lines) == 1
        assert len(nginx_lines) == 1
