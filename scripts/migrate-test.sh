#!/usr/bin/env bash
#
# migrate-test.sh — Frappe Manager end-to-end migration testing
#
# Usage: bash scripts/migrate-test.sh <command> [args]
#
# Commands:
#   init            Create migration source state (install prev version, create bench)
#   setup [version] Restore migration source from versioned backup
#   test            Run migration with FM_REPO
#   full            setup (latest) + test (full cycle)
#   status          Show comprehensive migration status
#   diff            Diff between init state and current
#   perms           Check file/directory ownership
#   logs            Last 100 lines of fm.log
#   versions        List all installed FM versions with backup status
#   switch <ver>    Switch fm-active symlink
#   cleanup         Remove temp/transient dirs
#
# Environment variables (set via .env or export):
#   FM_SERVER          SSH target (e.g., frappe@myserver.example.com)
#   FM_BENCH           Bench/site name (e.g., mybench.example.com)
#   FM_HOME            Home on remote (e.g., /home/frappe)
#   FM_UV              Path to uv binary (e.g., /home/frappe/.local/bin/uv)
#   FM_PREVIOUS_PYTHON Python for previous FM (e.g., 3.12)
#   FM_PYTHON          Python for target FM (e.g., 3.13)
#   FM_PREVIOUS_REPO   Previous version spec (e.g., git+https://...@fix/install-deps)
#   FM_REPO            Target version spec (e.g., git+https://...@fix-migrations)
#   FM_MIGRATE_FLAGS   Migration flags (e.g., --all-benches --auto-proceed)
#

set -euo pipefail

# ── Configuration (env vars — must be set via .env or export) ──────────────────

: "${FM_SERVER:?FM_SERVER is not set. Create .env or export it.}"
: "${FM_BENCH:?FM_BENCH is not set. Create .env or export it.}"
: "${FM_HOME:?FM_HOME is not set. Create .env or export it.}"
: "${FM_UV:?FM_UV is not set. Create .env or export it.}"
: "${FM_PREVIOUS_PYTHON:?FM_PREVIOUS_PYTHON is not set. Create .env or export it.}"
: "${FM_PYTHON:?FM_PYTHON is not set. Create .env or export it.}"
: "${FM_PREVIOUS_REPO:?FM_PREVIOUS_REPO is not set. Create .env or export it.}"
: "${FM_REPO:?FM_REPO is not set. Create .env or export it.}"
: "${FM_MIGRATE_FLAGS:?FM_MIGRATE_FLAGS is not set. Create .env or export it.}"

SSH_OPTS="-o ConnectTimeout=5 -o BatchMode=yes"
SSH="ssh $SSH_OPTS $FM_SERVER"
FM_DIR="$FM_HOME/frappe"           # Working FM data directory
FM_TMP="$FM_HOME/.fm-tmp"          # Transient venv for building

# ── Formatting helpers ─────────────────────────────────────────────────────────

BOLD='\033[1m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
CYAN='\033[0;36m'
NC='\033[0m'

info()  { echo -e "  ${CYAN}⋮${NC} $1"; }
ok()    { echo -e "  ${GREEN}✓${NC} $1"; }
warn()  { echo -e "  ${YELLOW}⚠${NC} $1"; }
err()   { echo -e "  ${RED}✗${NC} $1"; }

header() {
    local label="$1"
    echo -e "\n  ${BOLD}╔══════════════════════════════════════════════════════════╗${NC}"
    echo -e "  ${BOLD}║${NC}  $label"
    echo -e "  ${BOLD}╚══════════════════════════════════════════════════════════╝${NC}"
    echo ""
}

# ── SSH helpers ────────────────────────────────────────────────────────────────

check_server() {
    if ! ssh $SSH_OPTS "$FM_SERVER" "echo ok" 2>/dev/null; then
        err "Server unreachable: $FM_SERVER"
        echo "  Check:"
        echo "    • Are you connected to the VPN?"
        echo "    • Is the server hostname correct?"
        echo "    • Try: ssh $FM_SERVER"
        exit 1
    fi
    ok "Server reachable: $FM_SERVER"
}

ssh_cmd() {
    $SSH "$@"
}

# ── Utility functions ──────────────────────────────────────────────────────────

