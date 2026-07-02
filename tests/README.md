# Frappe Manager - Testing Guide

This document provides guidance for writing and running tests in the Frappe Manager project.

## Quick Start

```bash
# Run all tests
pytest tests/

# Run specific test module
pytest tests/unit/output_manager/

# Run with coverage
pytest tests/ --cov=frappe_manager --cov-report=html

# Run specific test file
pytest tests/unit/cli/test_log_level_flags.py -v

# Run specific test
pytest tests/unit/cli/test_log_level_flags.py::TestLogLevelFlagParsing::test_no_flags_defaults_to_warning -v
```

## Test Structure

```
tests/
├── unit/                           # Unit tests for all modules
│   ├── conftest.py                # Global fixtures (auto-initialized)
│   ├── cli/                       # CLI command tests
│   ├── output_manager/            # Output handler tests
│   │   └── conftest.py           # Output manager fixtures
│   ├── logger/                    # Logger tests
│   ├── migration_manager/         # Migration tests
│   │   └── conftest.py           # Migration fixtures
│   ├── docker/                    # Docker integration tests
│   ├── site_manager/              # Bench/site tests
│   └── ssl_manager/               # SSL certificate tests
│       └── conftest.py           # SSL manager fixtures
└── integration/                   # Integration tests (future)
```

## Global Output Handler (IMPORTANT)

**All tests automatically have access to an initialized global output handler.**

### Why This Matters

Frappe Manager uses a global singleton output handler pattern to prevent concurrent spinner conflicts. Any code that calls `get_global_output_handler()` expects it to be initialized.

### Automatic Initialization

The `tests/unit/conftest.py` provides an **autouse fixture** that initializes the global handler before every test:

```python
@pytest.fixture(autouse=True)
def init_global_output_handler():
    """Auto-initialize global handler for all tests."""
    handler = RichOutputHandler()
    set_global_output_handler(handler)
    yield
    set_global_output_handler(None)  # Reset for isolation
```

### Using Global Handler in Tests

Most tests don't need to do anything special - the handler is already initialized:

```python
def test_my_command():
    """Test that uses commands/__init__.py:app_callback()."""
    ctx = MagicMock(spec=typer.Context)
    ctx.obj = {}
    
    # app_callback() calls get_global_output_handler() internally
    # No setup needed - fixture handles it
    app_callback(ctx, verbose=0, log_level=None, version=None)
    
    assert ctx.obj["log_level"] == "WARNING"
```

### Custom Output Handler for Tests

If you need a specific handler behavior:

```python
from unittest.mock import MagicMock
from frappe_manager.output_manager import set_global_output_handler

def test_with_custom_handler():
    """Override the default handler for specific behavior."""
    mock_handler = MagicMock()
    set_global_output_handler(mock_handler)
    
    # Your test code here
    output = get_global_output_handler()
    output.print("test")
    
    mock_handler.print.assert_called_once_with("test")
    
    # Note: autouse fixture will reset handler after test
```

### Testing Output Messages

To verify output was called correctly:

```python
from unittest.mock import patch, MagicMock

def test_error_message():
    """Test that error messages are displayed."""
    from frappe_manager import output_manager
    
    mock_handler = MagicMock()
    with patch.object(output_manager, 'get_global_output_handler', return_value=mock_handler):
        # Code that should output error
        my_function_that_errors()
        
        # Verify error was called
        mock_handler.error.assert_called_once()
        error_msg = mock_handler.error.call_args[0][0]
        assert "expected error text" in error_msg.lower()
```

## Writing Tests

### Test Class Structure

```python
class TestMyFeature:
    """Tests for my feature."""
    
    def test_basic_functionality(self):
        """Test basic case."""
        result = my_function()
        assert result == expected
    
    def test_edge_case(self):
        """Test edge case."""
        with pytest.raises(ValueError):
            my_function(invalid_input)
```

### Mocking Best Practices

**Mock external dependencies, not internals:**

```python
# GOOD: Mock external Docker calls
with patch("frappe_manager.docker.docker_client.DockerClient"):
    bench = Bench(path)
    bench.start()

# AVOID: Over-mocking internal logic
with patch("frappe_manager.site_manager.site.Bench._internal_method"):
    # This makes tests brittle
```

**Use appropriate mock levels:**

```python
# For CLI tests that need many mocks
with patch("frappe_manager.commands.CLI_DIR") as mock_dir:
    with patch("frappe_manager.commands.DockerClient"):
        with patch("frappe_manager.commands.ServicesManager"):
            # Test code
            pass

# Better: Use fixtures for common mock combinations
@pytest.fixture
def mock_cli_environment(mocker):
    """Mock common CLI dependencies."""
    mocker.patch("frappe_manager.commands.CLI_DIR")
    mocker.patch("frappe_manager.commands.DockerClient")
    mocker.patch("frappe_manager.commands.ServicesManager")
```

### Testing Spinners and Output

**Testing with spinners:**

```python
def test_with_spinner():
    """Test code that uses spinner context manager."""
    # Mock the spinner to avoid Rich rendering in tests
    with patch("frappe_manager.commands.spinner"):
        result = my_command_with_spinner()
        assert result == expected
```

**Verifying spinner text:**

