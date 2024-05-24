#!/bin/bash

Cleanup() {
    python -m pip uninstall -y frappe_manager
    rm -rf ~/frappe
}


# sudo apt update && sudo apt upgrade -y
# sudo add-apt-repository ppa:deadsnakes/ppa
# sudo apt update && sudo apt install python3.12 python3.12-venv -y
# python3.12 --version

# python3.12 -m venv env

InstallFrappe() {
    local tagOrBranch=$1
    if [ -d ~/Frappe-Manager ]; then
        echo "Frappe git is already there already installed. Skipping. clone"
    else
        git clone https://github.com/rtCamp/Frappe-Manager.git
    fi
    cd ~/Frappe-Manager
    git fetch --all
    if [ -n "$tagOrBranch" ]; then
        git checkout $tagOrBranch
    fi
    cd -
    python -m pip install --upgrade Frappe-Manager
    fm --version
}

Prequisites() {
    if [ "$(id -u)" -eq 0 ]; then
        info_red "You are running as root."
        exit 30
    fi

    for n in git python docker docker-compose 'docker compose';
    do
        if ! [ -x "$(command -v $n)" ]; then
            echo "Error: $n is not installed." >&2
            exit 56
        fi
    done
    # TODO(alok): Check for python version
    # also also for the disk space to be more than 50% to be available
}

RemoveDanglingDockerStuff() {
   docker volume rm -f $(docker volume ls -q) || echo "Failed to delete dangling docker volume"
   docker rm -f $(docker ps -aq) || echo "Failed to delete dangling docker container"
   docker network rm -f $(docker network ls -q) || echo "Failed to dangling delete docker network"
}
