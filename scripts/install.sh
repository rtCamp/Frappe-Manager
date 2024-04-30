#!/bin/bash
PS4='+\[\033[0;33m\](\[\033[0;36m\]${BASH_SOURCE##*/}:${LINENO}\[\033[0;33m\])\[\033[0m\] '

set -xe

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
blue() { print_in_color "\e[34m" "$*"; }
magenta() { print_in_color "\e[35m" "$*"; }
cyan() { print_in_color "\e[36m" "$*"; }
bold() { print_in_color "\e[1m" "$*"; }
underlined() { print_in_color "\e[4m" "$*"; }

info_blue(){
    echo -e $'\U0001F6A7' "$(blue "$*")"
}

info_green(){
    echo "$(cyan '=>') $(green "$*")"
}

info_red(){
    echo "$(red 'X') $(red "$*")"
}

isRoot() {
    if [ "$(id -u)" -eq 0 ]; then
        info_red "You are running as root."
        exit 69
    fi
}

has_docker_compose(){
    declare desc="return 0 if we have docker compose command"
    if [[ "$(dockexr compose version 2>&1 || true)" = *"docker: 'compose' is not a docker command."* ]]; then
        return 1
    else
        return 0
    fi
}

has_pyenv(){
    declare desc="return 0 if we have docker compose command"
    if [[ "$(pyenv --version 2>&1 || true)" = *"pyenv: command not found"* ]]; then
        return 1
    else
        return 0
    fi
}

has_tty() {
    declare desc="return 0 if we have a tty"
    if [[ "$(/usr/bin/tty || true)" == "not a tty" ]]; then
        return 1
    else
        return 0
    fi
}
if has_tty; then
    if ! [[ "${INTERACTIVE:-}" ]]; then
        export DEBIAN_FRONTEND=noninteractive
    fi
else
    export DEBIAN_FRONTEND=noninteractive
fi

if [[ "${DEBUG:-}" -eq 1 ]]; then
    set -x
fi

# Function to install Homebrew on macOS
install_homebrew() {
    if ! type brew > /dev/null 2>&1; then
        NONINTERACTIVE=1 /bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"    else
    else
        info_green "brew is already installed."
    fi
}

# Function to check and install Docker Desktop on macOS
install_docker_macos() {
    if ! type docker > /dev/null 2>&1; then
        info_blue "Installing Docker Desktop for macOS..."
        install_homebrew
        brew install --cask docker
        # The Docker app needs to be opened to complete the installation, which can't be fully automated
        info_green "Docker Desktop installed."
    else
        info_green "Docker Desktop is already installed."
    fi
}

# Function to check and install Docker Engine and Docker Compose on Ubuntu
install_docker_ubuntu() {
    ARCH=$(dpkg --print-architecture)  # Detects the architecture (amd64, arm64, etc.)
    if command -v docker > /dev/null; then
        info_green "Docker is already installed."
    else
        info_blue "Installing Docker Engine for Ubuntu..."

        # Add Docker's official GPG key:
        sudo DEBIAN_FRONTEND=noninteractive apt-get update
        sudo DEBIAN_FRONTEND=noninteractive apt-get install -y ca-certificates curl
        sudo DEBIAN_FRONTEND=noninteractive install -m 0755 -d /etc/apt/keyrings
        sudo curl -fsSL https://download.docker.com/linux/ubuntu/gpg -o /etc/apt/keyrings/docker.asc
        sudo DEBIAN_FRONTEND=noninteractive chmod a+r /etc/apt/keyrings/docker.asc

        # Add the repository to Apt sources:
        echo \
            "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.asc] https://download.docker.com/linux/ubuntu \
            $(. /etc/os-release && echo "$VERSION_CODENAME") stable" | \
            sudo tee /etc/apt/sources.list.d/docker.list > /dev/null
            
            sudo DEBIAN_FRONTEND=noninteractive apt-get update

            sudo DEBIAN_FRONTEND=noninteractive apt-get install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin

            info_green "Docker Engine installed"
    fi

    # Check if the docker group exists, create it if not
    if ! getent group docker > /dev/null; then
        info_green "Docker group does not exist. Creating docker group..."
        sudo groupadd docker
    fi

    # Check if $USER is in the docker group
    if id -nG "$USER" | grep -qw docker; then
        info_green "$USER is already a member of the docker group."
    else
        info_blue "$USER is not a member of the docker group. Adding $USER to the docker group..."
        sudo usermod -aG docker "$USER"
        info_green "$USER has been added to the docker group."
    fi

    if ! has_docker_compose; then
        info_blue "Installing Docker Compose..."
        sudo DEBIAN_FRONTEND=noninteractive apt-get install -y docker-compose-plugin
        info_green "Docker Compose installed."
    else
        info_green "Docker Compose is already installed."
    fi

    sudo systemctl enable docker.service
    sudo systemctl start docker.service
}

