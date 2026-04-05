#!/bin/bash
PS4='+\[\033[0;33m\](\[\033[0;36m\]${BASH_SOURCE##*/}:${LINENO}\[\033[0;33m\])\[\033[0m\] '

# Function to setup log file with proper permissions
setup_logfile() {
	local log_dir
	if [ -n "$FM_INSTALL_AS_USER" ]; then
		# Child process runs as the target user — write to their home dir (always writable)
		log_dir="$HOME"
	else
		# Root/non-root process — write next to where the user invoked the script
		log_dir="$PWD"
	fi

	local logname="$log_dir/fm-install-$(date +"%Y%m%d_%H%M%S").log"
	touch "$logname"
	chmod 644 "$logname"

	echo "$logname"
}

LOGFILE=$(setup_logfile)

exec {BASH_XTRACEFD}>>"$LOGFILE"
set -ex

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

info_blue() {
	echo -e $'\U0001F6A7' "$(blue "$*")"
}

info_green() {
	echo "$(cyan '=>') $(green "$*")"
}

info_yellow() {
	echo "$(cyan '=>') $(yellow "$*")"
}

info_red() {
	echo "$(red 'X') $(red "$*")"
}

run_sudo() {
	echo "$PASSWORD" | sudo -S "$@"
}

cleanup() {
	local exit_code=$?
	info_green "Log: $LOGFILE"
	if [ "$OS" == "Linux" ] && [ "$NAME" == "Ubuntu" ]; then
		info_green "Please log out and log back from the current shell for linux group changes to take effect."
	fi
	exit $exit_code
}

trap cleanup EXIT

show_help() {
	cat <<EOF
Frappe Manager Installation Script

USAGE:
    As root:     $(basename "$0") [--username <name>] [--password <pass>] [--dev] [--branch <name>] [--force] [--non-interactive] [--help]
    As non-root: $(basename "$0") [--dev] [--branch <name>] [--force] [--non-interactive] [--help]

DESCRIPTION:
    Installs Frappe Manager (fm) and all required dependencies including Docker,
    Docker Compose, Python 3.10+, uv, and the fm CLI tool.

OPTIONS:
    --username <name>      User to create or configure when running as root (default: 'frappe').
                           Only valid when running as the root user.  Env: FM_USERNAME
    --password <password>  Password for the created user (default: 'frappemanager').
                           Only valid when running as the root user. Alias: --pass  Env: FM_PASSWORD
    --pass <password>      Alias for --password.
    --non-interactive      Skip all prompts and use provided or default values.
                           Implied when stdin is not a terminal (e.g. piped execution).  Env: FM_NON_INTERACTIVE
    --dev                  Install from the 'develop' branch instead of PyPI.
                           Re-running with --dev always replaces an existing stable install.  Env: FM_DEV
    --branch <name>        Install from a specific git branch (implies --dev, default: 'develop').
                           Also controls which branch the install script is downloaded from
                           when run via pipe/process substitution.  Env: FM_BRANCH
    --force                Force reinstall/update of all components, ignoring current versions.  Env: FM_FORCE
    --help                 Show this help message.

NOTES:
    - For Ubuntu: log out and back in after install for Docker group changes to take effect.
    - For macOS: complete Docker Desktop setup before using fm.
    - Installation log: written to the current directory (root/non-root run), or to the user's home directory (child re-run).

EXAMPLES:
    # Interactive install as root (prompts for username/password)
    $(basename "$0")

    # Non-interactive install as root with defaults
    $(basename "$0") --non-interactive

    # Install as root with custom username and password
    $(basename "$0") --username myuser --password mypass

    # Install development version (replaces stable if already installed)
    $(basename "$0") --dev

    # Install from a specific branch
    $(basename "$0") --branch my-feature-branch

    # Install development version as root with custom username
    $(basename "$0") --username myuser --dev

    # Force reinstall everything
    $(basename "$0") --force
EOF
	exit 0
}

