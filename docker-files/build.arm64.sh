#!/usr/bin/env bash
TAG='v0.7.0'

docker build --push --platform linux/arm64 -t xieytx/fm-nginx:arm64-"${TAG}" docker-images/nginx-docker/.
docker build --push --platform linux/arm64 -t xieytx/fm-mailhog:arm64-"${TAG}" docker-images/mailhog-docker/.
docker build --push --platform linux/arm64 -t xieytx/fm-frappe:arm64-"${TAG}" docker-images/frappe-docker/.
