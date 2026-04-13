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

# Generate command reference docs from the live CLI
docs-gen:
    uv run python scripts/update_cli_docs.py

# Serve the documentation site locally (live reload), regenerating command docs first
docs port="8001": docs-gen
    uv run --with zensical zensical serve -f zensical.toml -a 127.0.0.1:{{port}}

# Run fm in interactive mode (for AI agents - enables interactive prompts/selection)
fm *ARGS:
    bash /tmp/fm_interactive {{ARGS}}

# ── Docs styles ───────────────────────────────────────────────────────────────

_scss := "docs/stylesheets/extra.scss"
_css  := "docs/stylesheets/extra.css"

# Compile SCSS → CSS (one-shot)
css:
    bunx sass {{_scss}} {{_css}} --style=compressed --no-source-map

# Watch SCSS and recompile on change
css-watch:
    bunx sass {{_scss}} {{_css}} --style=compressed --no-source-map --watch

# Full docs build: compile CSS then run zensical
docs-build: css docs-gen
    uv run --with zensical zensical build -f zensical.toml
