#!/usr/bin/env bash
set -e

ARCH=$(uname -m)
IMAGE_NAME_PREFIX="ghcr.io/rtcamp/frappe-manager"
PUSH=${PUSH:-false}

# List of images to build
	IMAGES=("frappe" "nginx")

if [[ "${ARCH}" == "x86_64" ]]; then
	ARCH="amd64"
	OTHER_ARCH="arm64"
elif [[ "${ARCH}" == "arm64" ]]; then
	OTHER_ARCH="amd64"
fi

# Get version from Python package (pyproject.toml)
FM_VERSION=$(python3 -c "import importlib.metadata; print(importlib.metadata.version('frappe-manager'))" 2>/dev/null || echo "")

	if [[ -z "${FM_VERSION}" ]]; then
	echo "Error: Could not get frappe-manager version. Is the package installed?"
	echo "Run 'pip install -e .' first to install in development mode."
	exit 1
fi

# Prepend 'v' to version for Docker tag
DOCKER_TAG="v${FM_VERSION}"

echo "Building Frappe Manager Docker images"
echo "  Version: ${FM_VERSION}"
echo "  Docker Tag: ${DOCKER_TAG}"
echo "  Architecture: ${ARCH}"
echo ""

for image in "${IMAGES[@]}"; do
	IMAGE_NAME="${IMAGE_NAME_PREFIX}-${image}"
	IMAGE_NAME_WITH_TAG="${IMAGE_NAME}:${DOCKER_TAG}"
	CONTEXT_DIR="${image}"

	echo "Building ${IMAGE_NAME_WITH_TAG} for platform linux/${ARCH}"

	if [[ "${PUSH}" == "true" ]]; then
		docker build --push --platform "linux/${ARCH}" -t "${IMAGE_NAME}:${ARCH}-${DOCKER_TAG}" "${CONTEXT_DIR}"

		echo "Creating manifest for ${IMAGE_NAME}:${DOCKER_TAG}"
		rm -rf ~/.docker/manifests
		docker manifest create "${IMAGE_NAME}:${DOCKER_TAG}" \
			--amend "${IMAGE_NAME}:${ARCH}-${DOCKER_TAG}" \
			--amend "${IMAGE_NAME}:${OTHER_ARCH}-${DOCKER_TAG}"
		docker manifest push "${IMAGE_NAME}:${DOCKER_TAG}"
	else
		docker build --platform "linux/${ARCH}" -t "${IMAGE_NAME_WITH_TAG}" "${CONTEXT_DIR}"
		echo "Built ${IMAGE_NAME_WITH_TAG} (local only, not pushed)"
	fi
done

echo ""
echo "Build complete!"
echo "  Version: ${FM_VERSION}"
echo "  Docker Tag: ${DOCKER_TAG}"
if [[ "${PUSH}" == "true" ]]; then
	echo "Images pushed to registry"
else
	echo "Images built locally. Set PUSH=true to push to registry"
fi
