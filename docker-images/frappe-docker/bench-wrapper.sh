#!/bin/bash
# before_command() {
# 	supervisorctl -c /opt/user/supervisord.conf stop bench-dev
# }
after_command() {
	if [[ -f "/opt/user/supervisord.sock" ]]; then
		supervisorctl -c /opt/user/supervisord.conf signal KILL bench-dev
	fi
}
wrapper() {
	#before_command
	"$@"
	after_command
}
wrapper /opt/.pyenv/shims/bench "$@"
