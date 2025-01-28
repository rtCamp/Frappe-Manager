#!/bin/bash
restart_command() {
      fm-helper restart
}

status_command() {
      fm-helper status
}

if [[ "$@" =~ ^restart[[:space:]]* ]]; then
    restart_command
elif [[ "$@" =~ ^status[[:space:]]* ]]; then
    status_command
else
    /opt/.pyenv/shims/bench "$@"
fi