show_plan() {
	local running_as_root=false
	if [ "$(id -u)" -eq 0 ] && [ -z "$SUDO_USER" ]; then
		running_as_root=true
	fi

	local fm_label
	if [ "$DEVELOPMENT" = true ]; then
		fm_label="frappe-manager $(yellow "@${BRANCH}") via uv tool"
	else
		fm_label="frappe-manager $(yellow 'latest') via uv tool"
	fi

	local mode_label
	if [ "$FORCE" = true ]; then
		mode_label="$(yellow 'force reinstall all')"
	else
		mode_label="$(green 'smart — skip steps already up-to-date')"
	fi

	echo ""
	echo "  $(bold 'Frappe Manager Installer')"
	echo "  $(cyan '━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━')"
	echo ""
	echo "  $(blue 'Mode:')  $mode_label"
	if [ "$running_as_root" = true ]; then
		echo "  $(blue 'User:')  $(yellow "$USERNAME")"
	fi
	echo ""
	echo "  $(bold 'Steps:')"
	if [ "$running_as_root" = true ]; then
		echo "    $(cyan '›') Create user $(yellow "$USERNAME") $(cyan '(or configure if already exists)')"
		echo "    $(cyan '›') Ensure $(yellow "$USERNAME") is in sudo + docker groups"
		echo "    $(cyan '›') Install Docker Engine + Compose"
		echo "    $(cyan '›') Install uv"
		echo "    $(cyan '›') Install $fm_label for $(yellow "$USERNAME")"
		echo "    $(cyan '›') Configure PATH and shell completion"
	else
		echo "    $(cyan '›') Install Docker Engine + Compose $(cyan '(sudo required)')"
		echo "    $(cyan '›') Install uv"
		echo "    $(cyan '›') Install $fm_label"
		echo "    $(cyan '›') Configure PATH and shell completion"
	fi
	echo ""
}

prompt_root_config() {
	if [ "$NON_INTERACTIVE" = true ] || ! has_tty; then
		return 0
	fi
	if [ "$(id -u)" -ne 0 ] || [ -n "$SUDO_USER" ]; then
		return 0
	fi

	if [ "$USERNAME_GIVEN" = false ]; then
		printf "  %s [%s]: " "$(blue 'Username')" "$(yellow "$USERNAME")"
		local input
		read -r input || true
		[ -n "$input" ] && USERNAME="$input"
	fi

	if [ "$PASSWORD_GIVEN" = false ]; then
		printf "  %s [%s]: " "$(blue 'Password')" "$(yellow "$PASSWORD")"
		local input
		read -r -s input || true
		echo ""
		[ -n "$input" ] && PASSWORD="$input"
	fi

	echo ""
}

create_user() {
	local username=${1:-frappe}
	local password=${2:-frappemanager}

	if id "$username" >/dev/null 2>&1; then
		info_yellow "User $username already exists — skipping creation"
		if ! id -nG "$username" | grep -qw sudo; then
			info_blue "Adding $username to sudo group..."
			usermod -aG sudo "$username"
			info_green "$username added to sudo group."
		fi
		return
	fi

	info_blue "Creating user $username..."
	run_sudo useradd -m -s /bin/bash "$username"
	run_sudo usermod -aG sudo "$username"

	# Handle chpasswd separately from run_sudo to properly pipe both passwords
	echo "$PASSWORD" | sudo -S sh -c "echo '$username:$password' | chpasswd"

	info_green "Created user $username with password: $password"
	info_yellow "Please change this password after installation!"
}

