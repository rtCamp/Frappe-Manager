#!/usr/bin/env bash
TAG='v0.8.2'
ARCH=$(uname -m)

# arm64
if [[ "${ARCH}" == 'arm64' ]]; then
    echo "Building arm64 Images with tag arm64-${TAG}"
    docker build --push --platform linux/arm64 -t xieytx/fm-nginx:arm64-"${TAG}" nginx/.
    docker build --push --platform linux/arm64 -t xieytx/fm-mailhog:arm64-"${TAG}" mailhog/.
    docker build --push --platform linux/arm64 -t xieytx/fm-frappe:arm64-"${TAG}" frappe/.
fi

#amd64
if [[ "${ARCH}" == 'x86_64' ]]; then
    echo "Building arm64 Images with tag amd64-${TAG}"
    docker build --push --platform linux/amd64 -t xieytx/fm-nginx:amd64-"${TAG}" nginx/.
    docker build --push --platform linux/amd64 -t xieytx/fm-mailhog:amd64-"${TAG}" mailhog/.
    docker build --push --platform linux/amd64 -t xieytx/fm-frappe:amd64-"${TAG}" frappe/.

    echo "Combining arm64 and amd64 tags.."
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
fi
