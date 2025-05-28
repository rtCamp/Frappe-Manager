#!/bin/bash
cleanup() {
    echo "Received signal SIGTERM, stopping..."
    if [ -n "$running_script_pid" ]; then
        kill -s SIGKILL "$running_script_pid"
    fi
    exit 0
}

trap cleanup SIGTERM

bench watch &

running_script_pid=$!
wait $running_script_pid
