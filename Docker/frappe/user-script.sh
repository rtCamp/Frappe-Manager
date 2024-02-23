#!/bin/bash

source /scripts/helper-function.sh
source /prebake_info

set -e

emer() {
    echo "$@"
    exit 1
}

# handle previous logs location symlink
if [[ -d 'logs' ]]; then
    if [[ -f 'logs/bench-start.log' ]]; then
        mv logs/bench-start.log logs/bench-start.log.bak
    fi
    ln -sfn ../frappe-bench/logs/web.dev.log logs/bench-start.log
fi

REDIS_SOCKETIO_PORT=80
WEB_PORT=80

if [[ ! "${MARIADB_HOST:-}" ]]; then
    MARIADB_HOST='global-db'
fi

if [[ ! "${MARIADB_ROOT_PASS:-}" ]]; then
    MARIADB_ROOT_PASS='root'
fi

BENCH_COMMAND='/opt/.pyenv/shims/bench'

[[ "${ENVIRONMENT:-}" ]] || emer "[ERROR] ENVIRONMENT env not found. Please provide ENVIRONMENT env."
[[ "${WEB_PORT:-}" ]] || emer "[ERROR] WEB_PORT env not found. Please provide WEB_PORT env."
[[ "${SITENAME:-}" ]] || emer "[ERROR] SITENAME env not found. Please provide SITENAME env."

configure_common_site_config() {
    # start_time=$(date +%s.%N)

    update_common_site_config db_host "$MARIADB_HOST"
    update_common_site_config db_port 3307
    update_common_site_config redis_cache "redis://${CONTAINER_NAME_PREFIX}-redis-cache:6379"
    update_common_site_config redis_queue "redis://${CONTAINER_NAME_PREFIX}-redis-queue:6379"
    update_common_site_config redis_socketio "redis://${CONTAINER_NAME_PREFIX}-redis-socketio:6379"
    update_common_site_config mail_port 1025
    update_common_site_config mail_server 'mailhog'
    update_common_site_config disable_mail_smtp_authentication 1
    update_common_site_config webserver_port "$WEB_PORT"
    update_common_site_config developer_mode "$DEVELOPER_MODE"
    update_common_site_config socketio_port "$REDIS_SOCKETIO_PORT"
    update_common_site_config restart_supervisor_on_update 0

    # end_time=$(date +%s.%N)
    # execution_time=$(awk "BEGIN {print $end_time - $start_time}")
    # echo "Execution time for set-config : $execution_time seconds"
}

# check if the site is created
if [[ ! -d "/workspace/frappe-bench/sites/$SITENAME" ]]; then

    [[ "${REDIS_SOCKETIO_PORT:-}" ]] || emer "[ERROR] REDIS_SOCKETIO_PORT env not found. Please provide REDIS_SOCKETIO_PORT env."
    [[ "${DEVELOPER_MODE:-}" ]] || emer "[ERROR] DEVELOPER_MODE env not found. Please provide DEVELOPER_MODE env."
    [[ "${MARIADB_ROOT_PASS:-}" ]] || emer "[ERROR] MARIADB_ROOT_PASS env not found. Please provide MARIADB_ROOT_PASS env."
    [[ "${MARIADB_HOST:-}" ]] || emer "[ERROR] MARIADB_HOST env not found. Please provide MARIADB_HOST env."
    [[ "${ADMIN_PASS:-}" ]] || emer "[ERROR] ADMIN_PASS env not found. Please provide ADMIN_PASS env."
    [[ "${DB_NAME:-}" ]] || emer "[ERROR] DB_NAME env not found. Please provide DB_NAME env."
    [[ "${CONTAINER_NAME_PREFIX:-}" ]] || emer "[ERROR] CONTAINER_NAME_PREFIX env not found. Please provide CONTAINER_NAME_PREFIX env."

    # setting configuration
    wait-for-it -t 120 "$MARIADB_HOST":3306
    wait-for-it -t 120 "${CONTAINER_NAME_PREFIX}-redis-cache":6379
    wait-for-it -t 120 "${CONTAINER_NAME_PREFIX}-redis-queue":6379
    wait-for-it -t 120 "${CONTAINER_NAME_PREFIX}-redis-socketio":6379

    cd frappe-bench

    configure_common_site_config

    # HANDLE Frappe
    if [[ ! "${FRAPPE_BRANCH}" = "${PREBAKE_FRAPPE_BRANCH}" ]]; then
        bench get-app --overwrite --branch "${FRAPPE_BRANCH}" frappe
    fi

    install_apps "$APPS_LIST" "$PREBAKE_APPS"

    rm -rf archived

    $BENCH_COMMAND setup supervisor --skip-redis --skip-supervisord --yes --user "$USER"
    /scripts/divide-supervisor-conf.py config/supervisor.conf

    echo "Environment: ${ENVIRONMENT}"
    echo "Configuring frappe dev server"
    bench_serve_help_output=$($BENCH_COMMAND serve --help)
    host_changed=$(echo "$bench_serve_help_output" | grep -c 'host' || true)

    # Addresses the introduction of the --host flag in bench serve command for compatibility with Frappe version updates.
    if [[ "$host_changed" -ge 1 ]]; then
        awk -v a="$WEB_PORT" '{sub(/--port [[:digit:]]+/,"--host 0.0.0.0 --port "a); print}' /opt/user/bench-dev-server >file.tmp && mv file.tmp /opt/user/bench-dev-server.sh
    else
        awk -v a="$WEB_PORT" '{sub(/--port [[:digit:]]+/,"--port "a); print}' /opt/user/bench-dev-server >file.tmp && mv file.tmp /opt/user/bench-dev-server.sh
    fi

    chmod +x /opt/user/bench-dev-server.sh

    $BENCH_COMMAND build &
    $BENCH_COMMAND new-site --db-root-password $(cat $MARIADB_ROOT_PASS) --db-name "$DB_NAME" --db-host "$MARIADB_HOST" --admin-password "$ADMIN_PASS" --db-port 3306 --verbose --no-mariadb-socket "$SITENAME"
    $BENCH_COMMAND use "$SITENAME"
    $BENCH_COMMAND --site "$SITENAME" scheduler enable

    wait

    if [[ "${ENVIRONMENT}" = "dev" ]]; then
        cp /opt/user/frappe-dev.conf /opt/user/conf.d/frappe-dev.conf
    else
        ln -sfn /workspace/frappe-bench/config/frappe-bench-frappe-web.fm.supervisor.conf /opt/user/conf.d/frappe-bench-frappe-web.fm.supervisor.conf
    fi

    if [[ -n "$BENCH_START_OFF" ]]; then
        tail -f /dev/null
    else
        supervisord -c /opt/user/supervisord.conf
    fi