# Extract a human-readable ref from a repo spec
#   frappe-manager==0.18.0          → 0.18.0
#   git+https://...@fix-migrations  → fix-migrations
parse_ref() {
    local repo="$1"
    if [[ "$repo" == *"@"* ]]; then
        echo "${repo##*@}"
    elif [[ "$repo" == *"=="* ]]; then
        echo "${repo##*==}"
    else
        echo "unknown"
    fi
}

# Install FM from repo spec into the shared temp venv.
# Usage: install_fm <repo> <python_version>
# Returns the installed package version on stdout (e.g. "0.19.0").
# All status messages go to stderr.
install_fm() {
    local repo="$1"
    local python_ver="$2"
    local label
    label=$(parse_ref "$repo")

    echo "  ${CYAN}⋮${NC} Installing $repo (Python $python_ver)" >&2

    # Fresh venv every time
    if ! ssh_cmd "rm -rf \"$FM_TMP\" && \"$FM_UV\" venv --python \"$python_ver\" \"$FM_TMP\""; then
        echo "  ${RED}✗${NC} Failed to create venv (is uv installed at $FM_UV?)" >&2
        exit 1
    fi
    echo "  ${GREEN}✓${NC} Venv created at $FM_TMP" >&2

    if ! ssh_cmd "\"$FM_UV\" pip install --python \"$FM_TMP/bin/python\" \"$repo\" --quiet"; then
        echo "  ${RED}✗${NC} pip install failed for: $repo" >&2
        exit 1
    fi
    echo "  ${GREEN}✓${NC} Installed: $label" >&2

    # Query the actual package version (this is the ONLY stdout)
    local version
    version=$(ssh_cmd "$FM_TMP/bin/python -c 'from importlib.metadata import version; print(version(\"frappe-manager\"))'" 2>/dev/null)
    if [[ -z "$version" ]]; then
        echo "  ${RED}✗${NC} Could not determine installed version" >&2
        exit 1
    fi
    echo "$version"
}

