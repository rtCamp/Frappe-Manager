#!/bin/bash
set -e

cleanup() {
    echo "Received signal SIGTERM, stopping..."
    if [ -n "$running_script_pid" ]; then
        kill -s SIGTERM "$running_script_pid"
    fi
    exit 0
}

trap cleanup SIGTERM

emer() {
    echo "$@"
    exit 1
}

[[ "${SERVICE_NAME:-}" ]] || emer "[ERROR] Please provide SERVICE_NAME environment variable."

SOCK_DIR='/fm-sockets'
SOCK_SERVICE_PATH="$SOCK_DIR/$SERVICE_NAME.sock"

echo "Setting supervisord sock directory to $SOCK_SERVICE_PATH"

mkdir -p /opt/user/conf.d $SOCK_DIR
chown "$USERID:$USERGROUP" $SOCK_DIR /opt/user/conf.d
rm -rf "$SOCK_SERVICE_PATH"

sed -i "s/\opt\/user\/supervisor\.sock/fm-sockets\/${SERVICE_NAME}\.sock/g" /opt/user/supervisord.conf
echo "supervisord configured $?"

if [[ -n "$BENCH_START_OFF" ]]; then
    tail -f /dev/null
else
    echo "Starting supervisor.."
    supervisord -c /opt/user/supervisord.conf &
    running_script_pid=$!
    wait $running_script_pid
fi
