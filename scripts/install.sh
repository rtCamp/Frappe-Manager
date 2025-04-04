#!/bin/bash
PS4='+\[\033[0;33m\](\[\033[0;36m\]${BASH_SOURCE##*/}:${LINENO}\[\033[0;33m\])\[\033[0m\] '
LOGFILE="fm-install-$(date +"%Y%m%d_%H%M%S").log"

exec {BASH_XTRACEFD}>>"$LOGFILE"
set -e

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

info_yellow(){
    echo "$(cyan '=>') $(yellow "$*")"
}

info_red(){
    echo "$(red 'X') $(red "$*")"
}

# Cleanup function
cleanup() {
    local exit_code=$?
    #rm -f "$LOGFILE"
    exit $exit_code
}

# Register cleanup function
trap cleanup EXIT

set -x

show_help() {
    cat << EOF
Frappe Manager Installation Script

USAGE:
    As root: $(basename "$0") [username] [--dev] [--force] [--help]
    As non-root: $(basename "$0") [--dev] [--force] [--help]

DESCRIPTION:
    Installs Frappe Manager (fm) and all required dependencies including Docker, 
    Docker Compose, Python 3.10+, and Pip.

ARGUMENTS:
    username    Optional. Sets custom username when running as root (default: 'frappe')
               Only valid when running as root user.

OPTIONS:
    --dev      Install development version from 'develop' branch
    --force    Force all installations and updates, ignoring existing versions
    --help     Show this help message

NOTES:
    - For Ubuntu: You'll need to log out and log back in for Docker group changes to take effect
    - For macOS: You'll need to complete Docker Desktop setup before using fm
    - Creates log file 'fm-install-<timestamp>.log' in current directory

EXAMPLES:
    # Install stable version as root with custom username
    $(basename "$0") myuser

    # Install development version as root with custom username
    $(basename "$0") myuser --dev
    $(basename "$0") --dev myuser

    # Install development version as non-root user
    $(basename "$0") --dev

    # Install stable version as non-root user
    $(basename "$0")
EOF
    exit 0
}

create_user() {
    local username=${1:-frappe}
    
    # Check if user already exists
    if id "$username" >/dev/null 2>&1; then
        info_yellow "User $username already exists"
        return
    fi

    info_blue "Creating user $username..."
    useradd -m -s /bin/bash "$username"
    usermod -aG sudo "$username"
    
    # Set a random password
    local password='frappemanager'
    #local password=$(openssl rand -base64 12)
    echo "$username:$password" | chpasswd
    
    info_green "Created user $username with password: $password"
    info_yellow "Please change this password after installation!"
}

handle_root() {
    if [ "$(id -u)" -eq 0 ]; then
        local username=${1:-frappe}
        info_blue "Running as root, creating user $username..."
        
        create_user "$username"
        
        # Copy the script to the new user's home
        local script_path="/home/$username/install.sh"
        cp "$0" "$script_path"
        chown "$username:$username" "$script_path"
        chmod +x "$script_path"
        
        # Set up environment for non-interactive run
        export SUDO_ASKPASS="/bin/false"  # Prevent graphical password prompts
        export DEBIAN_FRONTEND=noninteractive
        
        # Pass the current flags to the new session
        local flags=""
        [ "$DEVELOPMENT" = true ] && flags="$flags --dev"
        [ "$FORCE" = true ] && flags="$flags --force"
        
        # Re-run the script as the new user with the known password
        info_blue "Re-running script as user $username..."
        echo "frappemanager" | sudo -S -u "$username" SUDO_ASKPASS=/bin/echo bash -c "echo frappemanager | sudo -S $script_path $flags"
        
        exit $?
    fi
}

install_fm_dev(){
    info_blue "Installing frappe-manager from development branch..."
    pip3 install --user --upgrade --force-reinstall --break-system-packages git+https://github.com/rtCamp/Frappe-Manager.git@develop
    info_green "$(bold 'fm' $(pip3 list | grep frappe-manager | awk '{print $2}')) (development) installed."
}

install_fm(){
    local dev=${1:-false}
    if check_fm_version; then
        info_green "Frappe Manager is already installed at the correct version."
        return 0
    fi
    
    if [ "$dev" = true ]; then
        install_fm_dev
    else
        info_blue "Installing frappe-manager..."
        pip3 install --user --upgrade --force-reinstall --break-system-packages frappe-manager
        info_green "$(bold 'fm' $(pip3 list | grep frappe-manager | awk '{print $2}')) installed."
    fi
}

has_docker_compose(){
    if command -v docker &> /dev/null; then
        if docker compose version &> /dev/null; then
            return 0
        fi
    fi
    return 1
}

has_pyenv(){
    if [[ "$(pyenv --version 2>&1 || true)" = *"pyenv: command not found"* ]]; then
        return 1
    else
        return 0
    fi
}