# Rename the temp venv into a permanent versioned directory and fix internal paths.
finalize_install() {
    local version="$1"
    local target="$FM_HOME/fm.$version"

    echo "  ${CYAN}⋮${NC} Moving $FM_TMP → $target" >&2
    ssh_cmd "mkdir -p \"$target\""
    # Merge venv contents into target
    ssh_cmd "cp -a \"$FM_TMP/.\" \"$target/\" && rm -rf \"$FM_TMP\""
    # Fix shebangs and venv config so the relocation is transparent
    ssh_cmd "if [ -d \"$target/bin\" ]; then
        for f in \"$target/bin\"/*; do
            [ -f \"\$f\" ] && sed -i \"s|$FM_TMP|$target|g\" \"\$f\" 2>/dev/null || true
        done
    fi"
    ssh_cmd "if [ -f \"$target/pyvenv.cfg\" ]; then
        sed -i \"s|$FM_TMP|$target|g\" \"$target/pyvenv.cfg\"
    fi"
    echo "  ${GREEN}✓${NC} Finalized $target" >&2
}

# ── Command implementations ────────────────────────────────────────────────────

# ─── init ────────────────────────────────────────────────────────────────────────
cmd_init() {
    header "Init — Create migration source state"

    local ref
    ref=$(parse_ref "$FM_PREVIOUS_REPO")
    info "Source ref: $ref"

    # ── Step 0: Clean existing ~/frappe ─────────────────────────────────
    echo ""
    if ssh_cmd "test -d \"$FM_DIR\"" 2>/dev/null; then
        info "Removing existing ~/frappe …"
        ssh_cmd "rm -rf \"$FM_DIR\""
        ok "Cleaned ~/frappe"
    fi

    # ── Step 1-3: Install previous version ────────────────────────────────
    local version
    version=$(install_fm "$FM_PREVIOUS_REPO" "$FM_PREVIOUS_PYTHON")
    ok "Previous version: $version"

    # ── Step 4: Start global services (mariadb, nginx-proxy) ──────────────
    echo ""
    info "Starting global services …"
    ssh_cmd "cd \"$FM_HOME\" && FRAPPE_MANAGER_HOME=\"$FM_DIR\" \"$FM_TMP/bin/fm\" services start all" || {
        err "fm services start failed"
        exit 1
    }
    ok "Global services started (mariadb, nginx-proxy)"

    # ── Step 5: Create bench ──────────────────────────────────────────────
    echo ""
    info "Creating bench: $FM_BENCH …"
    ssh_cmd "cd \"$FM_HOME\" && FRAPPE_MANAGER_HOME=\"$FM_DIR\" \"$FM_TMP/bin/fm\" create \"$FM_BENCH\"" || {
        err "fm create failed"
        exit 1
    }
    ok "Bench created: $FM_BENCH"

    # ── Step 6: Stop all FM containers ────────────────────────────────────
    echo ""
    info "Stopping all FM containers …"
    ssh_cmd "docker ps --filter name=fm_ -q | xargs -r docker stop" || true
    ok "FM containers stopped"

    # ── Step 7: Move ~/frappe → fm.<version>/frappe (migration source) ────
    echo ""
    local target="$FM_HOME/fm.$version"
    info "Moving ~/frappe → $target/frappe"
    ssh_cmd "mkdir -p \"$target\" && mv \"$FM_DIR\" \"$target/frappe\""
    ok "Migration source saved"

    # ── Step 8: Finalize install (temp → fm.<version>) ────────────────────
    echo ""
    finalize_install "$version"

    # ── Step 9: Summary ───────────────────────────────────────────────────
    echo ""
    header "Init Summary"
    echo "  Server:      $FM_SERVER"
    echo "  Version:     $version"
    echo "  Location:    $target"
    echo "  Bench:       $FM_BENCH"
    echo "  FM binary:   $target/bin/fm"
    echo "  Data:        $target/frappe/"
    echo "  SITE_DIR:    $target/frappe/sites/$FM_BENCH/"
    echo ""
    ok "Migration source state created — ready for 'setup' and 'test'"
}

# ─── setup ──────────────────────────────────────────────────────────────────────
cmd_setup() {
    local version="${1:-}"
    header "Setup — Restore migration source"

    # Auto-detect latest versioned backup if none specified
    if [[ -z "$version" ]]; then
        info "No version specified, detecting latest backup …"
        version=$(ssh_cmd "ls -1d \"$FM_HOME/fm.\"* 2>/dev/null | while read d; do
            [ -d \"\$d/frappe\" ] && basename \"\$d\" | sed 's/^fm.//'
        done | sort -V | tail -1")
        if [[ -z "$version" ]]; then
            err "No versioned backups found in $FM_HOME/fm.*/"
            echo "  Run '$0 init' first to create a migration source, or pass a version."
            exit 1
        fi
        info "Detected version: $version"
    fi

    local source="$FM_HOME/fm.$version/frappe"
    if ! ssh_cmd "test -d \"$source\"" 2>/dev/null; then
        err "Backup not found: $source"
        echo "  Run '$0 versions' to see available versions."
        exit 1
    fi

    echo ""
    info "Restoring $source → $FM_DIR"
    ssh_cmd "rm -rf \"$FM_DIR\" && cp -a \"$source\" \"$FM_DIR\""
    ok "Restored fm.$version/frappe → ~/frappe"

    echo ""
    header "Setup Complete"
    echo "  Restored:    fm.$version/frappe"
    echo "  To:          $FM_DIR"
    echo "  Bench:       $FM_BENCH"
    echo "  Sites:       $FM_DIR/sites/$FM_BENCH/"
    echo ""
    ok "Migration source restored — ready for 'test'"
}

# ─── test ───────────────────────────────────────────────────────────────────────
cmd_test() {
    header "Test — Run migration with FM_REPO"

    local ref
    ref=$(parse_ref "$FM_REPO")
    info "Target ref: $ref"

    # Verify we have data to migrate — auto-restore from latest backup if missing
    if ! ssh_cmd "test -d \"$FM_DIR\"" 2>/dev/null; then
        info "~/frappe not found — auto-restoring from earliest backup (migration source)"
        local version
        version=$(ssh_cmd "ls -1d \"$FM_HOME/fm.\"* 2>/dev/null | while read d; do
            [ -d \"\$d/frappe\" ] && basename \"\$d\" | sed 's/^fm.//'
        done | sort -V | head -1")
        if [[ -z "$version" ]]; then
            err "No versioned backups found in $FM_HOME/fm.*/"
            echo "  Run '$0 init' first to create a migration source."
            exit 1
        fi
        local source="$FM_HOME/fm.$version/frappe"
        if ! ssh_cmd "test -d \"$source\"" 2>/dev/null; then
            err "Backup not found: $source"
            echo "  Run '$0 versions' to see available versions."
            exit 1
        fi
        info "Auto-restoring from fm.$version/frappe"
        ssh_cmd "rm -rf \"$FM_DIR\" && cp -a \"$source\" \"$FM_DIR\""
        ok "Auto-restored fm.$version/frappe → ~/frappe"
    else
        ok "Found ~/frappe with existing bench data"
    fi

    # ── Step 1: Install new FM version ──────────────────────────────────
    local version
    version=$(install_fm "$FM_REPO" "$FM_PYTHON")
    ok "New version: $version"

    # ── Step 2: Stop all FM containers ──────────────────────────────────
    echo ""
    info "Stopping all FM containers …"
    ssh_cmd "docker ps --filter name=fm_ -q | xargs -r docker stop" || true
    ok "FM containers stopped"

    # ── Step 3: Finalize install (temp → fm.<version>) ──────────────────
    echo ""
    finalize_install "$version"

    # ── Step 4: Create symlink ──────────────────────────────────────────
    echo ""
    info "Creating symlink fm-active → fm.$version"
    ssh_cmd "ln -sf \"fm.$version\" \"$FM_HOME/fm-active\""
    ok "Symlink: fm-active → fm.$version"

    # ── Step 5: Run migration ───────────────────────────────────────────
    echo ""
    info "Running: fm migrate $FM_MIGRATE_FLAGS"
    echo ""

    # Run migration in foreground with live output streaming
    ssh_cmd "cd \"$FM_DIR\" && FRAPPE_MANAGER_HOME=\"$FM_DIR\" \"$FM_HOME/fm-active/bin/fm\" migrate $FM_MIGRATE_FLAGS 2>&1" \
        || warn "Migration exited with code $?"
    echo ""

    # ── Results ─────────────────────────────────────────────────────────
    header "Migration Results"
    echo "  Previous:    $FM_PREVIOUS_REPO"
    echo "  Target:      $ref"
    echo "  Version:     $version"
    echo "  Location:    $FM_HOME/fm.$version"
    echo "  Symlink:     fm-active → fm.$version"
    echo "  Data:        $FM_DIR/        (migrated)"
    echo ""
    ok "Migration test complete"
}

# ─── full ───────────────────────────────────────────────────────────────────────
cmd_full() {
    header "Full cycle: setup (latest) → test"

    echo "  ├─ Step 1/2: Restore latest migration source …"
    echo ""
    cmd_setup ""

    echo ""
    echo "  └─ Step 2/2: Run migration against FM_REPO …"
    echo ""
    cmd_test

    echo ""
    ok "Full migration cycle complete"
}

# ─── status ─────────────────────────────────────────────────────────────────────
cmd_status() {
    header "Migration Status Report"

    local SITE_DIR="$FM_DIR/sites/$FM_BENCH"
    local DCFG="$SITE_DIR/docker-compose.yml"
    local DCW="$SITE_DIR/docker-compose.workers.yml"
    local BCT="$SITE_DIR/bench_config.toml"

    # ── docker-compose.yml ────────────────────────────────────────────────
    echo "  ── docker-compose.yml ──────────────────────────────────────"
    ssh_cmd "test -f \"$DCFG\" && echo '  File: $DCFG (exists)' || echo '  File: $DCFG (NOT FOUND)'"
    echo ""
    ssh_cmd "test -f \"$DCFG\" && echo '  images:';             grep -n '^    image:' \"$DCFG\" 2>/dev/null | head -5 | sed 's/^/    /'" || true
    ssh_cmd "test -f \"$DCFG\" && echo '  SITE_MAPPINGS:';      grep -n 'SITE_MAPPINGS' \"$DCFG\" 2>/dev/null | head -3 | sed 's/^/    /'" || true
    ssh_cmd "test -f \"$DCFG\" && echo '  restart:';            grep -n 'restart:' \"$DCFG\" 2>/dev/null | head -3 | sed 's/^/    /'" || true
    ssh_cmd "test -f \"$DCFG\" && echo '  x-version:';          grep -n 'x-version' \"$DCFG\" 2>/dev/null | head -5 | sed 's/^/    /'" || true
    ssh_cmd "test -f \"$DCFG\" && echo '  x-version block:';    sed -n '/^x-version:/,/^[a-z]/p' \"$DCFG\" 2>/dev/null | head -10 | sed 's/^/    /'" || true
    ssh_cmd "test -f \"$DCFG\" && echo '  env_file:';           grep 'env_file:' \"$DCFG\" 2>/dev/null | head -3 | sed 's/^/    /'" || true

    # ── docker-compose.workers.yml ────────────────────────────────────────
    echo ""
    echo "  ── docker-compose.workers.yml ───────────────────────────────"
    ssh_cmd "test -f \"$DCW\" && (echo '  File: $DCW (exists)'; grep -n 'image:' \"$DCW\" 2>/dev/null | sed 's/^/    /') || echo '  File: $DCW (NOT FOUND)'"

    # ── Services docker-compose ───────────────────────────────────────────
    local SDIR="$FM_DIR/services"
    local SDCFG="$SDIR/docker-compose.yml"
    echo ""
    echo "  ── services/docker-compose.yml ──────────────────────────────"
    if ssh_cmd "test -f \"$SDCFG\"" 2>/dev/null; then
        echo "  File: $SDCFG (exists)"
        ssh_cmd "grep -n 'image:' \"$SDCFG\" 2>/dev/null | sed 's/^/    /'"
    else
        echo "  File: $SDCFG (NOT FOUND)"
    fi

    # ── bench_config.toml ────────────────────────────────────────────────
    echo ""
    echo "  ── bench_config.toml ────────────────────────────────────────"
    if ssh_cmd "test -f \"$BCT\"" 2>/dev/null; then
        echo "  File: $BCT (exists)"
        ssh_cmd "cat \"$BCT\"" | sed 's/^/    /'
    else
        echo "  File: $BCT (NOT FOUND)"
    fi

    # ── .fnm / .uv ownership ─────────────────────────────────────────────
    echo ""
    echo "  ── .fnm / .uv ownership ─────────────────────────────────────"
    for dir in .fnm .uv; do
        local target_dir="$SITE_DIR/workspace/frappe-bench/$dir"
        ssh_cmd "stat -f '%Sp %u:%g %N' \"$target_dir\" 2>/dev/null || stat -c '%A %U:%G %n' \"$target_dir\" 2>/dev/null || echo '    $dir: not found'"
    done

    # ── Supervisor configs ───────────────────────────────────────────────
    echo ""
    echo "  ── Supervisor config files ──────────────────────────────────"
    ssh_cmd "ls -la \"$SITE_DIR/workspace/frappe-bench/config/\"*.fm.supervisor.conf 2>/dev/null | sed 's/^/    /' || echo '    No bench supervisor configs found'"

    # ── fm-web-server.sh ─────────────────────────────────────────────────
    echo ""
    echo "  ── fm-web-server.sh ─────────────────────────────────────────"
    ssh_cmd "ls -la \"$SITE_DIR/workspace/frappe-bench/config/fm-web-server.sh\" 2>/dev/null | sed 's/^/    /' || echo '    File not found'"

    # ── Custom nginx configs ─────────────────────────────────────────────
    echo ""
    echo "  ── Custom nginx configs ─────────────────────────────────────"
    ssh_cmd "ls -la \"$SITE_DIR/configs/nginx/conf/custom/upload-limit.conf\" 2>/dev/null | sed 's/^/    /' || echo '    upload-limit.conf: not found'"

    # ── FM directory structure ───────────────────────────────────────────
    echo ""
    echo "  ── FM directory structure ───────────────────────────────────"
    ssh_cmd "ls -la \"$FM_DIR/\" 2>/dev/null | sed 's/^/    /' || echo '    FM dir: not found'"

    # ── Versioned backups ────────────────────────────────────────────────
    echo ""
    echo "  ── Versioned backups ────────────────────────────────────────"
    ssh_cmd "for d in \"$FM_HOME/fm.\"*; do
        if [ -d \"\$d/frappe\" ]; then
            echo \"    \$(basename \"\$d\")/frappe  (exists)\"
        fi
    done 2>/dev/null || echo '    (no versioned backups found)'"

    echo ""
    echo "  ─────────────────────────────────────────────────────────────"
    echo ""
}

# ─── diff ───────────────────────────────────────────────────────────────────────
cmd_diff() {
    header "Diff — init state vs current"

    # Find the most recent versioned backup (the init state)
    local prev_dir
    prev_dir=$(ssh_cmd "ls -1d \"$FM_HOME/fm.\"* 2>/dev/null | while read d; do
        [ -d \"\$d/frappe\" ] && echo \"\$d\"
    done | sort -V | tail -1")

    if [[ -z "$prev_dir" ]]; then
        err "No versioned init-state backups found in $FM_HOME/fm.*/"
        echo "  Run '$0 init' first, then '$0 test' to create points to diff."
        exit 1
    fi

    local prev_name
    prev_name=$(basename "$prev_dir")
    local prev_dc="$prev_dir/frappe/sites/$FM_BENCH/docker-compose.yml"
    local curr_dc="$FM_DIR/sites/$FM_BENCH/docker-compose.yml"

    echo "  Previous:  $FM_SERVER:$prev_dc  ($prev_name)"
    echo "  Current:   $FM_SERVER:$curr_dc"
    echo ""

    if ! ssh_cmd "test -f \"$prev_dc\"" 2>/dev/null; then
        err "Previous docker-compose.yml not found: $prev_dc"
        exit 1
    fi
    if ! ssh_cmd "test -f \"$curr_dc\"" 2>/dev/null; then
        err "Current docker-compose.yml not found: $curr_dc"
        exit 1
    fi

    # Capture diff exit code (1 = differences found, which is valid)
    local diff_rc=0
    ssh_cmd "diff -u \"$prev_dc\" \"$curr_dc\" 2>/dev/null" || diff_rc=$?
    echo ""

    if [[ "$diff_rc" -eq 0 ]]; then
        ok "docker-compose.yml is identical (no changes)"
    else
        warn "docker-compose.yml differs (diff shown above)"
    fi
}

# ─── perms ──────────────────────────────────────────────────────────────────────
cmd_perms() {
    header "File/Directory Ownership Check"
    echo "  FM path: $FM_DIR"
    echo ""

    local paths=(
        "$FM_DIR"
        "$FM_DIR/fm_config.toml"
        "$FM_DIR/services/docker-compose.yml"
        "$FM_DIR/sites/$FM_BENCH/docker-compose.yml"
        "$FM_DIR/sites/$FM_BENCH/bench_config.toml"
    )

    for path in "${paths[@]}"; do
        ssh_cmd "stat -f '%Sp %u:%g %N' \"$path\" 2>/dev/null || stat -c '%A %U:%G %n' \"$path\" 2>/dev/null || echo '    (not found)'"
    done

    echo ""
    ok "Ownership check complete"
}

# ─── logs ───────────────────────────────────────────────────────────────────────
cmd_logs() {
    header "Last 100 lines of fm.log"
    ssh_cmd "tail -100 \"$FM_DIR/logs/fm.log\" 2>/dev/null || tail -100 \"$FM_DIR/fm.log\" 2>/dev/null || echo '    fm.log not found under $FM_DIR/'" | sed 's/^/    /'
}

# ─── versions ───────────────────────────────────────────────────────────────────
cmd_versions() {
    header "Installed Frappe Manager Versions"
    echo ""

    ssh_cmd "for d in \"$FM_HOME/fm.\"*; do
        if [ -d \"\$d\" ]; then
            ver=\$(basename \"\$d\")
            has_backup='no'
            if [ -d \"\$d/frappe\" ]; then has_backup='yes'; fi
            has_fm_bin='no'
            if [ -x \"\$d/bin/fm\" ]; then has_fm_bin='yes'; fi
            printf '    %-20s  backup: %-3s  fm: %s\n' \"\$ver\" \"\$has_backup\" \"\$has_fm_bin\"
        fi
    done 2>/dev/null || echo '    (no fm.* directories found)'"

    echo ""
    if ssh_cmd "test -L \"$FM_HOME/fm-active\"" 2>/dev/null; then
        local target
        target=$(ssh_cmd "readlink \"$FM_HOME/fm-active\"")
        ok "Active: fm-active → $target"
    else
        warn "No fm-active symlink found"
    fi
}

# ─── switch ─────────────────────────────────────────────────────────────────────
cmd_switch() {
    local version="${1:-}"
    if [[ -z "$version" ]]; then
        err "No version specified"
        echo "  Usage: $0 switch <VERSION>"
        echo "  Run '$0 versions' to see available versions"
        exit 1
    fi
    header "Switch fm-active → fm.$version"

    if ssh_cmd "test -d \"$FM_HOME/fm.$version\"" 2>/dev/null; then
        ssh_cmd "ln -sf \"fm.$version\" \"$FM_HOME/fm-active\""
        ok "Symlink updated: fm-active → fm.$version"
    else
        err "Directory fm.$version not found in $FM_HOME"
        echo "  Available versions:"
        ssh_cmd "ls -1d \"$FM_HOME/fm.\"* 2>/dev/null | sed 's/^/    /'"
        exit 1
    fi
}

# ─── cleanup ────────────────────────────────────────────────────────────────────
cmd_cleanup() {
    header "Cleanup — Remove temporary directories"

    local cleaned=false

    if ssh_cmd "test -d \"$FM_TMP\"" 2>/dev/null; then
        ssh_cmd "rm -rf \"$FM_TMP\""
        ok "Removed temp venv: $FM_TMP"
        cleaned=true
    fi

    # Also clean any stale .fm-tmp-* dirs from aborted runs
    while read line; do
        ok "$line"
        cleaned=true
    done < <(ssh_cmd "for d in \"$FM_HOME/.fm-tmp-\"*; do
        [ -d \"\$d\" ] && rm -rf \"\$d\" && echo \"    Removed: \$d\"
    done 2>/dev/null")

    if ! $cleaned; then
        info "Nothing to clean"
    fi

    echo ""
    ok "Cleanup complete"
}

# ── Convenience: also accept "run" as alias for "test" ──────────────────────────
cmd_run() {
    warn "'run' is deprecated — use 'test' instead"
    cmd_test
}

# ── Main dispatch ──────────────────────────────────────────────────────────────

main() {
    local cmd="${1:-}"
    local arg="${2:-}"

    if [[ -z "$cmd" ]]; then
        echo "Usage: $0 <command> [args]"
        echo ""
        echo "Commands:"
        echo "  init            Create migration source state (install prev, create bench)"
        echo "  setup  [ver]    Restore migration source from versioned backup"
        echo "  test            Run migration with FM_REPO"
        echo "  full            setup (latest) + test (full cycle)"
        echo "  status          Show comprehensive migration status"
        echo "  diff            Diff between init state and current"
        echo "  perms           Check file/directory ownership"
        echo "  logs            Last 100 lines of fm.log"
        echo "  versions        List all installed FM versions"
        echo "  switch <ver>    Switch fm-active symlink"
        echo "  cleanup         Remove temp/transient dirs"
        echo ""
        echo "Environment:"
        echo "  FM_SERVER=$FM_SERVER"
        echo "  FM_BENCH=$FM_BENCH"
        echo "  FM_HOME=$FM_HOME"
        echo "  FM_PREVIOUS_REPO=$FM_PREVIOUS_REPO"
        echo "  FM_REPO=$FM_REPO"
        exit 1
    fi

    # All commands need server connectivity
    check_server

    case "$cmd" in
        init)     cmd_init ;;
        setup)    cmd_setup "$arg" ;;
        test)     cmd_test ;;
        run)      cmd_run ;;
        full)     cmd_full ;;
        status)   cmd_status ;;
        diff)     cmd_diff ;;
        perms)    cmd_perms ;;
        logs)     cmd_logs ;;
        versions) cmd_versions ;;
        switch)   cmd_switch "$arg" ;;
        cleanup)  cmd_cleanup ;;
        *)
            err "Unknown command: $cmd"
            echo "  Run '$0' without arguments for usage."
            exit 1
            ;;
    esac
}

main "$@"
