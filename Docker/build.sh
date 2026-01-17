#!/usr/bin/env bash
set -x
ARCH=$(uname -m)

IMAGE_NAME_PREFIX="ghcr.io/rtcamp/frappe-manager"
COMMAND='docker build --push'

# OCI description for the multi-arch manifest (index). Override by exporting IMAGE_DESCRIPTION before running.
IMAGE_DESCRIPTION=${IMAGE_DESCRIPTION:-"Frappe Manager multi-arch image with Bench tooling and pre-baked Frappe/ERPNext apps"}

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
            echo "Combining architectures into multi-arch manifest for ${IMAGE_NAME}:${IMAGE_TAG}"
            rm -rf ~/.docker/manifests || true

            if docker buildx imagetools create --help > /dev/null 2>&1; then
                echo "Using buildx imagetools with OCI description annotation"
                if ! docker buildx imagetools create \
                    --tag "${IMAGE_NAME}:${IMAGE_TAG}" \
                    --annotation "org.opencontainers.image.description=${IMAGE_DESCRIPTION}" \
                    "${IMAGE_NAME_WITH_TAG}" "${IMAGE_NAME}:${OTHER_ARCH}-${IMAGE_TAG}"; then
                    echo "[WARN] buildx imagetools create failed (maybe other arch not yet pushed). Falling back to docker manifest create without annotation." >&2
                    docker manifest create "${IMAGE_NAME}:${IMAGE_TAG}" \
                        --amend "${IMAGE_NAME_WITH_TAG}" \
                        --amend "${IMAGE_NAME}:${OTHER_ARCH}-${IMAGE_TAG}" || true
                fi
            else
                echo "[WARN] docker buildx imagetools not available; using docker manifest create (no description annotation)." >&2
                docker manifest create "${IMAGE_NAME}:${IMAGE_TAG}" \
                    --amend "${IMAGE_NAME_WITH_TAG}" \
                    --amend "${IMAGE_NAME}:${OTHER_ARCH}-${IMAGE_TAG}" || true
            fi
        fi

    fi
done
