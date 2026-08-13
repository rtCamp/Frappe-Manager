#!/usr/bin/bash

# Function: chown directory and files
# Parameters:
# - user
# - group
chown_directory_and_files() {
	local user
	user="$1"
	local group
	group="$2"
	local dir
	dir="$3"

	user_not_owned_files=$(find "$dir" ! -user "$user" -type f -exec realpath {} + | sort -u)
	group_not_owned_files=$(find "$dir" ! -group "$group" -type f -exec realpath {} + | sort -u)

	user_not_owned_dirs=$(find "$dir" ! -user "$user" -type d -exec realpath {} + | sort -u)
	group_not_owned_dirs=$(find "$dir" ! -group "$group" -type d -exec realpath {} + | sort -u)

	# Concatenate both lists, sort, and remove duplicates
	not_owned_files=$(echo -e "$user_not_owned_files\n$group_not_owned_files" | sort -u)
	not_owned_dirs=$(echo -e "$user_not_owned_dirs\n$group_not_owned_dirs" | sort -u)

	cpu_cores=$(nproc)

	echo "$not_owned_files" | xargs -P "$cpu_cores" -I{} bash -c "if [ -f {} ]; then chown ${user}:${group} {};fi"

	echo "$not_owned_dirs" | xargs -P "$cpu_cores" -I{} bash -c "if [ -d {} ]; then chown -R ${user}:${group} {};fi"
}

# Function: update_common_site_config
# Description: Updates the common site config file with the provided key-value pair.
# Parameters:
#   - key: The key to be updated in the config file.
#   - value: The value to be assigned to the key in the config file.
#   - is_value_json: Optional parameter. Set to true if the value provided is in JSON format.
update_common_site_config() {
	local key="$1"
	local value="$2"
	local is_value_json="$3" # set to true if any value provided
	local config_file="/workspace/frappe-bench/sites/common_site_config.json"

	# Check if the config file exists
	if [ ! -f "$config_file" ]; then
		echo "Error: Common site config file not found."
		return 1
	fi

	# Update the config file using jq
	if [[ "${is_value_json:-}" ]]; then
		jq -r --arg key "$key" --argjson value "$value" '.[$key] = $value' "$config_file" >"$config_file.tmp" && mv "$config_file.tmp" "$config_file"
	else
		jq -r --arg key "$key" --arg value "$value" '.[$key] = $value' "$config_file" >"$config_file.tmp" && mv "$config_file.tmp" "$config_file"
	fi

	echo "Updated $key => $value"
}

# Function to retrieve a value from the common site config file based on a given key
# Arguments:
#   - key: The key to search for in the common site config file
# Returns:
#   - The value corresponding to the given key, or "null" if the key does not exist
get_common_site_config() {
	local key="$1"
	local config_file="/workspace/frappe-bench/sites/common_site_config.json"

	# Check if the config file exists
	if [ ! -f "$config_file" ]; then
		echo "Error: Common site config file not found."
		return 1
	fi

	# Use jq to extract the value corresponding to the given key
	value=$(jq -r --arg key "$key" '.[$key]' "$config_file")

	# Check if the key exists
	if [ "$value" = "null" ]; then
		echo "null"
	else
		echo "$value"
	fi
}