check_docker_version() {
    if [ "$FORCE" = true ]; then
        return 1
    fi
    
    local required_version="20.10.0"
    if command -v docker >/dev/null 2>&1; then
        local current_version=$(docker version --format '{{.Server.Version}}' 2>/dev/null)
        if [ $? -eq 0 ] && [ "$(printf '%s\n' "$required_version" "$current_version" | sort -V | head -n1)" = "$required_version" ]; then
            return 0
        fi
    fi
    return 1
}

check_fm_version() {
    if [ "$FORCE" = true ]; then
        return 1
    fi
    
    local current_version=$(pip3 list | grep frappe-manager | awk '{print $2}')
    if [ -n "$current_version" ]; then
        if [ "$DEVELOPMENT" = true ]; then
            # For dev version, always reinstall to get latest
            return 1
        fi
        # For stable version, check if installed
        return 0
    fi
    return 1
}

check_path_entry() {
    local entry="$1"
    local shellrc="$2"
    if grep -q "^export PATH=.*$entry" "$shellrc" 2>/dev/null; then
        return 0
    fi
    return 1
}

check_shell_completion() {
    local shell_type="${SHELL##*/}"
    local completion_file
    case "$shell_type" in
        bash)
            completion_file="$HOME/.bash_completion.d/fm.completion"
            ;;
        zsh)
            completion_file="$HOME/.zsh/completion/_fm"
            ;;
    esac
    
    if [ -f "$completion_file" ]; then
        return 0
    fi
    return 1
}

has_tty() {
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
        NONINTERACTIVE=1 /bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
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
    if check_docker_version; then
        info_green "Docker $(docker version --format '{{.Server.Version}}') is already installed."
    else
        info_blue "Installing Docker Engine for Ubuntu..."

        # Use SUDO_ASKPASS for non-interactive password input
        export SUDO_ASKPASS="/bin/echo"
        
        # Function to run sudo commands
        run_sudo() {
            echo "frappemanager" | sudo -S "$@"
        }

        # Add Docker's official GPG key:
        run_sudo apt-get update
        run_sudo apt-get install -y ca-certificates curl
        run_sudo install -m 0755 -d /etc/apt/keyrings
        run_sudo curl -fsSL https://download.docker.com/linux/ubuntu/gpg -o /etc/apt/keyrings/docker.asc
        run_sudo chmod a+r /etc/apt/keyrings/docker.asc

        # Add the repository to Apt sources:
        REPO_CONTENT="deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.asc] https://download.docker.com/linux/ubuntu $(. /etc/os-release && echo "$VERSION_CODENAME") stable"
        echo "$password" | sudo -S bash -c "echo \"$REPO_CONTENT\" > /etc/apt/sources.list.d/docker.list"
        run_sudo apt-get update
        run_sudo apt-get install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin
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

    if ! type python3 > /dev/null 2>&1 || ! python3 -c 'import sys; exit(0 if sys.version_info >= (3,10) else 1)' 2>/dev/null; then
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

    install_fm "$DEVELOPMENT"
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
            info_blue "Installing pip3"
            python -m ensurepip --upgrade
            info_green "Installed pip3"
        else
            info_green "pip3 already installed."
        fi
    else
        info_blue "Pyenv detected. Using it to install python"
        install_pyenv_python
    fi

    install_fm "$DEVELOPMENT"
}

handle_shell(){
    local shellrc

    # Detect shell more reliably
    if [ -n "$BASH_VERSION" ]; then
        shellrc="${HOME}/.bashrc"
    elif [ -n "$ZSH_VERSION" ]; then
        shellrc="${HOME}/.zshrc"
    else
        shellrc="${HOME}/.bashrc"
    fi

    if ! has_pyenv; then
        if [[ "${shellrc:-}" ]]; then
            info_blue "Checking default pip3 dir in PATH"
            if ! check_path_entry "$HOME/.local/bin" "$shellrc"; then
                echo 'export PATH="$HOME/.local/bin:$PATH"' >> "$shellrc"
                export PATH="$HOME/.local/bin:$PATH"
                info_green "Added $HOME/.local/bin to PATH using $shellrc file."
            else
                info_green "$HOME/.local/bin already in PATH."
            fi
        fi
        
        if [ -x "$HOME/.local/bin/fm" ] && ! check_shell_completion; then
            info_blue "Installing fm shell completion."
            $HOME/.local/bin/fm --install-completion || true
        else
            info_green "FM shell completion already installed."
        fi
    else
        if ! check_shell_completion; then
            info_blue "Installing fm shell completion."
            export PATH="$(pyenv root)/shims:$PATH"
            command -v fm >/dev/null 2>&1 && fm --install-completion || true
        else
            info_green "FM shell completion already installed."
        fi
    fi
}


# Initialize default values
USERNAME="frappe"
DEVELOPMENT=false
FORCE=false

# Parse arguments in any order
for arg in "$@"; do
    case "$arg" in
        --help|-h)
            show_help
            ;;
        --dev)
            DEVELOPMENT=true
            ;;
        --force|-f)
            FORCE=true
            ;;
        *)
            USERNAME="$arg"
            ;;
    esac
done

# Detect OS and call the respective functions
OS="$(uname)"
handle_root "$USERNAME"
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
