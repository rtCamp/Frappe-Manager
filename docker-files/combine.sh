#!/bin/bash
TAG='v0.7.0'

rm -rf ~/.docker/manifests

docker manifest create xieytx/fm-nginx:"$TAG" \
--amend xieytx/fm-nginx:amd64-"$TAG" \
--amend xieytx/fm-nginx:arm64-"$TAG"

docker manifest push xieytx/fm-nginx:"$TAG"

docker manifest create xieytx/fm-mailhog:"$TAG" \
--amend xieytx/fm-mailhog:amd64-"$TAG" \
--amend xieytx/fm-mailhog:arm64-"$TAG"

docker manifest push xieytx/fm-mailhog:"$TAG"

docker manifest create xieytx/fm-frappe:"$TAG" \
--amend xieytx/fm-frappe:amd64-"$TAG" \
--amend xieytx/fm-frappe:arm64-"$TAG"

docker manifest push xieytx/fm-frappe:"$TAG"
