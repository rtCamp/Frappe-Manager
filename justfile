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

# Dead-code radar: vulture (dynamic-dispatch patterns suppressed) + never-called
# bash functions. Advisory -- verify each hit before deleting; TESTS-ONLY means
# only tests keep it alive, so the method AND its tests are delete candidates.
deadcode:
    uv run --with vulture python scripts/deadcode.py

# ── Remote sync ──────────────────────────────────────────────────────────────
# Target host for the fm test server. NEVER defaulted here: a real host baked
# into a committed file is infrastructure leakage. Set it in the environment --
# the gitignored .env (loaded by direnv) is its home:
#   echo 'export FM_REMOTE=user@host' >> .env && direnv allow
# or per-invocation: FM_REMOTE=user@host just sync

_remote     := env_var_or_default("FM_REMOTE", "")
_remote_dir := "fm-src/"
_rsync      := "rsync -azi --delete -e 'ssh -o ControlMaster=auto -o ControlPath=/tmp/fm-sync-%r@%h -o ControlPersist=120' --exclude .git --exclude .venv --exclude .env --exclude .direnv --exclude htmlcov --exclude node_modules --exclude .omp --exclude __pycache__ --exclude .pytest_cache --exclude .ruff_cache"

_require_remote:
    @test -n "{{_remote}}" || { echo "FM_REMOTE is not set (user@host). Put it in .env or pass it inline."; exit 1; }

# Push the working tree to $FM_REMOTE's ~/fm-src (its direnv venv is an
# EDITABLE install, so synced edits take effect there immediately; the uv-tool
# fm on its PATH stays frozen until `uv tool install --force .` is re-run)
sync: _require_remote
    {{_rsync}} ./ {{_remote}}:{{_remote_dir}}

# Continuous sync. Event-driven via fswatch when installed (sub-second push on
# save, idle costs nothing); otherwise a 2s rsync delta poll over a persistent
# ssh control socket. Ctrl-C to stop.
sync-watch: _require_remote sync
    #!/usr/bin/env bash
    push() {
        out=$({{_rsync}} ./ {{_remote}}:{{_remote_dir}} 2>&1)
        if [ -n "$out" ]; then
            echo "[$(date +%H:%M:%S)] synced:"
            echo "$out" | sed 's/^/  /'
        fi
    }
    if command -v fswatch >/dev/null; then
        echo "watching (fswatch) . -> {{_remote}}:{{_remote_dir}}"
        # -o coalesces event bursts (editor atomic saves) into one batch;
        # excluded dirs still emit events, but the resulting rsync is a no-op.
        fswatch -o -l 0.3 -e '\.git' -e '\.venv' -e node_modules -e __pycache__ -e '\.omp' -e htmlcov . \
            | while read -r _; do push; done
    else
        echo "watching (2s rsync poll; brew install fswatch for event-driven) . -> {{_remote}}:{{_remote_dir}}"
        while true; do push; sleep 2; done
    fi

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

# Preview README.md (or any FILE) exactly as GitHub renders it, with live reload.
# Renders through GitHub's own /markdown API using your gh auth, so GFM tables,
# alerts and <div align="center"> look the same here as on the repo page.
# Needs: gh extension install yusukebe/gh-markdown-preview (one-time).
readme-preview file="README.md" port="3939":
    #!/usr/bin/env bash
    if ! gh extension list | grep -q markdown-preview; then
        echo "Installing gh-markdown-preview (one-time)..."
        gh extension install yusukebe/gh-markdown-preview
    fi
    gh markdown-preview {{file}} --port {{port}}

# Regenerate the README hero image from REAL fm output on a live bench.
# Captures over a pty (rich needs a tty for colour), drops spinner frames,
# redacts the two passwords `fm info` prints, then renders with charm freeze.
# `freeze` comes from the use_comma shim in .envrc; no manual install needed.
# Host/bench/checkout come from .env (gitignored) so no real host is ever committed:
# FM_REMOTE, FM_HERO_BENCH, FM_SRC. See .env.example. Override per run:
#   just readme-hero user@host mybench docs/assets/fm-demo.svg
readme-hero host=env_var_or_default("FM_REMOTE", "") bench=env_var_or_default("FM_HERO_BENCH", "") out="docs/assets/fm-demo.svg":
    #!/usr/bin/env bash
    set -euo pipefail
    host="{{host}}"; bench="{{bench}}"; src="${FM_SRC:-~/fm-src}"
    if [ -z "$host" ] || [ -z "$bench" ]; then
        echo "readme-hero needs a host and a bench. Set FM_REMOTE and FM_HERO_BENCH in .env"
        echo "(copy .env.example), or pass them: just readme-hero user@host mybench"
        exit 2
    fi
    command -v freeze >/dev/null || { echo "freeze not on PATH: run 'direnv allow' (needs nix comma), or 'nix run nixpkgs#charm-freeze'"; exit 1; }
    tmp=$(mktemp -d)
    trap 'rm -rf "$tmp"' EXIT
    ssh -tt "$host" "export COLUMNS=100 TERM=xterm-256color; cd $src && ./.venv/bin/fm info $bench" > "$tmp/raw.ansi"
    python3 - "$tmp/raw.ansi" "$tmp/hero.ansi" "$bench" <<'PY'
    import re, sys
    raw = open(sys.argv[1], encoding="utf8", errors="replace").read()
    braille = {chr(c) for c in range(0x2800, 0x2900)}
    lines = []
    for line in raw.split("\n"):
        line = line.rstrip("\r")
        plain = re.sub(r"\x1b\[[0-9;?]*[a-zA-Z]", "", line).strip()
        if not plain or "Working" in plain or "Getting bench info" in plain or "Connection to" in plain:
            continue
        if all(ch in braille or ch.isspace() for ch in plain):
            continue            # spinner frame leftovers
        lines.append(line)
    body = "\n".join(lines)
    # `fm info` prints the site DB password and the admin-tools basic-auth password.
    # Never ship either in a committed image. The ANSI colour codes sit inside these
    # lines, so match on the COLOUR-STRIPPED text, then replace the literal token in
    # the coloured body. The schema name itself is not a secret and stays readable.
    secrets = set()
    for line in body.split("\n"):
        plain = re.sub(r"\x1b\[[0-9;?]*[a-zA-Z]", "", line)
        if re.search(r"^\s*\S*\s*(db|auth)\b", plain):
            for tok in re.findall(r"/\s+(\S{8,})", plain):
                secrets.add(tok)
    for tok in secrets:
        body = body.replace(tok, "\u2022" * 16)
    plain_body = re.sub(r"\x1b\[[0-9;?]*[a-zA-Z]", "", body)
    still_there = sorted(t for t in secrets if t in plain_body)
    if still_there:
        sys.exit(f"REFUSING to render: secret still present after redaction: {still_there}")
    bench = sys.argv[3]
    hero = (f"\x1b[1;32m$\x1b[0m \x1b[1;37mfm create {bench} --apps erpnext\x1b[0m\n"
            "\x1b[2m\u2026bench created, containers up, ERPNext installed\x1b[0m\n\n"
            f"\x1b[1;32m$\x1b[0m \x1b[1;37mfm info {bench}\x1b[0m\n" + body + "\n")
    open(sys.argv[2], "w").write(hero)
    print(f"redacted {len(secrets)} secret(s)")
    PY
    freeze "$tmp/hero.ansi" --language ansi --theme charm --font.family Menlo --font.size 14 \
        --line-height 1.3 --window --border.radius 10 --padding "28,32" --margin 24 \
        --shadow.blur 28 --shadow.y 12 --width 1000 --output {{out}}
    echo "wrote {{out}}"

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

