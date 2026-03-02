#!/bin/bash
# Lightweight entrypoint for quick command execution via exec-command.sh
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

# Source helper functions (needed for update_uid_gid)
source /scripts/helper-function.sh

# Validate required environment variables
[[ "${USERID:-}" ]] || emer "[ERROR] Please provide USERID environment variable."
[[ "${USERGROUP:-}" ]] || emer "[ERROR] Please provide USERGROUP environment variable."

# ONLY update UID/GID if there's a mismatch
current_uid=$(id -u frappe 2>/dev/null || echo "0")
current_gid=$(id -g frappe 2>/dev/null || echo "0")

if [[ "$current_uid" != "$USERID" || "$current_gid" != "$USERGROUP" ]]; then
	echo "UID/GID mismatch detected. Updating frappe user: $current_uid:$current_gid -> $USERID:$USERGROUP"
	update_uid_gid "${USERID}" "${USERGROUP}" "frappe" "frappe"
else
	echo "UID/GID already correct ($USERID:$USERGROUP). Skipping update."
fi

# Execute command as frappe user using gosu
# Note: No supervisor setup, no workspace configuration, no chown operations
exec gosu "${USERID}":"${USERGROUP}" "$@"
