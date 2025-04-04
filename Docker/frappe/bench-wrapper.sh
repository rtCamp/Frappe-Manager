#!/bin/bash
restart_command() {
      fm-helper restart "$@"
}

status_command() {
      fm-helper status "$@"
}

stop_command() {
      fm-helper stop "$@"
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

if [[ "$1" == "restart" ]]; then
    shift
    restart_command "$@"
elif [[ "$1" == "status" ]]; then
    shift
    status_command "$@"
elif [[ "$1" == "stop" ]]; then
    shift
    stop_command "$@"
elif [[ -z "$@" ]]; then
    /usr/local/bin/bench "$@"
    show_fm_helper_commands
else
    /usr/local/bin/bench "$@"
fi
