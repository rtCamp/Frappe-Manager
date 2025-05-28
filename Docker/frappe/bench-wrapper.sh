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
    /opt/.pyenv/shims/bench "$@"
    show_fm_helper_commands
else
    # Use exec to pass signals directly to the bench command
    exec /opt/.pyenv/shims/bench "$@"
fi
