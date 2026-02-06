#!/bin/bash

# Color output functions
print_in_color() {
    local color="$1"
    shift
    if [[ -z ${NO_COLOR+x} ]]; then
        printf "$color%b\e[0m\n" "$*"
    else
        printf "%b\n" "$*"
    fi
}

red() { print_in_color "\e[31m" "$*"; }
green() { print_in_color "\e[32m" "$*"; }
yellow() { print_in_color "\e[33m" "$*"; }
cyan() { print_in_color "\e[36m" "$*"; }
info_red() { echo "$(red 'X') $(red "$*")"; }
info_green() { echo "$(cyan '=>') $(green "$*")"; }
info_yellow() { echo "$(cyan '=>') $(yellow "$*")"; }
info_blue() { print_in_color "\e[34m" "$*"; }

Prequisites() {
    # Check if running as root
    if [ "$(id -u)" -eq 0 ]; then
        info_red "Tests should not be run as root"
        exit 30
    fi

    # Detect OS and validate support
    OS="$(uname)"
    if [ "$OS" == "Linux" ]; then
        . /etc/os-release
        if [ "$NAME" != "Ubuntu" ]; then
            info_red "Unsupported Linux distribution. Tests only support Ubuntu."
            exit 1
        fi
    elif [ "$OS" == "Darwin" ]; then
        info_yellow "Running on macOS"
    else
        info_red "Unsupported operating system. Tests only support Ubuntu and macOS."
        exit 1
    fi

    # Check for required commands
    local missing_deps=()
    for cmd in git python3 docker; do
        if ! command -v "$cmd" >/dev/null 2>&1; then
            missing_deps+=("$cmd")
        fi
    done

    # Check docker compose specifically
    if ! has_docker_compose; then
        missing_deps+=("docker compose")
    fi

    if [ ${#missing_deps[@]} -ne 0 ]; then
        info_red "Missing required dependencies: ${missing_deps[*]}"
        exit 56
    fi

    # Check Docker version
    if ! check_docker_version; then
        info_yellow "Docker version may be outdated. Recommended version 20.10.0 or higher"
    fi

    info_green "All prerequisites met"
}

# Helper function for docker compose check
has_docker_compose() {
    if command -v docker &>/dev/null; then
        if docker compose version &>/dev/null; then
            return 0
        fi
    fi
    return 1
}

# Helper function for docker version check
check_docker_version() {
    local required_version="20.10.0"
    if command -v docker >/dev/null 2>&1; then
        local current_version=$(docker version --format '{{.Server.Version}}' 2>/dev/null)
        if [ $? -eq 0 ] && [ "$(printf '%s\n' "$required_version" "$current_version" | sort -V | head -n1)" = "$required_version" ]; then
            return 0
        fi
    fi
    return 1
}

RemoveDanglingDockerStuff() {
    info_yellow "Cleaning up Docker resources..."
    docker volume rm -f $(docker volume ls -q) 2>/dev/null || info_yellow "No volumes to remove"
    docker rm -f $(docker ps -aq) 2>/dev/null || info_yellow "No containers to remove"
    docker network rm -f $(docker network ls -q) 2>/dev/null || info_yellow "No networks to remove"
    info_green "Docker cleanup completed"
}
