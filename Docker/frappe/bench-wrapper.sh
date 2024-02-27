#!/bin/bash
restart_command() {
      supervisorctl -c /opt/user/supervisord.conf restart frappe-bench-dev:
}
status_command() {
      supervisorctl -c /opt/user/supervisord.conf status frappe-bench-dev:
}

if [[ "$@" =~ ^restart[[:space:]]* ]]; then
    restart_command
elif [[ "$@" =~ ^status[[:space:]]* ]]; then
    status_command
else
    /opt/.pyenv/shims/bench "$@"
fi
