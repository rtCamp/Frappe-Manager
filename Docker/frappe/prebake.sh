#!/bin/bash
source /scripts/helper-function.sh

set -e

WEB_PORT=80
REDIS_SOCKETIO_PORT=80

emer() {
    echo "$@"
    exit 1
}

BENCH_COMMAND='/opt/user/.bin/bench_orig'

configure_common_site_config(){
    $BENCH_COMMAND config dns_multitenant on
    update_common_site_config webserver_port "$WEB_PORT"
    update_common_site_config socketio_port "$REDIS_SOCKETIO_PORT"
}

# create bench
$BENCH_COMMAND init --skip-assets --skip-redis-config-generation --frappe-branch "$PREBAKE_FRAPPE_BRANCH" frappe-bench

cd frappe-bench

configure_common_site_config

# install apps
install_apps "$PREBAKE_APPS"

# Addresses the introduction of the --host flag in bench serve command for compatibility with Frappe version updates.
bench_serve_help_output=$($BENCH_COMMAND serve --help)
host_changed=$(echo "$bench_serve_help_output" | grep -c 'host' || true)

if [[ "$host_changed" -ge 1 ]]; then
    awk -v a="$WEB_PORT" '{sub(/--port [[:digit:]]+/,"--host 0.0.0.0 --port "a); print}' /opt/user/bench-dev-server > file.tmp && mv file.tmp /opt/user/bench-dev-server.sh
else
    awk -v a="$WEB_PORT" '{sub(/--port [[:digit:]]+/,"--port "a); print}' /opt/user/bench-dev-server > file.tmp && mv file.tmp /opt/user/bench-dev-server.sh
fi

chmod +x /opt/user/bench-dev-server.sh

$BENCH_COMMAND build
