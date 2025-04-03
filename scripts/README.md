### Install

The install script automatically sets up all dependencies needed for Frappe Manager (fm), including:
- Docker & Docker Compose 
- Python 3.10 or higher
- Pip
- Frappe Manager CLI tool

#### Linux (Ubuntu)
```bash
bash <(curl -s https://raw.githubusercontent.com/rtCamp/Frappe-Manager/develop/scripts/install.sh)
```

#### macOS
```bash
zsh <(curl -s https://raw.githubusercontent.com/rtCamp/Frappe-Manager/develop/scripts/install.sh)
```

#### Notes:
- For Ubuntu: After installation, you'll need to log out and log back in for Docker group changes to take effect
- For macOS: You'll need to complete Docker Desktop setup before using fm
- The script creates a log file named `fm-install-<timestamp>.log` in the current directory
- By default, it creates a user named 'frappe' if run as root (can be customized by passing a different username as argument)
  ```bash
  # Example: Install with custom username 'myuser'
  bash <(curl -s https://raw.githubusercontent.com/rtCamp/Frappe-Manager/develop/scripts/install.sh) myuser
  ```

