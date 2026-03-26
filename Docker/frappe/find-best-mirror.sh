#!/bin/bash
set -e

ARCH=$(dpkg --print-architecture)
UBUNTU_CODENAME=${UBUNTU_CODENAME:-jammy}

echo "Finding best mirror for architecture: $ARCH"

test_mirror() {
	local mirror=$1
	local release_url="$mirror/dists/${UBUNTU_CODENAME}/Release"

	# Test if mirror responds and has valid Release file (HTTP 200)
	local http_code=$(curl -s -o /dev/null -w "%{http_code}" "$release_url" 2>/dev/null || echo "000")
	if [ "$http_code" != "200" ]; then
		echo "99999 $mirror"
		return
	fi

	local time_total=$(curl -s -o /dev/null -w "%{time_total}" "$release_url" 2>/dev/null || echo "99999")
	echo "$time_total $mirror"
}

export -f test_mirror
export UBUNTU_CODENAME

if [ "$ARCH" = "amd64" ]; then
	echo "Testing AMD64 mirrors..."

	MIRRORS=$(curl -s http://mirrors.ubuntu.com/mirrors.txt)

	BEST_RESULT=$(echo "$MIRRORS" |
		xargs -P 10 -I {} bash -c 'test_mirror "{}/ubuntu"' |
		sort -n |
		head -1)
	BEST_LATENCY=$(echo "$BEST_RESULT" | awk '{print $1}')
	BEST_MIRROR=$(echo "$BEST_RESULT" | awk '{print $2}')

	DEFAULT_MIRROR="http://archive.ubuntu.com/ubuntu"

elif [ "$ARCH" = "arm64" ] || [ "$ARCH" = "armhf" ]; then
	echo "Testing ARM64 mirrors..."

	MIRRORS="http://ports.ubuntu.com/ubuntu-ports
http://mirror.enzu.com/ubuntu-ports
http://mirror.math.princeton.edu/pub/ubuntu-ports
http://mirror.pit.teraswitch.com/ubuntu-ports
http://ftp.osuosl.org/pub/ubuntu-ports
http://mirrors.wikimedia.org/ubuntu-ports
http://mirror.aarnet.edu.au/pub/ubuntu/ports
http://ftp.kaist.ac.kr/ubuntu-ports"

	BEST_RESULT=$(echo "$MIRRORS" |
		xargs -P 10 -I {} bash -c 'test_mirror "{}"' |
		sort -n |
		head -1)
	BEST_LATENCY=$(echo "$BEST_RESULT" | awk '{print $1}')
	BEST_MIRROR=$(echo "$BEST_RESULT" | awk '{print $2}')

	DEFAULT_MIRROR="http://ports.ubuntu.com/ubuntu-ports"
else
	echo "Unknown architecture: $ARCH"
	exit 1
fi

if [ -z "$BEST_MIRROR" ] || [ "$BEST_LATENCY" = "99999" ]; then
	echo "Warning: Could not find working mirror, using default: $DEFAULT_MIRROR"
	BEST_MIRROR="$DEFAULT_MIRROR"
else
	RELEASE_URL="$BEST_MIRROR/dists/${UBUNTU_CODENAME}/Release"
	HTTP_CODE=$(curl -s -o /dev/null -w "%{http_code}" "$RELEASE_URL" 2>/dev/null || echo "000")
	if [ "$HTTP_CODE" != "200" ]; then
		echo "Warning: Selected mirror $BEST_MIRROR returned HTTP $HTTP_CODE, falling back to default: $DEFAULT_MIRROR"
		BEST_MIRROR="$DEFAULT_MIRROR"
	fi
fi

echo "Selected mirror: $BEST_MIRROR"

if [ "$ARCH" = "amd64" ]; then
	sed -i "s|http://archive.ubuntu.com/ubuntu|${BEST_MIRROR}|g" /etc/apt/sources.list
	sed -i "s|http://security.ubuntu.com/ubuntu|${BEST_MIRROR}|g" /etc/apt/sources.list
elif [ "$ARCH" = "arm64" ] || [ "$ARCH" = "armhf" ]; then
	sed -i "s|http://ports.ubuntu.com/ubuntu-ports|${BEST_MIRROR}|g" /etc/apt/sources.list
fi

echo "Successfully updated /etc/apt/sources.list with best mirror"
