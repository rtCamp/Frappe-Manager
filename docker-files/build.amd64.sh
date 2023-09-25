#!/usr/bin/env bash
TAG='v0.7.0'

docker build --push --platform linux/amd64 -t xieytx/fm-nginx:amd64-"${TAG}" docker-images/nginx-docker/.
docker build --push --platform linux/amd64 -t xieytx/fm-mailhog:amd64-"${TAG}" docker-images/mailhog-docker/.
docker build --push --platform linux/amd64 -t xieytx/fm-frappe:amd64-"${TAG}" docker-images/frappe-docker/.
