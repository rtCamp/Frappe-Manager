#!/bin/bash

export FNM_DIR=/workspace/.fnm
export FNM_NODE_DIST_MIRROR=https://nodejs.org/dist
export FNM_MULTISHELL_PATH=/workspace/.fnm

if [ -d "/workspace/.uv/python-default/bin" ]; then
	export PATH="/workspace/.uv/python-default/bin:/workspace/.fnm/aliases/default/bin:/usr/local/bin:/opt/user/.bin:${PATH}"
else
	export PATH="/workspace/.fnm/aliases/default/bin:/usr/local/bin:/opt/user/.bin:${PATH}"
fi

source /scripts/helper-function.sh

cleanup() {
	echo "Received signal SIGTERM, stopping..."
	if [ -n "$running_script_pid" ]; then
		kill -s SIGTERM "$running_script_pid"
	fi
	exit 0
}

trap cleanup SIGTERM

if [[ -n "${WORKER_NAME:-}" ]]; then
	SERVICE_NAME="${WORKER_NAME}"
fi

[[ "${USERID:-}" ]] || emer "[ERROR] Please provide USERID environment variable."
[[ "${USERGROUP:-}" ]] || emer "[ERROR] Please provide USERGROUP environment variable."
[[ "${SERVICE_NAME:-}" ]] || emer "[ERROR] Please provide SERVICE_NAME environment variable."

echo "Setting up user"

update_uid_gid "${USERID}" "${USERGROUP}" "frappe" "frappe"

SOCK_DIR='/fm-sockets'
SOCK_SERVICE_PATH="$SOCK_DIR/$SERVICE_NAME.sock"

echo "Setting supervisord sock directory to $SOCK_SERVICE_PATH"

mkdir -p $SOCK_DIR
chown "$USERID:$USERGROUP" $SOCK_DIR /opt/user /opt/user/conf.d

rm -rf "$SOCK_SERVICE_PATH"

sed -i "s|/opt/user/supervisor\.sock|${SOCK_SERVICE_PATH}|g" /opt/user/supervisord.conf
echo "supervisord configured $?"

if [ "$#" -gt 0 ]; then
	script_path="/scripts/$1"
	shift
	gosu "$USERID":"$USERGROUP" "$script_path" "$@" &
	running_script_pid=$!
else
	gosu "${USERID}":"${USERGROUP}" /scripts/user-script.sh &
	running_script_pid=$!
fi

configure_workspace

wait $running_script_pid
