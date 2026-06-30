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

# ── Linting ──────────────────────────────────────────────────────────────────

_py_changed := "git diff --name-only HEAD -- '*.py' && git ls-files --others --exclude-standard -- '*.py'"

# Run Ruff linter on changed files only
lint:
    #!/usr/bin/env bash
    files=$(
        (git diff --name-only HEAD -- '*.py'
         git ls-files --others --exclude-standard -- '*.py') \
        | sort -u | xargs
    )
    if [ -n "$files" ]; then
        uv run ruff check $files
    else
        echo "No changed Python files to lint"
    fi

# Run Ruff linter on tests only
lint-tests:
    uv run ruff check tests/

# Run Ruff linter + format check (CI-style, full repo)
lint-all:
    uv run ruff check frappe_manager/ tests/ && uv run ruff format --check .

# Auto-fix fixable lint issues on changed files only
lint-fix:
    #!/usr/bin/env bash
    files=$(
        (git diff --name-only HEAD -- '*.py'
         git ls-files --others --exclude-standard -- '*.py') \
        | sort -u | xargs
    )
    if [ -n "$files" ]; then
        uv run ruff check --fix $files
    else
        echo "No changed Python files to fix"
    fi

# ── Docs generation ──────────────────────────────────────────────────────────

# Generate command reference docs from the live CLI
docs-gen:
    uv run python scripts/update_cli_docs.py

# Serve versioned docs locally via mike (shows version selector)
docs port="8000":
    mike serve -F zensical.toml -a 127.0.0.1:{{port}}

# ── Docs styles ───────────────────────────────────────────────────────────────

_scss := "docs/stylesheets/extra.scss"
_css  := "docs/stylesheets/extra.css"

# Compile SCSS → CSS (one-shot)
css:
    bunx sass {{_scss}} {{_css}} --style=compressed --no-source-map

# Watch SCSS and recompile on change
css-watch:
    bunx sass {{_scss}} {{_css}} --style=compressed --no-source-map --watch

# ── Docs versioning ───────────────────────────────────────────────────────────

# Deploy current docs as a named version (e.g. just docs-deploy dev)
docs-deploy alias="dev": css docs-gen
    #!/usr/bin/env bash
    version=$(python -c "from frappe_manager.__about__ import __version__; print(__version__)")
    mike deploy {{alias}} --title "$version" -F zensical.toml

# Deploy and mark as latest (for release tags)
docs-release: css docs-gen
    #!/usr/bin/env bash
    version=$(python -c "from frappe_manager.__about__ import __version__; print(__version__)")
    slug=$(python -c "
    import re
    from frappe_manager.__about__ import __version__
    m = re.match(r'^(\d+\.\d+)', __version__)
    print('v' + m.group(1) if m else __version__)
    ")
    mike deploy --update-aliases "$slug" latest --title "$version" -F zensical.toml

# Set the default version redirect (run once after first deploy)
docs-default version="latest":
    mike set-default --push {{version}} -F zensical.toml

# Full build: compile CSS + generate docs + deploy as dev
docs-build: css docs-gen
    #!/usr/bin/env bash
    version=$(python -c "from frappe_manager.__about__ import __version__; print(__version__)")
    mike deploy dev --title "$version" -F zensical.toml

# ── Migration Testing ──────────────────────────────────────────────────────────
# All defaults are in scripts/migrate-test.sh. Override via env vars:
#   FM_BENCH=mybench just migrate-init
#   FM_REPO=git+https://...@my-branch just migrate-test

_check-server:
    #!/usr/bin/env bash
    if [[ -z "${FM_SERVER:-}" ]]; then
        echo "  ✗ FM_SERVER is not set"
        exit 1
    fi
    if ! ssh -o ConnectTimeout=5 -o BatchMode=yes "$FM_SERVER" "echo ok" 2>/dev/null; then
        echo "  ✗ Server unreachable: $FM_SERVER"
        exit 1
    fi

migrate-init: _check-server
    bash scripts/migrate-test.sh init

migrate-setup VERSION: _check-server
    bash scripts/migrate-test.sh setup {{VERSION}}

migrate-test: _check-server
    bash scripts/migrate-test.sh test

migrate-full: _check-server
    bash scripts/migrate-test.sh full

migrate-status: _check-server
    bash scripts/migrate-test.sh status

migrate-diff: _check-server
    bash scripts/migrate-test.sh diff

migrate-perms: _check-server
    bash scripts/migrate-test.sh perms

migrate-logs: _check-server
    bash scripts/migrate-test.sh logs

migrate-versions: _check-server
    bash scripts/migrate-test.sh versions

migrate-switch VERSION: _check-server
    bash scripts/migrate-test.sh switch {{VERSION}}

migrate-cleanup: _check-server
    bash scripts/migrate-test.sh cleanup
