#!/bin/bash
# Lightweight entrypoint for quick command execution
# ONLY handles UID/GID mismatch - skips supervisor setup and workspace configuration

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

# Set HOME explicitly for numeric UID (gosu would default to /)
# This ensures bashrc's $HOME/.local/bin resolves correctly
export HOME=/workspace

# Execute command using numeric UID:GID via gosu (NO usermod needed!)
# gosu supports numeric UIDs without requiring /etc/passwd entries
# This is INSTANT (~50ms) vs usermod which takes 16+ seconds
exec gosu "${USERID}":"${USERGROUP}" "$@"
