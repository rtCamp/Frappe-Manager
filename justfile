# Frappe Manager - Test Commands
# Usage: just <command>

# Default recipe - show available commands
default:
    @just --list

# The default is the WHOLE suite on purpose: it is the only number that answers
# "is the tree green?", and at ~16s there is no reason to run less. Bare `pytest`
# behaves identically (testpaths = ["tests"] in pyproject), so a partial path such
# as `pytest tests/unit` reports a smaller count that is easy to misread as the total.
# Run the whole test suite
test:
    pytest tests/ -q

# Run the whole test suite, verbose (per-test names)
test-verbose:
    pytest tests/ -v

# Coverage is measured over the whole package: scoping it to one subsystem reports a
# flattering number that hides every untested module.
# Run the whole test suite with coverage
test-cov:
    pytest tests/ --cov=frappe_manager --cov-report=html
    @echo "\nCoverage report: htmlcov/index.html"

# ~219 of ~3774 tests. A fast loop while working in that subsystem; NOT a green light.
# Run the SSL manager subset only
test-ssl:
    pytest tests/unit/ssl_manager/ -v

# Run the SSL manager subset with application logs
test-ssl-logs:
    pytest tests/unit/ssl_manager/ -v --show-app-logs

# Run specific test file
test-file FILE:
    pytest {{FILE}} -v

# Run specific test with logs
test-debug FILE:
    pytest {{FILE}} -vv --show-app-logs -s

# fmx is a separate package with its own interpreter range (>=3.10, vs fm's 3.13-only) and its
# own deps (supervisor, redis, rq), none of which are in fm's venv -- fmx cannot even be
# imported from it. So it gets an ephemeral env of its own here rather than coupling the two.
# Not part of `just test` and deliberately not in CI.
#
# VIRTUAL_ENV is cleared so uv builds that env instead of warning about fm's active one.
# --override-ini drops fm's addopts: rootdir discovery finds the repo pyproject.toml, and its
# `--cov=frappe_manager` is an unrecognised argument for a run that is not measuring fm.

# Run fmx's own tests (in-container CLI: SIGUSR1 kill ladder, timeouts, poll intervals)
test-fmx:
    cd Docker/frappe/fmx && VIRTUAL_ENV= uv run --with pytest python -m pytest tests/ -v \
        --override-ini="addopts=" -W "ignore::pytest.PytestConfigWarning"

# Mutation test: break covered lines one at a time and see if any test complains.
# Coverage says a line ran; this says a bug in it would be CAUGHT. Read the SURVIVED
# entries as a to-do list of missing assertions. Takes ~3 minutes for the default 60.
mutate n="60":
    MUT_N={{n}} .venv/bin/python scripts/mutation_test.py

# Re-collect the coverage the mutation sampler draws from, then mutate.
# Use after adding tests, otherwise the sample is drawn from a stale covered surface.
mutate-fresh n="60":
    rm -f "${TMPDIR:-/tmp}/fm-mutation-cov.json"
    MUT_N={{n}} .venv/bin/python scripts/mutation_test.py

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

# Run Ruff linter + format check + docs checks + shell checks (CI-style, full repo).
# All of them always run: `&&` would hide the later checks behind pre-existing ruff debt.
lint-all:
    #!/usr/bin/env bash
    rc=0
    uv run ruff check frappe_manager/ tests/ || rc=1
    uv run ruff format --check . || rc=1
    uv run python scripts/docslint.py || rc=1
    uv run pytest tests/unit/scripts/test_shell_lint.py -q --no-cov || rc=1
    exit $rc

# Shellcheck scripts/ and the bash embedded in action.yml, which actionlint cannot reach
shell-lint:
    uv run pytest tests/unit/scripts/test_shell_lint.py -v --no-cov

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

# Check GitHub Actions versions against latest releases (requires gh CLI)
check-actions:
    #!/usr/bin/env bash
    echo "Checking GitHub Actions versions..."
    echo ""
    cache_dir=$(mktemp -d)
    trap 'rm -rf "$cache_dir"' EXIT
    outdated=0
    while IFS='|' read -r filepath action; do
        file=$(basename "$filepath")
        repo=$(echo "$action" | cut -d@ -f1)
        current=$(echo "$action" | cut -d@ -f2)
        cache_key=$(echo "$repo" | tr '/' '_')
        if [ ! -f "$cache_dir/$cache_key" ]; then
            gh api "repos/$repo/releases/latest" --jq '.tag_name' 2>/dev/null \
                > "$cache_dir/$cache_key" || echo "unknown" > "$cache_dir/$cache_key"
        fi
        latest=$(cat "$cache_dir/$cache_key")
        if [ "$latest" = "unknown" ]; then
            printf "  %-10s %-45s %s\n" "unknown" "$repo ($current)" "[$file]"
            continue
        fi
        major_current=$(echo "$current" | grep -oE 'v[0-9]+')
        major_latest=$(echo "$latest" | grep -oE 'v[0-9]+')
        if [ "$major_current" != "$major_latest" ]; then
            printf "  %-10s %-45s %s\n" "OUTDATED" "$repo  ($current → $latest)" "[$file]"
            outdated=$((outdated + 1))
        else
            printf "  %-10s %-45s %s\n" "ok" "$repo  ($current)" "[$file]"
        fi
    done < <(
        grep -rn "uses:" .github/workflows/*.yml | grep -E '@v[0-9]+' \
        | while IFS= read -r line; do
            filepath=$(echo "$line" | cut -d: -f1)
            action=$(echo "$line" | grep -oE '[a-zA-Z0-9_.-]+/[a-zA-Z0-9_.-]+@v[0-9]+(\.[0-9]+)*')
            [ -n "$action" ] && echo "$filepath|$action"
          done | sort -u
    )
    echo ""
    if [ "$outdated" -gt 0 ]; then
        echo "$outdated action(s) outdated."
        exit 1
    else
        echo "All actions up to date."
    fi

# ── Docs generation ──────────────────────────────────────────────────────────

# Generate command reference docs from the live CLI
docs-gen:
    uv run python scripts/update_cli_docs.py

# Generate the annotated example configs from the pydantic models
config-example:
    uv run python scripts/gen_config_example.py

# Check docs against the live CLI: dash style, link hygiene, flags that no longer exist
docs-lint:
    uv run python scripts/docslint.py

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

# ── Dependabot PR Management (delegates to Justfile_depends) ──────────────────
depends REPO="auto":
    just -f scripts/justfile.depends depends {{REPO}}

depends-merge PR REPO="auto":
    just -f scripts/justfile.depends depends-merge {{PR}} {{REPO}}

depends-recreate PR REPO="auto":
    just -f scripts/justfile.depends depends-recreate {{PR}} {{REPO}}

