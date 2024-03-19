#!/usr/bin/env bash
set -x
ARCH=$(uname -m)

IMAGE_NAME_PREFIX="ghcr.io/rtcamp/frappe-manager"
COMMAND='docker build --push'

OTHER_ARCH="x86_64"
if [[ "${ARCH}" == "x86_64" ]]; then
    ARCH="amd64"
    OTHER_ARCH="arm64"
fi

# images=$(jq -rc '. | keys[] ' images-tag.json || exit 0)

images='frappe'

for image in ${images}; do
    IMAGE_TAG=$(jq -rc ".${image}" images-tag.json || exit 0)
    if [[ "${IMAGE_TAG:-}" ]]; then

        if [[ "${ARCH}" == 'arm64' ]]; then
            COMMAND+=" --provenance false"
        fi

        IMAGE_NAME="${IMAGE_NAME_PREFIX}-${image}"
        IMAGE_NAME_WITH_TAG="${IMAGE_NAME}:${ARCH}-${IMAGE_TAG}"
        CONTEXT_DIR="${image}/."

        echo "Building ${IMAGE_NAME_WITH_TAG}"

        COMMAND+=" --platform linux/${ARCH} -t ${IMAGE_NAME_WITH_TAG} $CONTEXT_DIR"

        eval "${COMMAND}"
        STATUS="$?"


        if [[ "${STATUS}" -eq 0 ]]; then
            echo "Combining"
            rm -rf ~/.docker/manifests
            docker manifest create "${IMAGE_NAME}:${IMAGE_TAG}" \
            --amend "${IMAGE_NAME_WITH_TAG}" \
            --amend "${IMAGE_NAME}:${OTHER_ARCH}-${IMAGE_TAG}"
        fi

    fi
done
