#!/usr/bin/env bash
set -e

ARCH=$(uname -m)
IMAGE_NAME_PREFIX="ghcr.io/rtcamp/frappe-manager"
PUSH=${PUSH:-false}

if [[ "${ARCH}" == "x86_64" ]]; then
	ARCH="amd64"
	OTHER_ARCH="arm64"
elif [[ "${ARCH}" == "arm64" ]]; then
	OTHER_ARCH="amd64"
fi

images=$(jq -rc '. | keys[]' images-tag.json)

for image in ${images}; do
	IMAGE_TAG=$(jq -rc ".${image}" images-tag.json)

	if [[ -z "${IMAGE_TAG}" ]]; then
		echo "No tag found for ${image}, skipping"
		continue
	fi

	IMAGE_NAME="${IMAGE_NAME_PREFIX}-${image}"
	IMAGE_NAME_WITH_TAG="${IMAGE_NAME}:${IMAGE_TAG}"
	CONTEXT_DIR="${image}"

	echo "Building ${IMAGE_NAME_WITH_TAG} for platform linux/${ARCH}"

	if [[ "${PUSH}" == "true" ]]; then
		docker build --push --platform "linux/${ARCH}" -t "${IMAGE_NAME}:${ARCH}-${IMAGE_TAG}" "${CONTEXT_DIR}"

		echo "Creating manifest for ${IMAGE_NAME}:${IMAGE_TAG}"
		rm -rf ~/.docker/manifests
		docker manifest create "${IMAGE_NAME}:${IMAGE_TAG}" \
			--amend "${IMAGE_NAME}:${ARCH}-${IMAGE_TAG}" \
			--amend "${IMAGE_NAME}:${OTHER_ARCH}-${IMAGE_TAG}"
		docker manifest push "${IMAGE_NAME}:${IMAGE_TAG}"
	else
		docker build --platform "linux/${ARCH}" -t "${IMAGE_NAME_WITH_TAG}" "${CONTEXT_DIR}"
		echo "Built ${IMAGE_NAME_WITH_TAG} (local only, not pushed)"
	fi
done

echo ""
echo "Build complete!"
if [[ "${PUSH}" == "true" ]]; then
	echo "Images pushed to registry"
else
	echo "Images built locally. Set PUSH=true to push to registry"
fi
