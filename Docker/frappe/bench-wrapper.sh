#!/bin/bash

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

if [[ "$1" == "restart" ]]; then
	shift
	exec fmx restart "$@"

elif [[ "$1" == "status" ]]; then
	shift
	exec fmx status "$@"

elif [[ "$1" == "stop" ]]; then
	shift
	exec fmx stop "$@"

elif [[ -z "$1" ]]; then
	/usr/local/bin/bench
	show_fm_helper_commands
	exit $?

else
	exec /usr/local/bin/bench "$@"
fi
