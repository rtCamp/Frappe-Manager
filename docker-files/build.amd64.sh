#!/usr/bin/env bash

docker build --push --platform linux/amd64 -t xieytx/fm-nginx:amd64 docker-images/nginx-docker/.
docker build --push --platform linux/amd64 -t xieytx/fm-mailhog:amd64 docker-images/mailhog-docker/.
docker build --push --platform linux/amd64 -t xieytx/fm-rqdashboard:amd64 docker-images/rq-dashboard-docker/.
docker build --push --platform linux/amd64 -t xieytx/fm-frappe:amd64 docker-images/frappe-docker/.
