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

[[ "${SERVICE_NAME:-}" ]] || emer "[ERROR] Please provide SERVICE_NAME environment variable."

emer() {
    echo "$@"
    exit 1
}

if [[ -n "$BENCH_START_OFF" ]]; then
    tail -f /dev/null
else
    echo "Starting supervisor.."
    supervisord -c /opt/user/supervisord.conf &
    running_script_pid=$!
    wait $running_script_pid
fi
