#!/usr/bin/env bash
TAG='v0.8.3'
ARCH=$(uname -m)

# arm64
if [[ "${ARCH}" == 'arm64' ]]; then
    echo "Building arm64 Images with tag arm64-${TAG}"
    docker build --push --platform linux/arm64 -t ghcr.io/rtcamp/frappe-manager-nginx:arm64-"${TAG}" nginx/.
    docker build --push --platform linux/arm64 -t ghcr.io/rtcamp/frappe-manager-mailhog:arm64-"${TAG}" mailhog/.
    docker build --push --platform linux/arm64 -t ghcr.io/rtcamp/frappe-manager-frappe:arm64-"${TAG}" frappe/.
fi

#amd64
if [[ "${ARCH}" == 'x86_64' ]]; then
    echo "Building amd64 Images with tag amd64-${TAG}"
    docker build --push --platform linux/amd64 -t ghcr.io/rtcamp/frappe-manager-nginx:amd64-"${TAG}" nginx/.
    docker build --push --platform linux/amd64 -t ghcr.io/rtcamp/frappe-manager-mailhog:amd64-"${TAG}" mailhog/.
    docker build --push --platform linux/amd64 -t ghcr.io/rtcamp/frappe-manager-frappe:amd64-"${TAG}" frappe/.

    echo "Combining arm64 and amd64 tags.."
    rm -rf ~/.docker/manifests

    docker manifest create ghcr.io/rtcamp/frappe-manager-nginx:"$TAG" \
    --amend ghcr.io/rtcamp/frappe-manager-nginx:amd64-"$TAG" \
    --amend ghcr.io/rtcamp/frappe-manager-nginx:arm64-"$TAG"

    docker manifest push ghcr.io/rtcamp/frappe-manager-nginx:"$TAG"

    docker manifest create ghcr.io/rtcamp/frappe-manager-mailhog:"$TAG" \
    --amend ghcr.io/rtcamp/frappe-manager-mailhog:amd64-"$TAG" \
    --amend ghcr.io/rtcamp/frappe-manager-mailhog:arm64-"$TAG"

    docker manifest push ghcr.io/rtcamp/frappe-manager-mailhog:"$TAG"

    docker manifest create ghcr.io/rtcamp/frappe-manager-frappe:"$TAG" \
    --amend ghcr.io/rtcamp/frappe-manager-frappe:amd64-"$TAG" \
    --amend ghcr.io/rtcamp/frappe-manager-frappe:arm64-"$TAG"

    docker manifest push ghcr.io/rtcamp/frappe-manager-frappe:"$TAG"
fi
