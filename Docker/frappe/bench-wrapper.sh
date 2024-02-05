#!/bin/bash
after_command() {
        supervisorctl -c /opt/user/supervisord.conf restart frappe-bench-dev:
}
if [[ "$@" =~ ^restart[[:space:]]* ]]; then
        after_command
else
        /opt/.pyenv/shims/bench "$@"
fi