else
    wait-for-it -t 120 "$MARIADB_HOST":3306
    wait-for-it -t 120 "${CONTAINER_NAME_PREFIX}-redis-cache":6379
    wait-for-it -t 120 "${CONTAINER_NAME_PREFIX}-redis-queue":6379
    wait-for-it -t 120 "${CONTAINER_NAME_PREFIX}-redis-socketio":6379

    cd frappe-bench

    echo "Environment: ${ENVIRONMENT}"
    echo "Configuring frappe dev server"
    # Addresses the introduction of the --host flag in bench serve command for compatibility with Frappe version updates.
    bench_serve_help_output=$($BENCH_COMMAND serve --help)

    host_changed=$(echo "$bench_serve_help_output" | grep -c 'host' || true)

    $BENCH_COMMAND setup supervisor --skip-redis --skip-supervisord --yes --user "$USER"
    /scripts/divide-supervisor-conf.py config/supervisor.conf

    if [[ "$host_changed" -ge 1 ]]; then
        awk -v a="$WEB_PORT" '{sub(/--port [[:digit:]]+/,"--host 0.0.0.0 --port "a); print}' /opt/user/bench-dev-server >file.tmp && mv file.tmp /opt/user/bench-dev-server.sh
    else
        awk -v a="$WEB_PORT" '{sub(/--port [[:digit:]]+/,"--port "a); print}' /opt/user/bench-dev-server >file.tmp && mv file.tmp /opt/user/bench-dev-server.sh
    fi

    chmod +x /opt/user/bench-dev-server.sh

    # Addresses the introduction of the --host flag in bench serve command for compatibility with Frappe version updates.
    if [[ "${ENVIRONMENT}" = "dev" ]]; then
        cp /opt/user/frappe-dev.conf /opt/user/conf.d/frappe-dev.conf
    else
        if [[ -f '/opt/user/conf.d/frappe-bench-frappe-web.fm.supervisor.conf' ]]; then

            ln -sfn /workspace/frappe-bench/config/frappe-bench-frappe-web.fm.supervisor.conf /opt/user/conf.d/frappe-bench-frappe-web.fm.supervisor.conf
        else
            emer 'Not able to start the server. /opt/user/conf.d/frappe-bench-frappe-web.fm.supervisor.conf not found.'
        fi
    fi

    if [[ -n "$BENCH_START_OFF" ]]; then
        tail -f /dev/null
    else
        echo "Starting supervisor.."
        supervisord -c /opt/user/supervisord.conf
    fi

fi
