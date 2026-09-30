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
		# The dev server's bind address is decided by bench-dev-server itself, because `bench`
		# only resolves subcommands inside a bench directory and this script runs from `/`.
	else
		echo "Linking production supervisor config (web.fm.supervisor.conf)"
		ln -sfn /workspace/frappe-bench/config/web.fm.supervisor.conf /opt/user/conf.d/active-env.conf
	fi

	echo "Starting supervisor.."
	supervisord -c /opt/user/supervisord.conf &
	running_script_pid=$!
	wait $running_script_pid
fi
