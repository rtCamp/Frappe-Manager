#!/usr/bin/env bash
# This script creates bench and executes it.

if [[ ! -d 'logs'  ]]; then
    mkdir -p logs
fi

if [[ -z "$FRAPPE_BRANCH" ]]; then
    FRAPPE_BRANCH='version-14'
fi

if [[ -z "$FRAPPE_ADMIN_PASS" ]]; then
    ADMIN_PASS='admin'
fi

if [[ -z "$MARIADB_ROOT_PASS" ]]; then
    MARIADB_ROOT_PASS='root'
fi

if [[ -z "$REDIS_SOCKETIO_PORT" ]]; then
    REDIS_SOCKETIO_PORT=9000
fi

if [[ -z "$FRAPPE_WEB_PORT" ]]; then
    WEB_PORT=80
fi

if [[ -z "$FRAPPE_DEVELOPER_MODE" ]]; then
    DEVELOPER_MODE=0
fi

# if the bench doesn't exists
if [[ ! -d "frappe-bench" ]]; then
    # create the bench
    bench init --skip-assets --skip-redis-config-generation --frappe-branch "$FRAPPE_BRANCH" frappe-bench


    # setting configuration
    wait-for-it -t 120 mariadb:3306
    wait-for-it -t 120 redis-cache:6379
    wait-for-it -t 120 redis-queue:6379
    wait-for-it -t 120 redis-socketio:6379

    ls -lah

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

    # change the procfile port 8000 to 80
    # this will chaange the web serving port from 8000 to 80

    awk -v a="$WEB_PORT" '{sub(/--port [[:digit:]]+/,"--port "a); print}' Procfile >> Procfile.local_setup


    bench build

    bench new-site --db-root-password "$MARIADB_ROOT_PASS" --no-mariadb-socket --admin-password "$ADMIN_PASS"  mysite.localhost

    bench --site mysite.localhost scheduler enable

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
            echo "Profile doesn't exist. Please create it so bench start can work."
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
