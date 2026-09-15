#!/usr/bin/bash

# Function: update_uid_gid
# Description: Points the container's service account at the HOST's numeric uid/gid, so files
#   written into bind mounts come out owned by the person running fm.
# Parameters:
#   - uid, gid: the host's numeric ids.
#   - username, groupname: the in-image account to repoint (frappe/frappe).
# Returns:
#   - 0 on success, 1 on a usage or validation error.
#
# Only the NUMBER ever lands on disk; the account's name is irrelevant to file ownership. That
# is the whole reason this does not rename or delete anything. The previous implementation
# deleted whichever account already held the target number and then used usermod/groupmod, which
# had three problems:
#   * it destroyed real accounts over a number collision. A macOS host is gid 20, which is
#     `dialout`, so every Mac create logged "Group dialout deleted". A host uid of 33 silently
#     deleted `www-data`.
#   * uid 0 failed outright and silently: `userdel root` reports "user root is currently used by
#     process 1", the failure was ignored, the following usermod also failed, and the container
#     carried on writing files as uid 1000 with nothing reported. fm now refuses to run as root
#     (main.py) so it no longer sends 0 here, but the handling below is uid-agnostic on purpose.
#   * `usermod -u` rewrites ownership of the account's home directory RECURSIVELY, and home is
#     /workspace, i.e. the whole bench. Measured at 8s for 12.7k files. Under the mount runtime
#     /workspace is a bind mount that already carries host ownership, so that pass was re-
#     stamping every file with the ownership it already had.
update_uid_gid() {
	if [ "$#" -ne 4 ]; then
		echo "Usage: update_uid_gid <uid> <gid> <username> <groupname>"
		return 1
	fi

	local uid="$1" gid="$2" username="$3" groupname="$4"

	if [[ ! "$uid" =~ ^[0-9]+$ || ! "$gid" =~ ^[0-9]+$ ]]; then
		echo "Error: UID and GID must be numeric values."
		return 1
	fi

	# Primary group. If a group already holds this gid, use it as it is: the name does not
	# matter and the current holder has done nothing wrong. Only when the number is unclaimed
	# does the service account's own group move onto it, which also keeps `ls -l` showing a
	# name instead of a bare number.
	if ! getent group "$gid" >/dev/null 2>&1; then
		sed -i -E "s|^(${groupname}:x:)[0-9]+:|\1${gid}:|" /etc/group
	fi

	# Numeric identity, rewritten in place. Anchored on [0-9]+ rather than the build-time
	# 1000:1000 so re-running with a different host user updates rather than silently missing.
	sed -i -E "s|^(${username}:x:)[0-9]+:[0-9]+:|\1${uid}:${gid}:|" /etc/passwd

	# A host uid that collides with an image account (root 0, bin 2, www-data 33, nobody 65534)
	# leaves two entries on one number. getpwuid answers with whichever comes first, so the
	# reverse lookup returns the wrong name AND the account loses its supplementary groups
	# (tty, sudo). Putting our line first makes the lookup resolve to us.
	if [ "$(getent passwd "$uid" | head -1 | cut -d: -f1)" != "$username" ]; then
		{
			grep -E "^${username}:" /etc/passwd
			grep -vE "^${username}:" /etc/passwd
		} >/tmp/.passwd.new && cat /tmp/.passwd.new >/etc/passwd && rm -f /tmp/.passwd.new
	fi

	# Hand over the baked tree ONLY when /workspace is not a bind mount, i.e. the image runtime,
	# where the workspace ships inside the image owned by the build uid. Under the mount runtime
	# the host already owns every file here and this would be a no-op costing seconds per start.
	if ! grep -qE '^[^ ]+ /workspace ' /proc/self/mounts 2>/dev/null; then
		chown -R "$uid:$gid" /workspace 2>/dev/null || true
	fi

	echo "UID and GID updated successfully."
}

emer() {
	echo "$1"
	exit 1
}

function fnm_activate_default() {
	export NODE_VERSION="${NODE_VERSIONS%% *}"
	fnm_activate
}

function fnm_activate() {
	export FNM_DIR="$FNM_DIR"
	export PATH="$FNM_DIR:$PATH"
	eval "$(fnm env --shell bash)"
}

function uv_activate_default() {
	export PYTHON_VERSION="${PYTHON_VERSIONS%% *}"
	# Make UV's default Python active by prepending to PATH
	# This ensures 'python3' resolves to UV's Python with headers included
	local uv_python_path="/opt/uv/python/cpython-${PYTHON_VERSION}-linux-$(uname -m)-gnu/bin"
	if [ -d "$uv_python_path" ]; then
		export PATH="${uv_python_path}:${PATH}"
	fi
	# Ensure UV binary is also in PATH
	command -v uv >/dev/null || export PATH="/usr/local/bin:$PATH"
}

# Deprecated: kept for backwards compatibility, now uses fnm
function nvm_activate_default() {
	echo "DEPRECATED: nvm_activate_default is deprecated, use fnm_activate_default instead" >&2
	fnm_activate_default
}

function nvm_activate() {
	echo "DEPRECATED: nvm_activate is deprecated, use fnm_activate instead" >&2
	fnm_activate
}

# Deprecated: kept for backwards compatibility, now uses UV
function pyenv_activate_default() {
	echo "DEPRECATED: pyenv_activate_default is deprecated, use uv_activate_default instead" >&2
	uv_activate_default
}

function pyenv_activate() {
	echo "DEPRECATED: pyenv_activate is deprecated, use uv_activate_default instead" >&2
	uv_activate_default
}

function configure_workspace() {
	start_time=$(date +%s.%N)

	if [[ ! -d "/workspace/frappe-bench/.fnm/node-versions" ]]; then
		echo "Creating /workspace/frappe-bench/.fnm for runtime Node installations..."
		mkdir -p /workspace/frappe-bench/.fnm/node-versions
		chown -R frappe:frappe /workspace/frappe-bench/.fnm
	fi

	# Create .hushlogin to suppress sudo message
	if [[ ! -f "/workspace/.hushlogin" ]]; then
		touch /workspace/.hushlogin
		chown frappe:frappe /workspace/.hushlogin
	fi

	# Removed in favor of specific chown dirs
	# chown -R "$USERID":"$USERGROUP" /opt

	end_time=$(date +%s.%N)
	execution_time=$(awk "BEGIN {print $end_time - $start_time}")

	echo "Time taken for configure_workspace : $execution_time seconds"
}
