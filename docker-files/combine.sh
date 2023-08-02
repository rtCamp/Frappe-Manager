#!/bin/bash
rm -rf ~/.docker/manifests
docker manifest create xieytx/fm-nginx:latest \
--amend xieytx/fm-nginx:amd64 \
--amend xieytx/fm-nginx:arm64

docker manifest push xieytx/fm-nginx:latest

docker manifest create xieytx/fm-mailhog:latest \
--amend xieytx/fm-mailhog:amd64 \
--amend xieytx/fm-mailhog:arm64

docker manifest push xieytx/fm-mailhog:latest

docker manifest create xieytx/fm-rqdashboard:latest \
--amend xieytx/fm-rqdashboard:amd64 \
--amend xieytx/fm-rqdashboard:arm64

docker manifest push xieytx/fm-rqdashboard:latest

docker manifest create xieytx/fm-frappe:latest \
--amend xieytx/fm-frappe:amd64 \
--amend xieytx/fm-frappe:arm64

docker manifest push xieytx/fm-frappe:latest
