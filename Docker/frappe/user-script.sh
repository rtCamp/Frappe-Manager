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

		# Rewritten in place. frappe-dev.conf runs /opt/user/bench-dev-server, so writing the
		# corrected copy to bench-dev-server.sh patched a file nothing executes: the dev server
		# kept `bench serve --port 80` with no host, bound 127.0.0.1 inside the container, and the
		# separate nginx container could never reach it -- a bench that built cleanly and then
		# served 502 forever.
		if ! grep -q -- "--host 0.0.0.0" /opt/user/bench-dev-server; then
			echo "Configuring bench dev server to bind 0.0.0.0:80"
			if /usr/local/bin/bench serve --help 2>/dev/null | grep -q "\-\-host"; then
				sed -i 's/--port [0-9]\+/--host 0.0.0.0 --port 80/' /opt/user/bench-dev-server
			fi
			chmod +x /opt/user/bench-dev-server
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