handle_root() {
	# Add a flag to prevent recursive execution
	if [ -n "$FM_INSTALL_AS_USER" ]; then
		return 0
	fi

	# Check if running as real root (not just sudo)
	if [ "$(id -u)" -eq 0 ] && [ -z "$SUDO_USER" ]; then
		local username=${1:-frappe}
		info_blue "Running as root, creating user $username..."

		create_user "$username" "$PASSWORD"

		local timestamp
		timestamp=$(date +"%Y%m%d_%H%M%S")
		local script_path="/tmp/fm-install-${timestamp}.sh"

		if [[ -f "$0" ]]; then
			cp -f "$0" "$script_path"
		else
			info_yellow "Pipe/process-substitution execution detected. Downloading fresh install script..."
			curl -fsSL "https://raw.githubusercontent.com/rtCamp/Frappe-Manager/${BRANCH:-develop}/scripts/install.sh" \
				-o "$script_path" || {
				info_red "Failed to download install script."
				exit 1
			}
		fi
		chmod +x "$script_path"

		export SUDO_ASKPASS="/bin/false"
		export DEBIAN_FRONTEND=noninteractive

		local flags="--non-interactive"
		[ "$DEVELOPMENT" = true ] && flags="$flags --dev"
		[ "$BRANCH_GIVEN" = true ] && flags="$flags --branch $BRANCH"
		[ "$FORCE" = true ] && flags="$flags --force"

		info_blue "Re-running script as user $username..."
		echo "$PASSWORD" | FM_INSTALL_AS_USER=1 sudo -S -E -H -u "$username" \
			env HOME="/home/$username" \
			USER="$username" \
			FM_SUDO_PASSWORD="$PASSWORD" \
			FM_USERNAME="" \
			FM_PASSWORD="" \
			FM_DEV="" \
			FM_BRANCH="" \
			FM_FORCE="" \
			FM_NON_INTERACTIVE="" \
			bash -c "FM_INSTALL_AS_USER=1 bash '$script_path' $flags"

		rm -f "$script_path"
		exit $?
	fi
}

install_fm_dev() {
	info_blue "Installing frappe-manager from branch '${BRANCH}'..."
	uv tool install --python 3.13 --force "git+https://github.com/rtCamp/Frappe-Manager.git@${BRANCH}"
	local version
	version=$(uv tool list 2>/dev/null | grep "^frappe-manager" | awk '{print $2}')
	info_green "$(bold "fm $version") (branch: ${BRANCH}) installed."
}

install_fm() {
	local dev=${1:-false}

	if check_fm_version; then
		local version
		version=$(uv tool list 2>/dev/null | grep "^frappe-manager" | awk '{print $2}')
		info_green "Frappe Manager $(bold "$version") is already at latest version."
		return 0
	fi

	if [ "$dev" = true ]; then
		install_fm_dev
	else
		info_blue "Installing frappe-manager..."
		if [ "$FORCE" = true ]; then
			uv tool install --python 3.13 --force frappe-manager
		else
			uv tool install --python 3.13 frappe-manager
		fi
		local version
		version=$(uv tool list 2>/dev/null | grep "^frappe-manager" | awk '{print $2}')
		info_green "$(bold "fm $version") installed."
	fi
}

has_docker_compose() {
	if command -v docker &>/dev/null; then
		if docker compose version &>/dev/null; then
			return 0
		fi
	fi
	return 1
}

check_docker_version() {
	if [ "$FORCE" = true ]; then
		return 1
	fi

	local required_version="20.10.0"
	if command -v docker >/dev/null 2>&1; then
		# Use 'docker --version' (client-only) — 'docker version' requires daemon
		# access via the docker socket, which the target user may not have yet
		# (group membership doesn't take effect until a new login session).
		local current_version
		current_version=$(docker --version 2>/dev/null | grep -oE '[0-9]+\.[0-9]+\.[0-9]+' | head -1)
		if [ -n "$current_version" ] && [ "$(printf '%s\n' "$required_version" "$current_version" | sort -V | head -n1)" = "$required_version" ]; then
			return 0
		fi
	fi
	return 1
}

