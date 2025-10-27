#!/bin/bash
source /scripts/helper-function.sh

# Activate NVM to make node available in PATH
if [[ -d "$NVM_DIR" ]]; then
    # Check if NODE_VERSIONS is set
    if [[ -z "${NODE_VERSIONS:-}" ]]; then
        echo "[ERROR] NODE_VERSIONS environment variable is not set" >&2
        exit 1
    fi
    export NODE_VERSION="${NODE_VERSIONS%% *}"
    [ -s "$NVM_DIR/nvm.sh" ] && \. "$NVM_DIR/nvm.sh"
fi

# Activate pyenv to make python available in PATH
if [[ -d "$PYENV_ROOT" ]]; then
    export PATH="$PYENV_ROOT/bin:$PATH"
    eval "$(pyenv init --path)"
fi

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
sed -i "s/\opt\/user\/supervisor\.sock/fm-sockets\/${SERVICE_NAME}\.sock/g" /opt/user/supervisord.conf

echo "supervisord configured $?"

if [ "$#" -gt 0 ]; then
    gosu "$USERID":"$USERGROUP" "/scripts/$@" &
    running_script_pid=$!
else
    gosu "${USERID}":"${USERGROUP}" /scripts/user-script.sh &
    running_script_pid=$!
fi

configure_workspace
wait $running_script_pid
