#!/usr/bin/env bash

docker build --push --platform linux/arm64,linux/amd64 -t xieytx/fm-nginx docker-images/nginx-docker/.
docker build --push --platform linux/arm64,linux/amd64 -t xieytx/fm-mailhog docker-images/mailhog-docker/.
docker build --push --platform linux/arm64,linux/amd64 -t xieytx/fm-rqdashboard docker-images/rq-dashboard-docker/.
docker build --push --platform linux/arm64,linux/amd64 -t xieytx/fm-frappe docker-images/frappe-docker/.
