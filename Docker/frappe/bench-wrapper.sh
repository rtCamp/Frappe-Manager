#!/bin/bash

# Path to actual bench binary (not the wrapper)
# /usr/local/bin/bench is a symlink created by 'uv tool install frappe-bench'
# pointing to /opt/uv-tools/frappe-bench/bin/bench (the real Python script)
REAL_BENCH="/usr/local/bin/bench"

show_fm_helper_commands() {
	echo -e "\nFrappe Manager Helper Commands (integrated with bench):"
	echo "  restart  Restart services with optional RQ worker coordination and migration"
	echo "  status   Show detailed status of services"
	echo "  stop     Stop services or specific processes"
	echo -e "\nThese commands can be executed in two ways:"
	echo "  1. Using bench: bench status/stop/restart"
	echo "  2. Using fmx:   fmx status/stop/restart"
	echo -e "\nFor more details on any command:"
	echo "  bench <command> --help"
	echo "  fmx <command> --help"
}

force_kill_worker_tree() {
	echo "[bench-wrapper] Received SIGUSR1 - force-killing worker process tree (PID: $WORKER_PID)"
	if [[ -n "$WORKER_PID" ]]; then
		pkill -9 -P "$WORKER_PID" 2>/dev/null
		kill -9 "$WORKER_PID" 2>/dev/null
	fi
	exit 0
}

if [[ "$1" == "restart" ]]; then
	shift
	exec fmx restart "$@"

elif [[ "$1" == "status" ]]; then
	shift
	exec fmx status "$@"

elif [[ "$1" == "stop" ]]; then
	shift
	exec fmx stop "$@"

elif [[ "$1" == "worker" ]]; then
	trap force_kill_worker_tree SIGUSR1

	"$REAL_BENCH" "$@" &
	WORKER_PID=$!

	wait $WORKER_PID
	exit $?

elif [[ -z "$1" ]]; then
	"$REAL_BENCH"
	show_fm_helper_commands
	exit $?

else
	exec "$REAL_BENCH" "$@"
fi
