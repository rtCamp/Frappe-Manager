#!/bin/bash

set -e

cleanup() {
    echo "Received signal, performing cleanup..."
    # Add any necessary cleanup commands here

    # Forward the signal to supervisord (if it's running)
    if [ -n "$supervisord_pid" ]; then
        kill -s SIGTERM "$supervisord_pid"
    fi
    exit 0
}

# Trap SIGQUIT, SIGTERM, SIGINT
trap cleanup SIGQUIT SIGTERM

emer() {
    echo "$@"
    exit 1
}

# fi
if [[ -n "$BENCH_START_OFF" ]]; then
    tail -f /dev/null
else
    echo "Starting supervisor.."
    supervisord -c /opt/user/supervisord.conf &
    supervisord_pid=$!
    wait $supervisord_pid
fi
