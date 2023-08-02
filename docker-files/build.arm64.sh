#!/usr/bin/env bash

docker build --push --platform linux/arm64 -t xieytx/fm-nginx:arm64 docker-images/nginx-docker/.
docker build --push --platform linux/arm64 -t xieytx/fm-mailhog:arm64 docker-images/mailhog-docker/.
docker build --push --platform linux/arm64 -t xieytx/fm-rqdashboard:arm64 docker-images/rq-dashboard-docker/.
docker build --push --platform linux/arm64 -t xieytx/fm-frappe:arm64 docker-images/frappe-docker/.
