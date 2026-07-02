#!/bin/bash
source /scripts/helper-function.sh

set -e

cleanup() {
	echo "Received signal SIGTERM, stopping..."
	if [ -n "$running_script_pid" ]; then
		kill -s SIGTERM "$running_script_pid"
	fi
	exit 0
}

trap cleanup SIGTERM

emer() {
	echo "$@"
	exit 1
}

if [[ -n "$BENCH_START_OFF" ]]; then
	tail -f /dev/null
else
	# Link appropriate supervisor config based on FRAPPE_ENV
	# FRAPPE_ENV is set by FM in docker-compose.yml (defaults to 'dev')
	if [ "${FRAPPE_ENV:-dev}" = "dev" ]; then
		echo "Linking development supervisor config (frappe-dev.conf)"
		ln -sfn /opt/user/frappe-dev.conf /opt/user/conf.d/active-env.conf

		if [ ! -f /opt/user/bench-dev-server.sh ]; then
			echo "Configuring bench dev server to bind 0.0.0.0:80"
			if /usr/local/bin/bench serve --help 2>/dev/null | grep -q "\-\-host"; then
				sed 's/--port [0-9]\+/--host 0.0.0.0 --port 80/' /opt/user/bench-dev-server >/opt/user/bench-dev-server.sh
			else
				sed 's/--port [0-9]\+/--port 80/' /opt/user/bench-dev-server >/opt/user/bench-dev-server.sh
			fi
			chmod +x /opt/user/bench-dev-server.sh
			echo "Configured bench dev server"
		fi
	else
		echo "Linking production supervisor config (web.fm.supervisor.conf)"
		ln -sfn /workspace/frappe-bench/config/web.fm.supervisor.conf /opt/user/conf.d/active-env.conf
	fi

	echo "Starting supervisor.."
	supervisord -c /opt/user/supervisord.conf &
	running_script_pid=$!
	wait $running_script_pid
fi
