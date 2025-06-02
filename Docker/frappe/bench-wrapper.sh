#!/bin/bash

# --- Define Logging Function ---
LOG_FILE="/tmp/bench.log"
RQ_LOG_FILE="/tmp/bench.rq.log"
MONITORING_MODE=0

log_message() {
    # Prepend timestamp with microseconds and PID to the message and append to log file
    echo "$(date '+%Y-%m-%d %H:%M:%S.%N' | cut -c1-26) - PID: $$ - $1" >> "$LOG_FILE"
}
# --- End Logging Function ---

# Check if pstree is available, if not fall back to ps
if ! command -v pstree >/dev/null 2>&1; then
    log_message "pstree not found, installing procps package"
    apt-get update -qq && apt-get install -qq procps >/dev/null 2>&1
    if [ $? -ne 0 ]; then
        log_message "Failed to install procps, will fall back to ps command"
    fi
fi

# Function to show process tree
show_process_tree() {
    local pid=$1
    if command -v pstree >/dev/null 2>&1; then
        pstree -p "$pid" 2>/dev/null | while read line; do
            log_message "PROCTREE: $line"
        done
    else
        ps -ef | grep -E "(bench|worker|supervisord)" | grep -v grep | while read line; do
            log_message "PROCTREE: $line"
        done
    fi
}

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
    # Detaches wrapper from supervisord control while keeping worker running
    handle_signal_34_detach_and_terminate_worker() {
        log_message "Received request to detach worker (signal 34)"
        
        if [[ -z "$BENCH_WORKER_PID" ]]; then
            log_message "No worker PID found, cannot detach"
            exit 1
        fi

        # Log current state
        log_message "Process tree before detaching:"
        show_process_tree "$$"

        # First fork
        log_message "Starting daemonization process - first fork"
        daemon_pid=$$
        
        fork1_pid=$(bash -c "echo \$PPID & exit")

        if [ $? -ne 0 ]; then
            log_message "First fork failed"
            exit 1
        fi

        if [ "$fork1_pid" -ne 0 ]; then
            # Launch background process to send SIGTERM after delay
            (
                log_message "Starting delayed termination process"
                sleep 5
                if kill -0 -"$BENCH_WORKER_PID" 2>/dev/null; then
                    log_message "Sending delayed SIGTERM to worker group -$BENCH_WORKER_PID"
                    kill -15 -"$BENCH_WORKER_PID"
                    log_message "Delayed SIGTERM sent with status: $?"
                else
                    log_message "Worker group -$BENCH_WORKER_PID already terminated"
                fi
            ) &
            disown

            # Parent exits immediately
            log_message "Parent process exiting after first fork"
            exit 0
        fi

    }

    # Track which signals have already been forwarded to prevent duplicate forwarding
    # Function to forward any other trapped signal by its number
    forward_signal_by_num() {
        local signal_num="$1"

        # Log the signal receipt
        log_message "Received signal ${signal_num}"

        if [[ -n "$BENCH_WORKER_PID" ]]; then
            log_message "Forwarding signal ${signal_num} to worker group (-$BENCH_WORKER_PID)"

            # Forward directly to process group
            kill "-${signal_num}" "-$BENCH_WORKER_PID" 2>/dev/null
            KILL_STATUS=$?

            log_message "kill signal forward status: $KILL_STATUS"

            if [ $KILL_STATUS -eq 0 ]; then
                log_message "Successfully forwarded signal ${signal_num} to process group -$BENCH_WORKER_PID"
            else
                log_message "Failed to forward signal ${signal_num} to process group -$BENCH_WORKER_PID"
            fi

            # For SIGTERM, we want to continue monitoring but log it
            if [ "$signal_num" -eq 15 ]; then
                log_message "SIGTERM received. Continuing to monitor worker..."
            fi

            # Reset the signal handler for this signal
            trap "forward_signal_by_num $signal_num" "$signal_num"
            
            return 0  # Return to the wait loop
        else
            log_message "Bench worker PID not set, cannot forward signal ${signal_num}."
            return 1
        fi
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
    #setsid bash -c "echo \"\$PPID\" > /tmp/bench_wrapper.pid; exec env PYTHONDEVMODE=1 /opt/user/.bin/bench_orig $*" </dev/null >> "$RQ_LOG_FILE" 2>&1 &
    setsid bash -c "echo \"\$PPID\" > /tmp/bench_wrapper.pid; exec env PYTHONDEVMODE=1 /opt/user/.bin/bench_orig $*" </dev/null 2>&1 &

    # Capture the Process ID (PID) of the background command
    BENCH_WORKER_PID=$!
    BENCH_WRAPPER_PID=$(cat /tmp/bench_wrapper.pid 2>/dev/null)
    log_message "Bench wrapper PID: $BENCH_WRAPPER_PID, Worker group PID: $BENCH_WORKER_PID"

    # Use continuous wait loop that handles interruptions
    log_message "Starting wait loop for worker"
    while true; do
        wait "$BENCH_WORKER_PID" 2>/dev/null
        wait_status=$?
        
        # Check if process actually exited or wait was interrupted
        if ! kill -0 "$BENCH_WORKER_PID" 2>/dev/null; then
            log_message "Worker actually exited with status: $wait_status"
            EXIT_STATUS=$wait_status
            break
        fi
        
        log_message "Wait interrupted by signal, continuing to wait..."
    done

    # --- Cleanup ---
    trap - 34
    log_message "Cleaned up trap for signal 34."
    log_message "Wrapper exiting with status: $EXIT_STATUS"
    exit $EXIT_STATUS
else
    # Use exec for other bench commands to pass signals directly and replace the wrapper
    log_message "Executing '/opt/user/.bin/bench_orig $@'"
    exec /opt/user/.bin/bench_orig "$@"
fi
