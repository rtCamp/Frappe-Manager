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
    # --- Signal Handling for 'bench worker' ---

    # Ensure BENCH_WORKER_PID is unset initially
    BENCH_WORKER_PID=""

    # Function to handle SIGRTMIN+1 (Signal 35) - Special Case
    handle_sigrtmin_plus_1() {
        echo "Wrapper: Received SIGRTMIN+1 (35)."
        if [[ -n "$BENCH_WORKER_PID" ]]; then
            echo "Wrapper: Sending SIGTERM to bench worker (PID $BENCH_WORKER_PID) and exiting wrapper immediately."
            # Send SIGTERM (graceful shutdown) to the actual bench worker process
            kill -TERM "$BENCH_WORKER_PID" 2>/dev/null
        else
            echo "Wrapper: Bench worker PID not set, cannot send SIGTERM. Exiting wrapper."
        fi
        # Exit the wrapper script immediately with success status
        exit 0
    }

    # Function to forward any other trapped signal by its number
    forward_signal_by_num() {
        local signal_num="$1"
        echo "Wrapper: Received signal ${signal_num}."
        if [[ -n "$BENCH_WORKER_PID" ]]; then
            echo "Wrapper: Forwarding signal ${signal_num} to bench worker (PID $BENCH_WORKER_PID)."
            # Send the *same* signal number that the wrapper received to the child process
            kill "-${signal_num}" "$BENCH_WORKER_PID" 2>/dev/null
        else
            echo "Wrapper: Bench worker PID not set, cannot forward signal ${signal_num}."
        fi
        # DO NOT exit the wrapper here. Let the 'wait' command handle termination.
    }

    # --- Set Traps ---
    echo "Wrapper: Setting up signal traps..."
    # Special trap for Signal 35
    trap 'handle_sigrtmin_plus_1' 35
    echo "Wrapper: Set trap for signal 35."

    # Dynamically trap signals 1-64 (excluding non-trappable and special case 35)
    for sig_num in $(seq 1 64); do
        case "$sig_num" in
            9 | 19) # SIGKILL, SIGSTOP - cannot be trapped, skip
                ;;
            35) # Special case - already handled, skip
                ;;
            *) # All other signals in the range 1-64
                # Set the trap to call the forwarding function with the signal number
                trap "forward_signal_by_num ${sig_num}" "${sig_num}"
                ;;
        esac
    done
    echo "Wrapper: Dynamically set traps for signals 1-64 (excluding 9, 19, 35)."

    # --- Run the actual bench worker command ---
    # Run in the background so the wrapper can wait and handle signals
    /opt/user/.bin/bench_orig "$@" &

    # Capture the Process ID (PID) of the background command
    BENCH_WORKER_PID=$!
    echo "Wrapper: Started bench worker (PID $BENCH_WORKER_PID)."

    # --- Wait for the child process ---
    # Wait for the bench worker process to finish.
    # This 'wait' will be interrupted if any trapped signal is received.
    wait "$BENCH_WORKER_PID"
    # Capture the exit status of the waited process
    EXIT_STATUS=$?
    echo "Wrapper: Bench worker (PID $BENCH_WORKER_PID) exited with status $EXIT_STATUS."

    # --- Cleanup ---
    # Reset signal 35 trap explicitly. Other traps are generally reset on exit.
    trap - 35
    echo "Wrapper: Cleaned up trap for signal 35."

    # Exit the wrapper script with the exit status of the bench worker process
    exit $EXIT_STATUS
else
    # Use exec for other bench commands to pass signals directly and replace the wrapper
    echo "Wrapper: Executing '/opt/user/.bin/bench_orig $@'"
    exec /opt/user/.bin/bench_orig "$@"
fi
