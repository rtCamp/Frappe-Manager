#!/bin/bash
# Lightweight entrypoint for quick command execution
# ONLY handles UID/GID mismatch - skips supervisor setup and workspace configuration
#
# PERFORMANCE: Eliminates 16+ second usermod/groupmod delay on every 'docker compose run'
# - entrypoint.sh: Full setup with usermod + chown (slow, but needed for long-running services)
# - exec-entrypoint.sh: Direct numeric UID execution (ultra-fast, perfect for one-off commands)
#
# FILE PERMISSIONS: Cache directories are pre-created with 777 at build time (Dockerfile)
# - /workspace/.cache/ writable by any UID (npm, yarn/pnpm via Corepack, pip, uv)
# - USER=frappe env var fixes Python's getpass.getuser() without /etc/passwd entry
# - No runtime chown needed = instant execution

set -e

# Set up environment paths (same as entrypoint.sh)
export FNM_DIR=/workspace/.fnm
export FNM_NODE_DIST_MIRROR=https://nodejs.org/dist
export FNM_MULTISHELL_PATH=/workspace/.fnm

if [ -d "/workspace/.uv/python-default/bin" ]; then
	export PATH="/workspace/.uv/python-default/bin:/workspace/.fnm/aliases/default/bin:/usr/local/bin:/opt/user/.bin:${PATH}"
else
	export PATH="/workspace/.fnm/aliases/default/bin:/usr/local/bin:/opt/user/.bin:${PATH}"
fi

# Validate required environment variables
[[ "${USERID:-}" ]] || { echo "[ERROR] Please provide USERID environment variable."; exit 1; }
[[ "${USERGROUP:-}" ]] || { echo "[ERROR] Please provide USERGROUP environment variable."; exit 1; }


# Execute command using numeric UID:GID via gosu (NO usermod needed!)
# gosu supports numeric UIDs without requiring /etc/passwd entries
# Pass all critical environment variables explicitly since gosu may reset them
exec gosu "${USERID}":"${USERGROUP}" env \
    HOME=/workspace \
    USER=frappe \
    GROUP=frappe \
    PATH="${PATH}" \
    FNM_DIR="${FNM_DIR}" \
    FNM_NODE_DIST_MIRROR="${FNM_NODE_DIST_MIRROR}" \
    FNM_MULTISHELL_PATH="${FNM_MULTISHELL_PATH}" \
    UV_PYTHON_INSTALL_DIR=/workspace/.uv/python \
    UV_CACHE_DIR=/workspace/.uv/cache \
    BENCH_USE_UV=true \
    PYTHONUNBUFFERED=1 \
    LC_ALL=en_US.UTF-8 \
    LANG=en_US.UTF-8 \
    LANGUAGE=en_US.UTF-8 \
    "$@"