```python
from frappe_manager.output_manager import spinner

def test_spinner_text():
    """Verify spinner shows correct text."""
    output = get_global_output_handler()
    
    with patch.object(output, 'spinner') as mock_spinner:
        my_function_with_spinner()
        
        # Check spinner was called with correct text
        mock_spinner.assert_called_once()
        assert "Processing..." in str(mock_spinner.call_args)
```

## Module-Specific Fixtures

Some test directories have their own `conftest.py` with specialized fixtures:

### Output Manager (`tests/unit/output_manager/conftest.py`)

```python
@pytest.fixture
def mock_richprint(mocker):
    """Mock the richprint singleton for testing RichOutputHandler."""
    # Returns a mocked DisplayManager instance
```

### Migration Manager (`tests/unit/migration_manager/conftest.py`)

Provides fixtures for:
- Mock bench configurations
- Mock Docker clients
- Mock services managers
- Test migration scenarios

### SSL Manager (`tests/unit/ssl_manager/conftest.py`)

Provides fixtures for:
- Mock SSL certificates
- Mock nginx configurations
- Mock acme.sh interactions

## Common Test Patterns

### Testing CLI Commands

```python
from typer.testing import CliRunner
from frappe_manager.commands import app

runner = CliRunner()

def test_cli_command():
    """Test CLI command output."""
    result = runner.invoke(app, ["list"])
    assert result.exit_code == 0
    assert "No benches found" in result.stdout
```

### Testing Context Managers

```python
def test_context_manager():
    """Test spinner context manager."""
    output = get_global_output_handler()
    
    with output.spinner(text="Working"):
        # Spinner should be active here
        pass
    
    # Spinner should be stopped after exiting
```

### Testing Exception Handling

```python
def test_exception_handling():
    """Test that exceptions are handled correctly."""
    with pytest.raises(FrappeManagerException) as exc_info:
        my_function_that_raises()
    
    assert "expected error message" in str(exc_info.value)
```

## Pytest Configuration

Project pytest settings in `pyproject.toml`:

```toml
[tool.pytest.ini_options]
testpaths = ["tests"]
python_files = ["test_*.py"]
python_classes = ["Test*"]
python_functions = ["test_*"]
addopts = [
    "--strict-markers",
    "--tb=short",
    "--cov-report=term-missing",
]
```

## Environment Variables

Some tests respect environment variables:

```bash
# Run in CI mode (non-interactive)
CI=true pytest tests/

# Set log level for debugging test failures
PYTEST_LOG_LEVEL=DEBUG pytest tests/
```

## Troubleshooting

### Test fails with "RuntimeError: Global output handler not initialized"

**Cause**: Test bypasses the autouse fixture or runs before fixture executes.

**Solution**: Ensure test is in `tests/unit/` and imports are correct:

```python
# Your test file
from frappe_manager.output_manager import get_global_output_handler

def test_something():
    # This works - fixture auto-initializes handler
    output = get_global_output_handler()
    output.print("test")
```

### Mock conflicts with global handler

**Cause**: Trying to patch `get_global_output_handler` conflicts with autouse fixture.

**Solution**: Mock the handler itself, not the getter:

```python
# WRONG: Conflicts with autouse fixture
with patch("frappe_manager.output_manager.get_global_output_handler"):
    # This won't work as expected
    pass

# RIGHT: Mock the handler instance
from frappe_manager import output_manager
mock_handler = MagicMock()
with patch.object(output_manager, 'get_global_output_handler', return_value=mock_handler):
    # This works
    pass
```

### Tests pass individually but fail in suite

**Cause**: Shared state between tests (e.g., global handler not reset).

**Solution**: The autouse fixture handles cleanup. If issue persists, add explicit cleanup:

```python
def teardown_function():
    """Clean up after each test."""
    set_global_output_handler(None)
```

## Coverage

Generate coverage report:

```bash
# Run tests with coverage
pytest tests/ --cov=frappe_manager --cov-report=html --cov-report=term

# Open HTML report
open htmlcov/index.html
```

Current coverage targets:
- **output_manager**: 100% (147/147 tests)
- **logger**: 100% (86/86 tests)
- **CLI**: 100% (16/16 tests)
- **Overall**: 98.3% (412/419 tests pass)

## Continuous Integration

Tests run automatically on:
- Pull requests
- Push to main/develop branches
- Manual workflow triggers

GitHub Actions workflows:
- `.github/workflows/e2e-site.yaml` - E2E site tests
- `.github/workflows/e2e-migration.yml` - Migration tests
- Unit tests run as part of main CI pipeline

## Additional Resources

- [pytest documentation](https://docs.pytest.org/)
- [unittest.mock guide](https://docs.python.org/3/library/unittest.mock.html)
- [Project AGENTS.md](../AGENTS.md) - Architecture overview
- [Output migration guide](.plans/output-migration-guide.md) - Output handler patterns

## Contributing

When adding tests:

1. **Follow existing patterns** - Check similar tests for structure
2. **Use fixtures** - Don't reinvent common setup
3. **Test edge cases** - Not just happy paths
4. **Keep tests focused** - One concept per test
5. **Document complex mocks** - Help future maintainers understand why

When tests fail:

1. **Read the error** - Error messages are usually clear
2. **Check fixtures** - Verify autouse fixtures are working
3. **Isolate the failure** - Run just that test with `-v --tb=long`
4. **Check mocks** - Ensure mocks match actual code structure
5. **Ask for help** - Open an issue if stuck