install_pyenv_python(){

    if ! type python3 > /dev/null 2>&1 || ! python3 -c 'import sys; assert sys.version_info >= (3,10)' 2>/dev/null; then
        LATEST_PYTHON=$(pyenv install --list | grep -v - | grep -v b | grep -v a | grep -v rc | grep -E '^\s*3' | tail -1 | tr -d '[:space:]')
        if [ -z "$LATEST_PYTHON" ]; then
            info_red "Could not find the latest Python version."
            exit 1
        fi
        info_blue "Latest Python version available is: $LATEST_PYTHON"

        # Check if the latest version is already installed
        if pyenv versions | grep -q "$LATEST_PYTHON"; then
            info_green "Python $LATEST_PYTHON is already installed."
        else
            info_blue "Installing Python $LATEST_PYTHON..."
            # Install the latest Python version. Adjust this line if you need custom configuration or want to handle output differently.
            pyenv install "${LATEST_PYTHON}"
            pyenv global "${LATEST_PYTHON}"
            info_green "Python $LATEST_PYTHON installed."
        fi
    fi

    if ! type pip3 > /dev/null 2>&1; then
        info_blue "Installing pip3..."
        python -m ensurepip --upgrade
        info_green "Installed pip3"
    fi
}

# Function to install Python and frappe-manager on Ubuntu
install_python_and_frappe_ubuntu() {
    if ! has_pyenv; then
        if ! type python3 > /dev/null 2>&1 || ! python3 -c 'import sys; assert sys.version_info >= (3,10)' 2>/dev/null; then
            # is pyenv installed
            info_blue "Using $(yellow 'apt') for installing python3..."
            sudo DEBIAN_FRONTEND=noninteractive apt-get update
            sudo DEBIAN_FRONTEND=noninteractive apt-get install -y python3
            info_green "Installed python3"
        else
            info_green "python 3.10 or higher is already installed."
        fi

        if ! type pip3 > /dev/null 2>&1; then
            info_blue "Using $(yellow 'apt') for installing pip3..."
            sudo DEBIAN_FRONTEND=noninteractive apt-get install -y python3-pip
            info_green "Installed pip3"
        else
            info_green "pip3 already installed."
        fi
    else
        info_blue "Pyenv detected. Using it to install python"
        install_pyenv_python
    fi

    info_blue "Installing frappe-manager..."
    pip3 install --user --upgrade frappe-manager
    info_green "$(bold 'fm' $(pip3 list | grep frappe-manager | awk '{print $2}')) installed."
}

# Function to install Python and frappe-manager on macOS
install_python_and_frappe_macos() {
    if ! has_pyenv; then
        if ! type python3 > /dev/null 2>&1 || ! python3 -c 'import sys; assert sys.version_info >= (3,10)' 2>/dev/null; then
            info_blue "Using $(yellow 'brew') for installing python..."
            brew install python
            info_green "$(python3 -V) installed"
        else
            info_green "python 3.10 or higher is already installed."
        fi

        if ! type pip3 > /dev/null 2>&1; then
            info_blue "Using $(yellow 'brew') for installing pip3..."
            python -m ensurepip --upgrade
            info_green "Installed pip3"
        else
            info_green "pip3 already installed."
        fi
    else
        info_blue "Pyenv detected. Using it to install python"
        install_pyenv_python
    fi

    info_blue "Installing frappe-manager..."
    pip3 install --user frappe-manager
    info_green "$(bold 'fm' $(pip3 list | grep frappe-manager | awk '{print $2}')) installed."
}

handle_shell(){
    local shellrc

    if ! has_pyenv; then
        if [[ "${SHELL:-}" =  *"bash"* ]]; then
            shellrc="${HOME}/.bashrc"
        elif [[ "${SHELL:-}" =  *"zsh"* ]]; then
            shellrc="${HOME}/.zshrc"
        fi

        if [[ "${shellrc:-}" ]]; then
            info_blue "Checking default pip3 dir in PATH"
            if ! [[ "$PATH" = *"$HOME/.local/bin"* ]];then
                export PATH="$HOME/.local/bin:$PATH"
                echo 'export PATH="$HOME/.local/bin:$PATH"' >> "$shellrc"
                info_green "Added $HOME/.local/bin to PATH using $shellrc file."
            else
                info_green "$HOME/.local/bin already in PATH."
            fi
        fi
        info_blue "Installing fm shell completion."
        $HOME/.local/bin/fm --install-completion
    else
        info_blue "Installing fm shell completion."
        export PATH="$(pyenv root)/shims:$PATH"
        fm --install-completion
    fi

}


# Detect OS and call the respective functions
OS="$(uname)"
if [ "$OS" == "Darwin" ]; then
    install_docker_macos
    install_python_and_frappe_macos
    handle_shell
    info_yellow "🔴 Please complete docker desktop setup before using fm."
    osascript -e 'display notification "Please complete docker desktop setup before using fm." with title "🔴 fm - complete docker desktop setup."'
    # start docker app
    open -na /Applications/Docker.app
    info_green "Script execution completed."

elif [ "$OS" == "Linux" ]; then
    . /etc/os-release
    if [ "$NAME" == "Ubuntu" ]; then
        isRoot
        install_docker_ubuntu
        install_python_and_frappe_ubuntu
        handle_shell

        # Function to be executed upon script exit
        function remind_logout() {
            info_green "Please log out and log back from the current shell for linux group changes to take effect."
        }
        # Trap the EXIT signal and call remind_logout function
        trap remind_logout EXIT

        info_green "Script execution completed."
        sudo su - "$USER"
    else
        info_red "Unsupported Linux distribution. This script supports macOS and Ubuntu."
        exit 1
    fi
else
    info_red "Unsupported operating system. This script supports macOS and Ubuntu."
    exit 1
fi
