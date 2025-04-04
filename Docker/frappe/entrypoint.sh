#!/bin/bash

source /scripts/helper-function.sh

cleanup() {
    echo "Received signal SIGTERM, stopping..."
    if [ -n "$running_script_pid" ]; then
        kill -s SIGTERM "$running_script_pid"
    fi
    exit 0
}

trap cleanup SIGTERM

[[ "${USERID:-}" ]] || emer "[ERROR] Please provide USERID environment variable."
[[ "${USERGROUP:-}" ]] || emer "[ERROR] Please provide USERGROUP environment variable."

echo "Setting up user"


# configure uv
# mkdir -p /workspace/.local/share/uv
# rsync -av --ignore-existing --info=progress2 --partial --owner /opt/user/uv/ /workspace/.local/share/uv/

update_uid_gid "${USERID}" "${USERGROUP}" "frappe" "frappe"

if [[ -n "${WORKER_NAME:-}" ]]; then
    SERVICE_NAME="${WORKER_NAME}"
fi

if [[ -n "${WORKER_NAME:-}" || -n "${SERVER_NAME:-}" ]]; then
    SOCK_DIR='/fm-sockets'
    SOCK_SERVICE_PATH="$SOCK_DIR/$SERVICE_NAME.sock"

    echo "Setting supervisord sock directory to $SOCK_SERVICE_PATH"

# REFACTOR: Fix this
# mkdir -p $SOCK_DIR
# chown "$USERID:$USERGROUP" $SOCK_DIR /opt/user /opt/user/conf.d

# rm -rf "$SOCK_SERVICE_PATH"
    mkdir -p /opt/user/conf.d $SOCK_DIR
    chown "$USERID:$USERGROUP" $SOCK_DIR /opt/user /opt/user/conf.d
    rm -rf "$SOCK_SERVICE_PATH"

    sed -i "s/\opt\/user\/supervisor\.sock/fm-sockets\/${SERVICE_NAME}\.sock/g" /opt/user/supervisord.conf
    echo "supervisord configured $?"
fi

if [ "$#" -gt 0 ]; then
    gosu "$USERID":"$USERGROUP" "/scripts/$@" &
    running_script_pid=$!
else
    gosu "${USERID}":"${USERGROUP}" /scripts/user-script.sh &
    running_script_pid=$!
fi

configure_workspace

wait $running_script_pid