check_fm_version() {
	if [ "$FORCE" = true ]; then
		return 1
	fi

	if [ "$DEVELOPMENT" = true ]; then
		return 1
	fi

	local current_version
	current_version=$(uv tool list 2>/dev/null | grep "^frappe-manager" | awk '{print $2}' | tr -d 'v')
	if [ -n "$current_version" ]; then
		local latest_version
		latest_version=$(uv pip index versions frappe-manager 2>/dev/null | head -1 | awk -F'[()]' '{print $2}')
		if [ "$current_version" = "$latest_version" ]; then
			return 0
		fi
		return 1
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

install_homebrew() {
	if ! type brew >/dev/null 2>&1; then
		NONINTERACTIVE=1 /bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
	else
		info_green "brew is already installed."
	fi
}

install_docker_macos() {
	if ! type docker >/dev/null 2>&1; then
		info_blue "Installing Docker Desktop for macOS..."
		install_homebrew
		brew install --cask docker
		info_green "Docker Desktop installed."
	else
		info_green "Docker Desktop is already installed."
	fi
}

install_docker_ubuntu() {
	if check_docker_version; then
		info_green "Docker $(docker --version | grep -oE '[0-9]+\.[0-9]+\.[0-9]+' | head -1) is already installed."
	else
		info_blue "Installing Docker Engine for Ubuntu..."

		run_sudo apt-get update
		run_sudo apt-get install -y ca-certificates curl
		run_sudo install -m 0755 -d /etc/apt/keyrings
		run_sudo curl -fsSL https://download.docker.com/linux/ubuntu/gpg -o /etc/apt/keyrings/docker.asc
		run_sudo chmod a+r /etc/apt/keyrings/docker.asc

		REPO_CONTENT="deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.asc] https://download.docker.com/linux/ubuntu $(. /etc/os-release && echo "$VERSION_CODENAME") stable"
		echo "$PASSWORD" | sudo -S bash -c "echo \"$REPO_CONTENT\" > /etc/apt/sources.list.d/docker.list"
		run_sudo apt-get update
		run_sudo apt-get install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin
		info_green "Docker Engine installed"
	fi

	if ! getent group docker >/dev/null; then
		info_green "Docker group does not exist. Creating docker group..."
		run_sudo groupadd docker
	fi

	if id -nG "$USER" | grep -qw docker; then
		info_green "$USER is already a member of the docker group."
	else
		info_blue "$USER is not a member of the docker group. Adding $USER to the docker group..."
		run_sudo usermod -aG docker "$USER"
		info_green "$USER has been added to the docker group."
	fi

	if ! has_docker_compose; then
		info_blue "Installing Docker Compose..."
		run_sudo DEBIAN_FRONTEND=noninteractive apt-get install -y docker-compose-plugin
		info_green "Docker Compose installed."
	else
		info_green "Docker Compose is already installed."
	fi

	if ! systemctl is-active --quiet docker.service; then
		info_blue "Docker service is not running. Starting docker service..."
		run_sudo systemctl enable docker.service
		run_sudo systemctl start docker.service
		info_green "Docker service started and enabled."
	else
		info_green "Docker service is already running."
	fi
}

install_uv() {
	local uv_bin="${HOME}/.local/bin/uv"
	if command -v uv &>/dev/null || [[ -x "$uv_bin" ]]; then
		export PATH="$HOME/.local/bin:$PATH"
		info_green "uv $(uv --version) is already installed."
		return 0
	fi
	info_blue "Installing uv..."
	curl -LsSf https://astral.sh/uv/install.sh | sh
	export PATH="$HOME/.local/bin:$PATH"
	if ! command -v uv &>/dev/null; then
		info_red "Failed to install uv. Please install manually: https://docs.astral.sh/uv/"
		exit 1
	fi
	info_green "uv $(uv --version) installed."
}

handle_shell() {
	local shellrc
	if [ -n "$BASH_VERSION" ]; then
		shellrc="${HOME}/.bashrc"
	elif [ -n "$ZSH_VERSION" ]; then
		shellrc="${HOME}/.zshrc"
	else
		shellrc="${HOME}/.bashrc"
	fi

	info_blue "Checking default tools dir in PATH"
	if ! check_path_entry "$HOME/.local/bin" "$shellrc"; then
		echo 'export PATH="$HOME/.local/bin:$PATH"' >>"$shellrc"
		export PATH="$HOME/.local/bin:$PATH"
		info_green "Added $HOME/.local/bin to PATH using $shellrc file."
	else
		info_green "$HOME/.local/bin already in PATH."
	fi

	if [ -x "$HOME/.local/bin/fm" ] && ! check_shell_completion; then
		info_blue "Installing fm shell completion."
		$HOME/.local/bin/fm --install-completion || true
	else
		info_green "FM shell completion already installed."
	fi
}

USERNAME="${FM_USERNAME:-frappe}"
PASSWORD="${FM_PASSWORD:-frappemanager}"
DEVELOPMENT=false
FORCE=false
NON_INTERACTIVE=false
USERNAME_GIVEN=false
PASSWORD_GIVEN=false
BRANCH="${FM_BRANCH:-develop}"
BRANCH_GIVEN=false

[[ -n "${FM_USERNAME:-}" ]] && USERNAME_GIVEN=true
[[ -n "${FM_PASSWORD:-}" ]] && PASSWORD_GIVEN=true
[[ -n "${FM_DEV:-}" ]] && DEVELOPMENT=true
[[ -n "${FM_BRANCH:-}" ]] && BRANCH_GIVEN=true && DEVELOPMENT=true
[[ -n "${FM_FORCE:-}" ]] && FORCE=true
[[ -n "${FM_NON_INTERACTIVE:-}" ]] && NON_INTERACTIVE=true

# Parse arguments
while [[ $# -gt 0 ]]; do
	key="$1"
	case $key in
	--help | -h)
		show_help
		;;
	--dev)
		DEVELOPMENT=true
		shift
		;;
	--branch)
		if [[ -z "$2" || "$2" == --* ]]; then
			info_red "Error: --branch requires a value."
			exit 1
		fi
		BRANCH="$2"
		BRANCH_GIVEN=true
		DEVELOPMENT=true
		shift
		shift
		;;
	--force | -f)
		FORCE=true
		shift
		;;
	--non-interactive)
		NON_INTERACTIVE=true
		shift
		;;
	--username)
		if [[ -z "$2" || "$2" == --* ]]; then
			info_red "Error: --username requires a value."
			exit 1
		fi
		USERNAME="$2"
		USERNAME_GIVEN=true
		shift
		shift
		;;
	--password | --pass)
		if [[ -z "$2" || "$2" == --* ]]; then
			info_red "Error: $1 requires a value."
			exit 1
		fi
		PASSWORD="$2"
		PASSWORD_GIVEN=true
		shift
		shift
		;;
	*) # legacy positional username argument (deprecated)
		if [ "$(id -u)" -eq 0 ] && [ -z "$SUDO_USER" ] && [[ ! "$key" == --* ]]; then
			info_yellow "Warning: Positional username '$key' is deprecated. Use --username '$key' instead."
			USERNAME="$key"
			USERNAME_GIVEN=true
			shift
		else
			info_red "Unknown option: $1"
			exit 1
		fi
		;;
	esac
