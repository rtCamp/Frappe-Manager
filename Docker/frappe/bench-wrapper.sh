#!/bin/bash

# --- Define Logging Function ---
LOG_FILE="/tmp/bench-wrapper.log"

log_message() {
    # Prepend timestamp with microseconds and PID to the message and append to log file
    echo "$(date '+%Y-%m-%d %H:%M:%S.%N' | cut -c1-26) - PID: $$ - $1" >> "$LOG_FILE"
}
# --- End Logging Function ---

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
    log_message "Running bench without arguments followed by help message"
    /opt/user/.bin/bench_orig "$@"
    show_fm_helper_commands
    
    # Ensure proper exit with the exit code from the bench_orig command
    exit $?

elif [[ "$@" =~ ^worker[[:space:]]* ]]; then
    # --- Signal Handling for 'bench worker' ---

    # Ensure BENCH_WORKER_PID is unset initially
    BENCH_WORKER_PID=""

    # Function to handle Signal 34:
    # Sends SIGTERM to the worker, detaches wrapper from supervisord control,
    # and continues monitoring until worker exits.
    handle_signal_34_detach_and_terminate_worker() {
        log_message "Wrapper received signal 34 (request to terminate worker and detach)."
        if [[ -n "$BENCH_WORKER_PID" ]]; then
            # Log the current process tree for debugging
            log_message "Process tree before detaching:"
            ps -ef | grep -E "(bench|worker|supervisord)" | grep -v grep | while read line; do
                log_message "PROCTREE: $line"
            done

            log_message "Creating daemon process to handle worker termination"

            # Create background daemon process that will send signal and wait for worker to terminate
            (
                # Redirect I/O
                exec 0</dev/null 1>>"$LOG_FILE" 2>>"$LOG_FILE"

                # Create new session
                setsid &>/dev/null

                # Add initial delay to ensure wrapper has fully exited
                sleep 1

                echo "$(date '+%Y-%m-%d %H:%M:%S.%N' | cut -c1-26) - PID: $$ - Detached process checking worker status (PID $BENCH_WORKER_PID)"

                # Check if worker is still running
                if kill -0 $BENCH_WORKER_PID 2>/dev/null; then
                    echo "$(date '+%Y-%m-%d %H:%M:%S.%N' | cut -c1-26) - PID: $$ - Worker is still running, sending SIGTERM"

                    # Log processes and signal states before signal
                    ps -ef | grep -E "(bench|worker|supervisord)" | grep -v grep | while read line; do
                        echo "$(date '+%Y-%m-%d %H:%M:%S.%N' | cut -c1-26) - PID: $$ - PROCTREE-BEFORE: $line"
                    done

                    # Try to check what signals the worker process has received/blocked
                    echo "$(date '+%Y-%m-%d %H:%M:%S.%N' | cut -c1-26) - PID: $$ - Signal state for worker process:"
                    cat /proc/$BENCH_WORKER_PID/status | grep -E "^Sig" 2>/dev/null | while read line; do
                        echo "$(date '+%Y-%m-%d %H:%M:%S.%N' | cut -c1-26) - PID: $$ - SIGNAL-STATE: $line"
                    done

                    # Send signals to the worker process
                    echo "$(date '+%Y-%m-%d %H:%M:%S.%N' | cut -c1-26) - PID: $$ - Sending SIGTERM to worker"

                    # Send SIGTERM directly to the process group
                    echo "$(date '+%Y-%m-%d %H:%M:%S.%N' | cut -c1-26) - PID: $$ - Sending SIGTERM (15) to process group"
                    kill -15 -$BENCH_WORKER_PID 2>/dev/null
                    signal_result=$?
                    echo "$(date '+%Y-%m-%d %H:%M:%S.%N' | cut -c1-26) - PID: $$ - SIGTERM sent, result: $signal_result"

                    # Track worker status with timestamps
                    start_time=$(date +%s)

                    while kill -0 $BENCH_WORKER_PID 2>/dev/null; do
                        current_time=$(date +%s)
                        elapsed=$((current_time - start_time))

                        # Log status every 5 seconds
                        if (( elapsed % 5 == 0 )); then
                            echo "$(date '+%Y-%m-%d %H:%M:%S.%N' | cut -c1-26) - PID: $$ - Worker still running after $elapsed seconds"
                            # Capture process state and signal info
                            ps -o pid,ppid,stat,cmd -p $BENCH_WORKER_PID | while read line; do
                                echo "$(date '+%Y-%m-%d %H:%M:%S.%N' | cut -c1-26) - PID: $$ - PROCSTATE: $line"
                            done

                            # Check current signal state
                            cat /proc/$BENCH_WORKER_PID/status | grep -E "^Sig" 2>/dev/null | while read line; do
                                echo "$(date '+%Y-%m-%d %H:%M:%S.%N' | cut -c1-26) - PID: $$ - CURRENT-SIGNALS: $line"
                            done
                        fi
                        sleep 1
                    done

                    end_time=$(date +%s)
                    total_time=$((end_time - start_time))
                    echo "$(date '+%Y-%m-%d %H:%M:%S.%N' | cut -c1-26) - PID: $$ - Worker process terminated after $total_time seconds"

                    # Try to get exit status info
                    echo "$(date '+%Y-%m-%d %H:%M:%S.%N' | cut -c1-26) - PID: $$ - Checking for exit signal in /proc/[PID]/stat if available"
                    if [ -f /proc/$BENCH_WORKER_PID/stat ]; then
                        # This might not work because the process is gone, but worth a try
                        cat /proc/$BENCH_WORKER_PID/stat 2>/dev/null | while read line; do
                            echo "$(date '+%Y-%m-%d %H:%M:%S.%N' | cut -c1-26) - PID: $$ - EXIT-INFO: $line"
                        done
                    else
                        echo "$(date '+%Y-%m-%d %H:%M:%S.%N' | cut -c1-26) - PID: $$ - No /proc/$BENCH_WORKER_PID/stat available"
                    fi
                else
                    echo "$(date '+%Y-%m-%d %H:%M:%S.%N' | cut -c1-26) - PID: $$ - Worker already terminated before we could send SIGTERM!"
                fi

                # Final process tree check
                echo "$(date '+%Y-%m-%d %H:%M:%S.%N' | cut -c1-26) - PID: $$ - Final process tree:"
                ps -ef | grep -E "(bench|worker|supervisord)" | grep -v grep | while read line; do
                    echo "$(date '+%Y-%m-%d %H:%M:%S.%N' | cut -c1-26) - PID: $$ - PROCTREE-AFTER: $line"
                done
            ) &

            # Tell supervisord we're done
            log_message "Wrapper exiting, detached monitor will handle termination"
            exit 0
        else
            log_message "Bench worker PID not set. Cannot send SIGTERM."
            exit 1
        fi
    }

    # Track which signals have already been forwarded to prevent duplicate forwarding
    declare -A FORWARDED_SIGNALS

    # Function to forward any other trapped signal by its number
    forward_signal_by_num() {
        local signal_num="$1"

        # Check if we've already forwarded this signal
        if [[ -n "${FORWARDED_SIGNALS[$signal_num]}" ]]; then
            log_message "Signal ${signal_num} already being processed, skipping duplicate forwarding."
            return
        fi

        # Mark this signal as being forwarded
        FORWARDED_SIGNALS[$signal_num]=1

        log_message "Wrapper received signal ${signal_num}."

        if [[ -n "$BENCH_WORKER_PID" ]]; then
            log_message "Forwarding signal ${signal_num} to bench worker (PID $BENCH_WORKER_PID)."
            # Send the *same* signal number that the wrapper received to the child process
            kill "-${signal_num}" "$BENCH_WORKER_PID" 2>/dev/null

            # For SIGTERM (15), ensure we don't exit immediately but wait for child to handle it
            if [ "$signal_num" -eq 15 ]; then
                log_message "SIGTERM received. Waiting for worker to gracefully terminate..."
                # DO NOT exit - just let the wait command handle it
            fi
        else
            log_message "Bench worker PID not set, cannot forward signal ${signal_num}."
        fi

        # Clear this signal from being processed
        unset FORWARDED_SIGNALS[$signal_num]

        # DO NOT exit the wrapper here. Let the 'wait' command handle termination.
    }

    # --- Set Traps ---
    log_message "Setting up signal traps..."
    # Trap for Signal 34 (custom detach and terminate worker behavior)
    trap 'handle_signal_34_detach_and_terminate_worker' 34
    log_message "Set trap for signal 34 to 'handle_signal_34_detach_and_terminate_worker'."

    # Set up explicit traps only for signals we want to handle
    # SIGTERM (15) - normal termination request
    trap "forward_signal_by_num 15" 15
    # SIGHUP (1) - terminal disconnect
    trap "forward_signal_by_num 1" 1
    # SIGINT (2) - Ctrl+C
    trap "forward_signal_by_num 2" 2
    # SIGQUIT (3) - Ctrl+\
    trap "forward_signal_by_num 3" 3

    # # Explicitly ignore SIGCHLD (17) - avoid forwarding child status changes
    # trap ":" 17
    # log_message "Ignoring SIGCHLD to avoid signal flood."

    log_message "Set up explicit signal handlers for SIGTERM, SIGHUP, SIGINT, SIGQUIT."

    # --- Run the actual bench worker command ---
    # Run in the background so the wrapper can wait and handle signals
    # Use setsid to ensure the worker becomes a process group leader
    # Use bash -c with exec and proper I/O redirection to prevent broken pipes
    setsid bash -c "exec /opt/user/.bin/bench_orig $*" </dev/null >> "$LOG_FILE" 2>&1 &

    # Capture the Process ID (PID) of the background command
    BENCH_WORKER_PID=$!
    log_message "Started bench worker as process group leader (PID $BENCH_WORKER_PID)."

    # --- Wait for the child process ---
    # Wait for the bench worker process to finish.
    # This 'wait' will be interrupted if any trapped signal is received.
    wait "$BENCH_WORKER_PID"

    # Capture the exit status of the waited process
    EXIT_STATUS=$?
    log_message "Bench worker (PID $BENCH_WORKER_PID) exited with status $EXIT_STATUS."

    # --- Cleanup ---
    # Reset signal 34 trap explicitly. Other traps are generally reset on exit.
    trap - 34
    log_message "Cleaned up trap for signal 34."

    # Exit the wrapper script with the exit status of the bench worker process
    exit $EXIT_STATUS
else
    # Use exec for other bench commands to pass signals directly and replace the wrapper
    log_message "Executing '/opt/user/.bin/bench_orig $@'"
    exec /opt/user/.bin/bench_orig "$@"
fi
