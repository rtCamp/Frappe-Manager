### Install

The install script automatically sets up all dependencies needed for Frappe Manager (fm), including:
- Docker & Docker Compose 
- Python 3.10 or higher
- Pip
- Frappe Manager CLI tool

#### Installation as root user
When run as root, the script will:
1. Create a new user (default: 'frappe')
2. Set up all dependencies
3. Install Frappe Manager for the new user
4. Re-run itself as the new user to complete the setup

```bash
# As root on Ubuntu:
bash <(curl -s https://raw.githubusercontent.com/rtCamp/Frappe-Manager/develop/scripts/install.sh) [username]

# As root on macOS:
zsh <(curl -s https://raw.githubusercontent.com/rtCamp/Frappe-Manager/develop/scripts/install.sh) [username]
```
Note: If username is not provided, it defaults to 'frappe'

#### Installation as non-root user
When run as a normal user, the script will:
1. Use sudo to install system dependencies
2. Set up Docker permissions for the current user
3. Install Frappe Manager for the current user

```bash
# As non-root user on Ubuntu:
bash <(curl -s https://raw.githubusercontent.com/rtCamp/Frappe-Manager/develop/scripts/install.sh)

# As non-root user on macOS:
zsh <(curl -s https://raw.githubusercontent.com/rtCamp/Frappe-Manager/develop/scripts/install.sh)
```

#### Development Version Installation
To install the latest development version from the 'develop' branch, add the `--dev` flag:

As root:
```bash
# Ubuntu
bash <(curl -s https://raw.githubusercontent.com/rtCamp/Frappe-Manager/develop/scripts/install.sh) [username] --dev

# macOS
zsh <(curl -s https://raw.githubusercontent.com/rtCamp/Frappe-Manager/develop/scripts/install.sh) [username] --dev
```

As non-root user:
```bash
# Ubuntu
bash <(curl -s https://raw.githubusercontent.com/rtCamp/Frappe-Manager/develop/scripts/install.sh) --dev

# macOS
zsh <(curl -s https://raw.githubusercontent.com/rtCamp/Frappe-Manager/develop/scripts/install.sh) --dev
```

#### Notes:
- For Ubuntu: After installation, you'll need to log out and log back in for Docker group changes to take effect
- For macOS: You'll need to complete Docker Desktop setup before using fm
- The script creates a log file named `fm-install-<timestamp>.log` in the current directory

#### Command Line Arguments:
- `[username]`: Optional. Sets custom username when running as root (default: 'frappe')
- `--dev`: Optional. Installs development version from 'develop' branch
- `--help`: Optional. Show help message and usage information
- Arguments can be provided in any order

Examples:
```bash
# Show help message
bash <(curl -s https://raw.githubusercontent.com/rtCamp/Frappe-Manager/develop/scripts/install.sh) --help

# Install stable version as root with custom username
bash <(curl -s https://raw.githubusercontent.com/rtCamp/Frappe-Manager/develop/scripts/install.sh) myuser

# Install development version as root with custom username
bash <(curl -s https://raw.githubusercontent.com/rtCamp/Frappe-Manager/develop/scripts/install.sh) myuser --dev
bash <(curl -s https://raw.githubusercontent.com/rtCamp/Frappe-Manager/develop/scripts/install.sh) --dev myuser

# Install development version as non-root user
bash <(curl -s https://raw.githubusercontent.com/rtCamp/Frappe-Manager/develop/scripts/install.sh) --dev
```

