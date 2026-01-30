# Frappe Manager - Test Commands
# Usage: just <command>

# Default recipe - show available commands
default:
    @just --list

# Run SSL manager tests (clean output)
test:
    pytest tests/unit/ssl_manager/ -v

# Run SSL manager tests with application logs
test-logs:
    pytest tests/unit/ssl_manager/ -v --show-app-logs

# Run SSL manager tests (quick summary)
test-quick:
    pytest tests/unit/ssl_manager/ -q

# Run SSL manager tests with coverage
test-cov:
    pytest tests/unit/ssl_manager/ --cov=frappe_manager/ssl_manager --cov-report=html
    @echo "\nCoverage report: htmlcov/index.html"

# Run all tests in the repository
test-all:
    pytest tests/ -v

# Run specific test file
test-file FILE:
    pytest {{FILE}} -v

# Run specific test with logs
test-debug FILE:
    pytest {{FILE}} -vv --show-app-logs -s
