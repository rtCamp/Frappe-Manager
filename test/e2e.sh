#!/bin/bash
PS4='+\[\033[0;33m\](\[\033[0;36m\]${BASH_SOURCE##*/}:${LINENO}\[\033[0;33m\])\[\033[0m\] '

set -xe

echo "Frappe executing $PWD"

# sudo apt update && sudo apt upgrade -y
# sudo add-apt-repository ppa:deadsnakes/ppa
# sudo apt update && sudo apt install python3.12 python3.12-venv -y
# python3.12 --version

# python3.12 -m venv env

source env/bin/activate # make sure the envirnment variable is set

cleanup() {
    python -m pip uninstall -y frappe_manager
    rm -rf ~/frappe
}

installFromTarGz() {
    local tarGzFile="$1"
    python -m pip install --upgrade $tarGzFile
}

isRoot() {
    if [ "$(id -u)" -eq 0 ]; then
        info_red "You are running as root."
        exit 30
    fi
}

createSites() {
	local siteName="$1"
	echo "Create SiteName: $siteName"
	fm create $siteName.prod.local --env prod
	fm create $siteName.dev.local --env dev
}

listSites() {
    fm list
}

validation() {
    local siteName="$1"
    curl -f --head -H "Host: $1.dev.local" http://localhost:80 || echo "Failed to curl $1.dev.local"
    curl -f --head -H "Host: $1.prod.local" http://localhost:80 || echo "Failed to curl $1.prod.local"
}

infoSites() {
    local siteName="$1"
	echo "Create SiteName: $siteName"
    fm info $1.prod.local
    fm info $1.dev.local
}

deleteSites() {
	local siteName="$1"
	echo "Delete SiteName: $siteName"
	fm delete $siteName.prod.local # TODO: need to add -y/--yes flag
	fm delete $siteName.dev.local # TODO: need to add -y/--yes flag

	docker volume rm -f $(docker volume ls -q) || echo "Failed to delete dangling docker volume"
    docker rm -f $(docker ps -aq) || echo "Failed to delete dangling docker container"
    docker network rm -f $(docker network ls -q) || echo "Failed to dangling delete docker network"
}

main() {
	isRoot
	cleanup
	installFromTarGz "frappe_manager-0.14.0.tar.gz"
	createSites "test-site"
	listSites
	validation "test-site"
	infoSites "test-site"
	deleteSites "test-site"
}

time main
