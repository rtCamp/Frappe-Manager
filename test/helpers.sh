#!/bin/bash

Prequisites() {
    if [ "$(id -u)" -eq 0 ]; then
        info_red "You are running as root."
        exit 30
    fi

    for n in git python docker docker-compose;
    do
        if ! [ -x "$(command -v $n)" ]; then
            echo "Error: $n is not installed." >&2
            exit 56
        fi
    done
}

RemoveDanglingDockerStuff() {
   docker volume rm -f $(docker volume ls -q) || echo "Failed to delete dangling docker volume"
   docker rm -f $(docker ps -aq) || echo "Failed to delete dangling docker container"
   docker network rm -f $(docker network ls -q) || echo "Done deleting the networks"
}