# Function to install apps
# Parameters:
#   - apps_lists: comma-separated list of apps to install
#   - already_installed_apps: comma-separated list of apps already installed
install_apps() {
	local apps_lists
	local already_installed_apps
	local remove_apps

	apps_lists="$1"
	already_installed_apps="$2"

	keep_prebaked_apps=$(mktemp)

	# get apps_json if not available then default to empty list
	apps_json='[]'

	if [[ "${apps_lists:-}" ]]; then
		apps=$(awk -F ',' '{for (i=1; i<=NF; i++) {print $i}}' <<<"$apps_lists")
		for app in $apps; do

			app_contains_http=0

			if [[ $app == http* ]]; then
				app_contains_http=1
				app="${app//http:/https:}"
				app="${app//https:/https;}"
			fi

			app_name=$(echo "$app" | awk 'BEGIN {FS=":"}; {print $1}')
			branch_name=$(echo "$app" | awk 'BEGIN {FS=":"}; {print $2}')

			if [[ "${app_contains_http}" -gt 0 ]]; then
				app_name="${app_name//https\;/https:}"
			fi

			# check if app prebaked
			ALREADY_PREBAKED=$(grep -cw "$app" <<<"$already_installed_apps" || exit 0)

			if [[ "${ALREADY_PREBAKED}" -gt 0 ]]; then
				echo "${app} already prebaked and installed."
				echo "${app}" >>"$keep_prebaked_apps"
				# apps_json=$(echo "$apps_json" | jq -rc --arg app_name "${app_name}" '.+ [$app_name]')
				continue
			fi

			if [[ "${branch_name:-}" ]]; then
				echo "Installing app $app_name -> $branch_name"
				$BENCH_COMMAND get-app --overwrite --skip-assets --branch "${branch_name}" "${app_name}"
			else
				echo "Installing app $app_name"
				$BENCH_COMMAND get-app --overwrite --skip-assets "${app_name}"
			fi
		done
	else
		echo "No app provided to install."
	fi

	remove_apps=$(awk -F ',' '{for (i=1; i<=NF; i++) {print $i}}' <<<"$already_installed_apps")

	for app in $remove_apps; do
		app_name=$(echo "$app" | awk 'BEGIN {FS=":"}; {print $1}')

		is_app_installed=$(cat "$keep_prebaked_apps" | grep -cw "$app_name" || exit 0)

		if [[ ! "$is_app_installed" -gt 0 ]]; then
			echo "remove app ${app_name}"
			(/usr/local/bin/bench rm --no-backup --force "${app_name}" || exit 0)
			apps_json=$(echo "$apps_json" | jq -rc --arg app_name "${app_name}" 'del(.[] | select(. == $app_name))')
		fi
	done

	# create apps_txt
	local apps_list

	apps_txt=$(mktemp)

	apps_list=$(ls -1 apps || exit 0)

	for app_name in $(echo "$apps_list"); do
		get_app_name "$app_name"
		echo "$APP_NAME" >>"$apps_txt"
	done

	cp "$apps_txt" sites/apps.txt

	# create apps_json
	for app_name in $(cat "$apps_txt" | grep -v 'frappe' || exit 0); do
		apps_json=$(echo "$apps_json" | jq -rc --arg app_name "${app_name}" '.+ [$app_name]')
	done

	update_common_site_config install_apps "$apps_json" 'true'
}

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

# this return the list of apps
# input
# $1 -> app_name respective to apps dir
get_app_name() {
	local app="$1"
	local app_dir
	app_dir="/workspace/frappe-bench/apps/${app}"
	hooks_py_path=$(find "$app_dir" -maxdepth 2 -type f -name hooks.py)

	# Extract the app name from the hooks.py file
	APP_NAME=$(awk -F'"' '/app_name/{print $2}' "$hooks_py_path" || exit 0)

	if ! [[ "${APP_NAME:-}" ]]; then
		# If the app name is not found, use app name from basename of the app dir
		APP_NAME=${app##*/}
	fi
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

function setup_fnm_and_yarn() {
	version="$1"

	[[ "${version:-}" ]] || emer "[ERROR] Please provide fnm version as first argument."

	echo "Installing fnm v${version} system-wide..."
	curl -fsSL https://fnm.vercel.app/install | bash -s -- --install-dir /tmp/fnm-install --skip-shell
	mv /tmp/fnm-install/fnm /usr/local/bin/fnm
	chmod +x /usr/local/bin/fnm
	rm -rf /tmp/fnm-install

	mkdir -p /opt/fnm
	export FNM_DIR=/opt/fnm
	export PATH="/usr/local/bin:$PATH"

	eval "$(fnm env --shell bash)"

	for node_ver in ${NODE_VERSIONS}; do
		echo "Installing Node ${node_ver}..."
		fnm install "${node_ver}" || echo "Failed to install Node ${node_ver}"
	done

	NODE_VERSION="${NODE_VERSIONS%% *}"
	echo "Setting default Node version to ${NODE_VERSION}..."
	fnm default "${NODE_VERSION}"

	eval "$(fnm env --shell bash)"

	# Set FNM_COREPACK_ENABLED to auto-enable corepack for all Node versions
	export FNM_COREPACK_ENABLED=true

	echo "Verifying yarn is available (auto-enabled by FNM_COREPACK_ENABLED)..."
	if yarn --version >/dev/null 2>&1; then
		echo "Yarn is available"
	else
		echo "WARNING: Yarn not available - corepack may have failed"
	fi

	chmod -R 755 /opt/fnm
}

get_pyvenv_version() {
	local venv_cfg="$1"
	local venv_version=""
	if [[ -f "$venv_cfg" ]]; then
		venv_version=$(grep "version_info" "$venv_cfg" | cut -d "=" -f 2 | tr -d ' ')
	fi
	echo "$venv_version"
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
