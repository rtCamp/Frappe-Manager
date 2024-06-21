#!/bin/bash
restart_command() {
      supervisorctl -c /opt/user/supervisord.conf restart all
}
status_command() {
      supervisorctl -c /opt/user/supervisord.conf status all
}

if [[ "$@" =~ ^restart[[:space:]]* ]]; then
    restart_command
elif [[ "$@" =~ ^status[[:space:]]* ]]; then
    status_command
else
    /opt/.pyenv/shims/bench "$@"
fi
