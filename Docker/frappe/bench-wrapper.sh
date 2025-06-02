#!/bin/bash

# === bench-wrapper.sh Documentation ===
#
# Purpose:
#   Manages Frappe bench worker processes under supervisor control, handling:
#   - Process detachment
#   - Signal management
#   - Logging and monitoring
#
# Core Functions
# =============
#
# 1. Worker Launch & Monitoring
# ---------------------------
# Initial Launch:
#   - Sets up logging streams
#   - Creates process group via setsid
#   - Captures worker PID
#   - Establishes signal handlers
#
# Monitor Mode (--monitor):
#   - Triggered by Signal 34
#   - Performs process detachment
#   - Implements 3-second grace period
#   - Sends TERM signal to process group
#   - Tracks child process changes
#   - Logs state transitions
#
# 2. Signal Management
# ------------------
# Handled Signals:
#   - 34 (SIGRTMIN+2): Initiates detachment
#   - 1 (SIGHUP): Forwarded to worker
#   - 2 (SIGINT): Forwarded to worker
#   - 3 (SIGQUIT): Forwarded to worker
#   - 15 (SIGTERM): Forwarded to worker
#
# 3. Process Flow
# -------------
# Normal Operation:
#   1. Start under supervisor
#   2. Execute bench worker command
#   3. Wait for signals
#
# Detachment Sequence:
#   1. Receive Signal 34
#   2. Fork monitor process
#   3. Setup stream redirection
#   4. Detach from supervisor
#   5. Wait 3 seconds
#   6. Send TERM to process group
#   7. Monitor until completion
#
# Logging
# =======
# Files:
#   - /tmp/bench.log: Main process log
#   - /tmp/bench.rq.log: Worker output
#
# Debug Mode:
#   Enable: export BENCH_DEBUG=1
#   Provides:
#   - Process tree changes
#   - File descriptor states
#   - Signal handling details
#   - Environment information
#
# Command Examples
# ==============
# Start Worker:
#   bench worker --queue long,default,short
#
# Trigger Detachment:
#   kill -34 <bench-wrapper-pid>
#
# Monitor Status:
#   tail -f /tmp/bench.log
#
# Implementation Notes
# ==================
# - Uses setsid for process group management
# - Implements robust signal forwarding
# - Handles supervisor detection
# - Maintains process hierarchy logging
# - Ensures clean process detachment
#
# Error Handling
# ============
# - Logs failed signal operations
# - Tracks process state changes
# - Reports worker termination
# - Maintains audit trail
#
# === End Documentation ===

# --- Define Logging Function ---
LOG_FILE="/workspace/frappe-bench/logs/worker.error.log"
RQ_LOG_FILE="/workspace/frappe-bench/logs/worker.log"

MONITORING_MODE=0
DEBUG=0  # Set to 1 to enable debug logging

# Enable debug if BENCH_DEBUG is set
if [[ -n "$BENCH_DEBUG" ]]; then
    DEBUG=1
fi

debug_log() {
    if [[ $DEBUG -eq 1 ]]; then
        log_message "DEBUG: $1"
    fi
}

log_message() {
    local timestamp=$(date '+%Y-%m-%d %H:%M:%S.%N' | cut -c1-26)
    local context="PID: $$ PPID: $PPID CMD: $0"
    echo "$timestamp - $context - $1" >> "$LOG_FILE"
}

is_under_supervisor() {
    # Check various ways to detect supervisor
    if [[ -n "$SUPERVISOR_ENABLED" ]] || \
       [[ "$(ps -o comm= -p $PPID)" =~ supervisor ]] || \
       [[ -n "$SUPERVISOR_PROCESS_NAME" ]]; then
        return 0
    fi
    return 1
}
# --- End Logging Function ---

setup_process_streams() {
    debug_log "Setting up process streams"
    debug_log "Current FD list: $(ls -l /proc/$$/fd)"
    exec 1>>"$RQ_LOG_FILE"
    exec 2>>"$RQ_LOG_FILE"
    debug_log "Process streams redirected to $RQ_LOG_FILE"
}

