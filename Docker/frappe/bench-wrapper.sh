#!/bin/bash
restart_command() {
      exec fm-helper restart "$@"
}

status_command() {
      exec fm-helper status "$@"
}

stop_command() {
      exec fm-helper stop "$@"
}

show_fm_helper_commands() {
    echo -e "\nFrappe Manager Helper Commands (integrated with bench):"
    echo "  status   - Show status of all services"
    echo "  restart  - Restart all services"
    echo "  stop     - Stop all services"
    echo -e "\nThese commands can be executed in two ways:"
    echo "  1. Using bench: bench status/stop/restart"
    echo "  2. Using fm-helper: fm-helper status/stop/restart"
    echo -e "\nFor more details on any command:"
    echo "  bench <command> --help"
    echo "  fm-helper <command> --help"
    echo -e "\nNote: Both methods provide the same functionality. bench integration is provided for convenience.\n"
}

if [[ "$@" =~ ^restart[[:space:]]* ]]; then
    # Remove 'restart' from arguments and pass the rest
    args="${@#restart}"
    restart_command $args

elif [[ "$@" =~ ^status[[:space:]]* ]]; then
    # Remove 'status' from arguments and pass the rest
    args="${@#status}"
    status_command $args

elif [[ "$@" =~ ^stop[[:space:]]* ]]; then
    # Remove 'stop' from arguments and pass the rest
    args="${@#stop}"
    stop_command $args

elif [[ -z "$@" ]]; then
    # Run bench without exec to allow show_fm_helper_commands afterwards
    /opt/user/.bin/bench_orig "$@"
    show_fm_helper_commands

elif [[ "$@" =~ ^worker[[:space:]]* ]]; then
    # Handle bench worker separately to allow custom signal handling

    # It sends SIGTERM to the child process and exits the wrapper immediately
    handle_sigrtmin_plus_1() {
        echo "Wrapper received SIGRTMIN+1 (35), sending SIGTERM to PID $BENCH_WORKER_PID and exiting."
        # Send SIGTERM (graceful shutdown) to the bench worker process
        kill -TERM "$BENCH_WORKER_PID" 2>/dev/null
        # Exit the wrapper script immediately, do not wait for the child
        exit 0
    }

    # Set the trap for SIGRTMIN+1 (Signal 35 on most Linux systems)
    # Using the number 35 for broader compatibility
    trap 'handle_sigrtmin_plus_1' 35

    # Run the actual bench worker command in the background
    /opt/user/.bin/bench_orig "$@" &

    # Capture the Process ID (PID) of the background command
    BENCH_WORKER_PID=$!

    # Wait for the bench worker process to finish
    # This wait will be interrupted if the trap is triggered
    wait "$BENCH_WORKER_PID"
    # Capture the exit status of the waited process
    EXIT_STATUS=$?

    # Clean up the trap
    trap - 35

    # Exit the wrapper script with the exit status of the bench worker process
    exit $EXIT_STATUS
else
    # Use exec for other bench commands to pass signals directly
    exec /opt/user/.bin/bench_orig "$@"
fi
