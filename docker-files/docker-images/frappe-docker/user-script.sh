#!/usr/bin/env bash
# This script creates bench and executes it.
emer() {
    echo "$@"
    exit 1
}

if [[ ! -d 'logs'  ]]; then
    mkdir -p logs
fi

REDIS_SOCKETIO_PORT=9000
WEB_PORT=80
MARIADB_ROOT_PASS='root'

# if the bench doesn't exists
if [[ ! -d "frappe-bench" ]]; then

    [[ "${SITENAME:-}" ]] || emer "[ERROR] SITENAME env not found. Please provide SITENAME env."
    [[ "${REDIS_SOCKETIO_PORT:-}" ]] || emer "[ERROR] REDIS_SOCKETIO_PORT env not found. Please provide REDIS_SOCKETIO_PORT env."
    [[ "${DEVELOPER_MODE:-}" ]] || emer "[ERROR] DEVELOPER_MODE env not found. Please provide DEVELOPER_MODE env."
    [[ "${WEB_PORT:-}" ]] || emer "[ERROR] WEB_PORT env not found. Please provide WEB_PORT env."
    [[ "${MARIADB_ROOT_PASS:-}" ]] || emer "[ERROR] MARIADB_ROOT_PASS env not found. Please provide MARIADB_ROOT_PASS env."
    [[ "${ADMIN_PASS:-}" ]] || emer "[ERROR] ADMIN_PASS env not found. Please provide ADMIN_PASS env."
    [[ "${DB_NAME:-}" ]] || emer "[ERROR] DB_NAME env not found. Please provide DB_NAME env."

    # create the bench
    bench init --skip-assets --skip-redis-config-generation --frappe-branch "$FRAPPE_BRANCH" frappe-bench

    # setting configuration
    wait-for-it -t 120 mariadb:3306
    wait-for-it -t 120 redis-cache:6379
    wait-for-it -t 120 redis-queue:6379
    wait-for-it -t 120 redis-socketio:6379

    cd frappe-bench

    bench config dns_multitenant on
    bench set-config -g db_host 'mariadb'
    bench set-config -g db_port 3306
    bench set-config -g redis_cache 'redis://redis-cache:6379'
    bench set-config -g redis_queue 'redis://redis-queue:6379'
    bench set-config -g redis_socketio 'redis://redis-socketio:6379'
    bench set-config -g socketio_port "$REDIS_SOCKETIO_PORT"
    bench set-config -g mail_port 1025
    bench set-config -g mail_server 'mailhog'
    bench set-config -g disable_mail_smtp_authentication 1
    bench set-config -g webserver_port "$WEB_PORT"
    bench set-config -g developer_mode "$DEVELOPER_MODE"
    # HANDLE APPS
    # apps are taken as follows
    # appsname:branch
    # no branch if you want to do default installation of the app

    apps_json='[]'
    if [[ "${APPS_LIST:-}" ]]; then
        apps=$(awk -F ',' '{for (i=1; i<=NF; i++) {print $i}}' <<<"$APPS_LIST")
        for app in $apps; do
            app_name=$(echo "$app" | awk 'BEGIN {FS=":"}; {print $1}')
            branch_name=$(echo "$app" | awk 'BEGIN {FS=":"}; {print $2}')
            if [[ "${branch_name:-}" ]]; then
                echo "Installing app $app_name -> $branch_name"
                bench get-app --skip-assets --branch "${branch_name}" "${app_name}"
            else
                echo "Installing app $app_name"
                bench get-app --skip-assets "${app_name}"
            fi
            apps_json=$(echo "$apps_json" | jq --arg app_name "${app_name}" '.+ [$app_name]')
            done
    fi
    apps_json=$(echo "$apps_json" | jq -rc '.')

    # add install apps config to common site config
    bench set-config -g install_apps "$apps_json" --parse

    # change the procfile port 8000 to 80
    # this will chaange the web serving port from 8000 to 80

    awk -v a="$WEB_PORT" '{sub(/--port [[:digit:]]+/,"--port "a); print}' Procfile >> Procfile.local_setup


    bench build
    bench new-site --db-root-password "$MARIADB_ROOT_PASS" --db-name "$DB_NAME"  --no-mariadb-socket --admin-password "$ADMIN_PASS"  "$SITENAME"
    bench --site "$SITENAME" scheduler enable

    wait

    if [[ -n "$BENCH_START_OFF" ]]; then
        tail -f /dev/null
    else
        supervisord -c /opt/user/supervisord.conf
    fi


else
    wait-for-it -t 120 mariadb:3306;
    wait-for-it -t 120 redis-cache:6379;
    wait-for-it -t 120 redis-queue:6379;
    wait-for-it -t 120 redis-socketio:6379;

    cd frappe-bench

    if [[ ! -f "Procfile.local_setup" ]]; then

        if [[ ! -f "Procfile" ]]; then
            echo "Procfile doesn't exist. Please create it so bench start can work."
            exit 1
        fi

        awk -v a="$WEB_PORT" '{sub(/--port [[:digit:]]+/,"--port "a); print}' Procfile >> Procfile.local_setup
        bench set-config -g webserver_port "$WEB_PORT";
    fi

    wait

    if [[ -n "$BENCH_START_OFF" ]]; then
        tail -f /dev/null
    else
        supervisord -c /opt/user/supervisord.conf
    fi

fi