# Add new monitor-simple mode
if [[ "$1" == "--monitor" ]]; then
    MONITORING_MODE=1
    BENCH_WORKER_PID="$2"
    
    log_message "Simple monitor mode started"
    log_message "Monitor PID: $$, PPID: $PPID, Worker PID: $BENCH_WORKER_PID"
    log_message "Initial process tree before disown:"
    ps -ef f | grep -v grep | grep -E "supervisor|bench|worker" >> "$LOG_FILE"
    
    # Set up proper stream handling before detachment
    setup_process_streams
    
    # Detach completely from process hierarchy
    log_message "Attempting to disown worker process $BENCH_WORKER_PID"
    disown -h "$BENCH_WORKER_PID" 2>/dev/null
    disown_status=$?
    log_message "Disown worker status: $disown_status"
    
    # Ensure we're completely detached
    cd /
    umask 022
    
    # Close any inherited file descriptors
    for fd in $(ls /proc/$$/fd); do
        case "$fd" in
            0|1|2) continue ;; # Keep stdin/stdout/stderr
            *) eval "exec $fd>&-" 2>/dev/null ;; # Close everything else
        esac
    done
    
    log_message "Detaching all remaining background jobs"
    disown -a 2>/dev/null
    
    log_message "Process tree after disown:"
    ps -ef f | grep -v grep | grep -E "supervisor|bench|worker" >> "$LOG_FILE"
    
    cd /
    log_message "Starting worker process monitoring"

    debug_log "Waiting 3 seconds before sending TERM signal to worker group"
    sleep 3

    # Send TERM signal to the worker process group
    if kill -TERM -"$BENCH_WORKER_PID" 2>/dev/null; then
        debug_log "Successfully sent TERM signal to worker group -$BENCH_WORKER_PID"
    else
        debug_log "Failed to send TERM signal to worker group -$BENCH_WORKER_PID"
        # Try sending to just the worker process as fallback
        if kill -TERM "$BENCH_WORKER_PID" 2>/dev/null; then
            debug_log "Sent TERM signal directly to worker PID $BENCH_WORKER_PID"
        else
            debug_log "Failed to send TERM signal to worker PID $BENCH_WORKER_PID"
        fi
    fi

    last_children=""
    
    while kill -0 "$BENCH_WORKER_PID" 2>/dev/null; do
        current_children=$(pgrep -P "$BENCH_WORKER_PID")
        if [[ "$current_children" != "$last_children" ]]; then
            debug_log "Worker children changed"
            debug_log "Previous children: $last_children"
            debug_log "Current children: $current_children"
            debug_log "Process details:"
            ps axo pid,ppid,pgid,sid,comm,wchan | grep -E "($BENCH_WORKER_PID|$current_children)" | 
                while read -r line; do
                    debug_log "PROC: $line"
                done
            last_children="$current_children"
        fi
        sleep 1
    done
    
    log_message "Worker process $BENCH_WORKER_PID has finished"
    exit 0
fi

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

        # Log current process info
        log_message "Current PPID: $PPID"
        log_message "Current PID: $$"
        
        # Fork with proper quoting and redirection
        log_message "Starting fork"
        /usr/bin/nohup "$0" "--monitor" "$BENCH_WORKER_PID" </dev/null >/dev/null 2>&1 &
        FORK_STATUS=$?
        
        log_message "Fork completed with status: $FORK_STATUS"
        
        # Give the monitor a moment to start
        sleep 1
        
        log_message "Parent exiting after fork"
        exit 0
    }

    # Function to forward signals
    forward_signal() {
        local signal_num="$1"

        if [[ -n "$BENCH_WORKER_PID" ]]; then
            log_message "Forwarding signal ${signal_num} to worker group (-$BENCH_WORKER_PID)"
            kill "-${signal_num}" "-$BENCH_WORKER_PID" 2>/dev/null
            local kill_status=$?

            if [ $kill_status -eq 0 ]; then
                log_message "Successfully forwarded signal ${signal_num}"
            else
                log_message "Failed to forward signal ${signal_num}"
            fi
        else
            log_message "Worker PID not set, cannot forward signal ${signal_num}"
        fi
        
        return 0
    }

    # Function to handle different signal types appropriately
    handle_signal() {
        local signal_num="$1"
        debug_log "Received signal $signal_num"
        debug_log "Current process tree:"
        ps -ef f | grep -E "supervisor|bench|worker" | while read -r line; do
            debug_log "TREE: $line"
        done
    
        case $signal_num in
            34)  
                debug_log "Processing detach signal"
                handle_signal_34_detach_and_terminate_worker
                ;;
            1|2|3|15)  # SIGHUP, SIGINT, SIGQUIT, SIGTERM
                debug_log "Processing termination signal $signal_num"
                forward_signal "$signal_num"
                ;;
        esac
    }

    # --- Run the actual bench worker command ---
    # Ensure RQ worker has proper streams
    export PYTHONUNBUFFERED=1
    setup_process_streams
    
    debug_log "Starting worker with args: $@"
    debug_log "Current environment:"
    env | grep -E 'SUPERVISOR|PYTHON|RQ' | while read -r line; do
        debug_log "ENV: $line"
    done

    debug_log "Pre-launch process state:"
    debug_log "Open file descriptors: $(ls -l /proc/$$/fd)"
    debug_log "Current working directory: $(pwd)"
    debug_log "ulimit settings: $(ulimit -a)"

    # Run in the background so the wrapper can wait and handle signals
    # Use setsid to ensure the worker becomes a process group leader
    # Use bash -c with exec and proper I/O redirection to prevent broken pipes
    setsid bash -c "
        # Debug logging for process creation
        log_message 'Worker launch environment:'
        log_message 'PPID: \$PPID'
        log_message 'Process groups before RQ:'
        ps axo pid,ppid,pgid,sid,comm | grep -E '(supervisor|bench|worker|rq)' >> '$LOG_FILE'
        
        # Export debug variables for RQ
        export PYTHONUNBUFFERED=1

        echo \"\$PPID\" > /tmp/bench_wrapper.pid
        exec /opt/user/.bin/bench_orig $*" \
        2>> "$RQ_LOG_FILE" &

    # Capture the Process ID (PID) of the background command
    BENCH_WORKER_PID=$!
    BENCH_WRAPPER_PID=$(cat /tmp/bench_wrapper.pid 2>/dev/null)
    log_message "Bench wrapper PID: $BENCH_WRAPPER_PID, Worker group PID: $BENCH_WORKER_PID"

    # --- Set Traps ---
    log_message "Setting up signal traps..."

    # Define the signals we want to handle
    declare -a signals_to_trap=(1 2 3 15 34)

    for signum in "${signals_to_trap[@]}"; do
        trap "handle_signal $signum" $signum
        log_message "Set trap for signal $signum"
    done

    log_message "Completed setting up signal handling"

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
