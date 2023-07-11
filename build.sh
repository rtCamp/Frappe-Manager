#!/usr/bin/env bash

if [[ ! "$(docker images -q frappe-local-nginx)" == "" ]]; then
    echo "BUILDER [NOT BUILDING]: Image frappe-local-nginx already exists"
else
    echo "BUILDER [BUILDING]: Image frappe-local-nginx"
    docker build -t frappe-local-nginx docker-images/nginx-docker/.
fi

if [[ ! "$(docker images -q frappe-local-mailhog)" == "" ]]; then
    echo "BUILDER [NOT BUILDING]: Image frappe-local-mailhog already exists"
else
    echo "BUILDER [BUILDING]: Image frappe-local-mailhog"
    docker build -t frappe-local-mailhog docker-images/mailhog-docker/.
fi

if [[ ! "$(docker images -q frappe-local-rqdashboard)" == "" ]]; then
    echo "BUILDER [NOT BUILDING]: Image frappe-local-rqdashboard already exists"
else
    echo "BUILDER [BUILDING]: Image frappe-local-rqdashboard"
    docker build -t frappe-local-rqdashboard docker-images/rq-dashboard-docker/.
fi

if [[ ! "$(docker images -q frappe-local-frappe)" == "" ]]; then
    echo "BUILDER [NOT BUILDING]: Image frappe-local-frappe already exists"
else
    echo "BUILDER [BUILDING]: Image frappe-local-frappe"
    docker build -t frappe-local-frappe docker-images/frappe-docker/.
fi
