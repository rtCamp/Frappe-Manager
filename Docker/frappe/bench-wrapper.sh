#!/bin/bash

# --- Define Logging Function ---
LOG_FILE="/tmp/bench.log"
RQ_LOG_FILE="/tmp/bench.rq.log"

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
    # Detaches wrapper from supervisord control while keeping worker running
    handle_signal_34_detach_and_terminate_worker() {
        log_message "Received request to detach worker (signal 34)"
        
        if [[ -z "$BENCH_WORKER_PID" ]]; then
            log_message "No worker PID found, cannot detach"
            exit 1
        fi

        # Log current state
        log_message "Process tree before detaching:"
        ps -ef | grep -E "(bench|worker|supervisord)" | grep -v grep | while read line; do
            log_message "PROCTREE: $line"
        done

        # First fork
        log_message "Starting daemonization process - first fork"
        daemon_pid=$$
        
        fork1_pid=$(bash -c "echo \$PPID & exit")
        if [ $? -ne 0 ]; then
            log_message "First fork failed"
            exit 1
        fi

        if [ "$fork1_pid" -ne 0 ]; then
            # Parent exits immediately
            log_message "Parent process exiting after first fork"
            exit 0
        fi

        # Child continues...
        
        # Create new session
        if ! setsid; then
            log_message "setsid failed"
            exit 1
        fi

        # Set umask
        umask 0

        # Change working directory
        cd /

        # Second fork
        fork2_pid=$(bash -c "echo \$PPID & exit")
        if [ $? -ne 0 ]; then
            log_message "Second fork failed"
            exit 1
        fi

        if [ "$fork2_pid" -ne 0 ]; then
            log_message "Parent process exiting after second fork"
            exit 0
        fi

        # Now we're fully daemonized
        daemon_pid=$$
        log_message "Successfully daemonized with PID: $daemon_pid"

        # Close and redirect standard file descriptors
        exec 0>&- 
        exec 1>&- 
        exec 2>&- 
        exec 0</dev/null
        exec 1>>"$LOG_FILE"
        exec 2>>"$LOG_FILE"

        # Write PID file
        echo "$daemon_pid" > /tmp/bench-daemon.pid
        log_message "Wrote daemon PID to /tmp/bench-daemon.pid"

        # Function to clean up daemon resources
        cleanup_daemon() {
            local signal=$1
            log_message "Daemon received $signal - starting cleanup"
            
            # Forward signal to worker process group
            if kill -$signal -$BENCH_WORKER_PID 2>/dev/null; then
                log_message "Forwarded $signal to worker process group -$BENCH_WORKER_PID"
            else
                log_message "Failed to forward $signal to worker process group -$BENCH_WORKER_PID"
            fi

            # Wait for worker processes to finish
            local timeout=30  # 30 seconds timeout
            local counter=0
            while kill -0 -$BENCH_WORKER_PID 2>/dev/null; do
                sleep 1
                ((counter++))
                if [ $counter -ge $timeout ]; then
                    log_message "Timeout waiting for workers to finish, forcing termination"
                    kill -9 -$BENCH_WORKER_PID 2>/dev/null
                    break
                fi
                log_message "Waiting for workers to finish ($counter/$timeout seconds)..."
            done

            # Remove PID file
            rm -f /tmp/bench-daemon.pid
            log_message "Removed PID file, daemon cleanup complete"
            
            # Exit with appropriate status
            exit 0
        }

        # Set up improved signal handlers for daemon
        trap 'cleanup_daemon TERM' SIGTERM
        trap 'cleanup_daemon HUP' SIGHUP
        trap 'cleanup_daemon INT' SIGINT

        log_message "Daemon process fully initialized and running"
        log_message "Original worker process group ($BENCH_WORKER_PID) is now detached"

        # Monitor worker process group
        while true; do
            if ! kill -0 -$BENCH_WORKER_PID 2>/dev/null; then
                log_message "Worker process group no longer exists, daemon exiting"
                cleanup_daemon TERM
            fi
            sleep 1
        done
    }

    # Track which signals have already been forwarded to prevent duplicate forwarding
    # Function to forward any other trapped signal by its number
    forward_signal_by_num() {
        local signal_num="$1"

        # Log the signal receipt
        log_message "Received signal ${signal_num}"
        log_message "envs: $(env)"

        log_message "xd: $BENCH_WORKER_PID"
        log_message "xd: $BENCH_WRAPPER_PID"

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

            if [ "$signal_num" -eq 15 ]; then
                log_message "SIGTERM received. Continuing to monitor worker..."
            fi
        else
            log_message "Bench worker PID not set, cannot forward signal ${signal_num}."
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

    # Monitor worker process group without using wait
    EXIT_STATUS=0
    log_message "Starting monitoring loop for worker process group"
    
    while true; do
        if ! kill -0 -$BENCH_WORKER_PID 2>/dev/null; then
            EXIT_STATUS=$?
            log_message "Worker process group no longer exists (status: $EXIT_STATUS), wrapper exiting"
            break
        fi
        # Log process tree periodically to help with debugging
        if [ $((SECONDS % 60)) -eq 0 ]; then
            log_message "Current process tree:"
            ps -ef | grep -E "(bench|worker)" | grep -v grep | while read line; do
                log_message "PROCTREE: $line"
            done
        fi
        sleep 1
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