done

if [ "$PASSWORD_GIVEN" = false ] && [ -n "${FM_SUDO_PASSWORD:-}" ]; then
	PASSWORD="$FM_SUDO_PASSWORD"
fi

if { [ "$(id -u)" -ne 0 ] || [ -n "$SUDO_USER" ]; }; then
	if [ "$USERNAME_GIVEN" = true ]; then
		info_red "Error: --username is only valid when running as the root user."
		exit 1
	fi
	if [ "$PASSWORD_GIVEN" = true ]; then
		info_red "Error: --password / --pass is only valid when running as the root user."
		exit 1
	fi
fi

OS="$(uname)"

if [ -z "${FM_INSTALL_AS_USER:-}" ]; then
	show_plan
	prompt_root_config
fi

handle_root "$USERNAME"
if [ "$OS" == "Darwin" ]; then
	install_docker_macos
	install_uv
	install_fm "$DEVELOPMENT"
	handle_shell
	info_yellow "🔴 Please complete docker desktop setup before using fm."
	osascript -e 'display notification "Please complete docker desktop setup before using fm." with title "🔴 fm - complete docker desktop setup."'
	open -na /Applications/Docker.app
	info_green "Script execution completed."

elif [ "$OS" == "Linux" ]; then
	. /etc/os-release
	if [ "$NAME" == "Ubuntu" ]; then
		install_docker_ubuntu
		install_uv
		install_fm "$DEVELOPMENT"
		handle_shell
		info_green "Script execution completed."
	else
		info_red "Unsupported Linux distribution. This script supports macOS and Ubuntu."
		exit 1
	fi
else
	info_red "Unsupported operating system. This script supports macOS and Ubuntu."
	exit 1
fi
