<div align="center">

# 🚀 Frappe Manager

[![Python 3.11+](https://img.shields.io/badge/python-3.11+-blue.svg)](https://www.python.org/downloads/)
[![GHCR](https://img.shields.io/badge/ghcr-%232496ED.svg?logo=docker&logoColor=white)](https://github.com/orgs/rtCamp/packages?repo_name=Frappe-Manager)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![PyPI version](https://badge.fury.io/py/frappe-manager.svg)](https://badge.fury.io/py/frappe-manager)

### Simplify Your Frappe Development & Deployment Workflow

*A powerful CLI tool that streamlines the entire lifecycle of Frappe applications using Docker - from development to deployment.*

[Quick Start](#-quick-start) • [Documentation](https://github.com/rtCamp/Frappe-Manager/wiki) • [Examples](#-examples) • [Support](#-support)

</div>

![Frappe-Manager-Create-Site.svg](https://user-images.githubusercontent.com/28294795/283108791-0237d05a-2562-48be-987b-037a200d71a3.svg)

## ✨ Features

<table style="border: none;" cellspacing="20" cellpadding="10">
<tr style="border: none;">
<td style="border: none; vertical-align: top; width: 33%;">
<h3>🔥 Easy Setup</h3>
• Get a new Frappe environment running in minutes<br>
• Zero configuration needed
</td>
<td style="border: none; vertical-align: top; width: 33%;">
<h3>🐳 Docker-Based</h3>
• Consistent environments across all platforms<br>
• Isolated development environments
</td>
<td style="border: none; vertical-align: top; width: 33%;">
<h3>🌐 Multi-Bench Support</h3>
• Manage multiple Frappe benches from one server
</td>
</tr>

<tr style="border: none;">
<td style="border: none; vertical-align: top;">
<h3>👨‍💻 Development Tools</h3>
• VSCode & Antigravity IDE integration<br>
• Full debugger support for both editors<br>
• Automatic environment switching between dev/prod
</td>
<td style="border: none; vertical-align: top;">
<h3>🔒 SSL Management</h3>
• Built-in Let's Encrypt integration<br>
• Automatic certificate renewal
</td>
<td style="border: none; vertical-align: top;">
<h3>🛠️ Admin Tools</h3>
• Mailpit for email testing<br>
• Redis Queue Dashboard<br>
• Adminer for db management
</td>
</tr>
</table>

## 🛠️ Requirements

- Python 3.11 or higher
- Docker
- VSCode or Google Antigravity IDE (optional, for development features)

## 🚀 Quick Start

```bash
# Install Frappe Manager (stable)
pipx install frappe-manager 

# Install Frappe Manager (latest develop)
pipx install git+https://github.com/rtcamp/frappe-manager@develop 

# Setup shell completion
fm --install-completion

# Create your first site
fm create mysite
```

## 📚 Examples

### Development Setup
```bash
# Create a dev environment with ERPNext
fm create devsite --apps erpnext:version-15 --environment dev

# Start coding in VSCode (default)
fm code devsite --debugger

# Start coding in Google Antigravity IDE
fm code devsite --editor antigravity --debugger
```

### Production Setup

```bash
# Create Production Site
fm create example.com --environment prod

# Create production site with SSL using HTTP01 challenge
fm create example.com --environment prod \
  --ssl letsencrypt --letsencrypt-preferred-challenge http01 \
  --letsencrypt-email admin@example.com

# Create production site with SSL using DNS01 challenge 
fm create example.com --environment prod \
  --ssl letsencrypt --letsencrypt-preferred-challenge dns01 \
  --letsencrypt-email admin@example.com
```

### Daily Operations
```bash
# Common commands
fm start mysite      # Start site
fm stop mysite       # Stop site
fm info mysite       # View site info
fm logs mysite -f    # View logs
fm shell mysite      # Access shell
```

### IDE Integration

Frappe Manager supports multiple IDEs for development with full debugger support:

#### VSCode (Default)
```bash
# Open in VSCode without debugger
fm code mysite

# Open in VSCode with debugger configuration
fm code mysite --debugger

# Specify user and working directory
fm code mysite --user frappe --work-dir /workspace/frappe-bench --debugger
```

#### Google Antigravity IDE
```bash
# Open in Antigravity IDE
fm code mysite --editor antigravity

# Open in Antigravity IDE with debugger configuration
fm code mysite --editor antigravity --debugger

# Specify working directory
fm code mysite --editor antigravity --work-dir /workspace/frappe-bench --debugger
```

**What gets configured with `--debugger` flag:**
- **VSCode**: Creates `.vscode/` directory with `launch.json`, `tasks.json`, and `settings.json`
- **Antigravity**: Creates `.antigravity/` directory with `launch.json`, `tasks.json`, and `settings.json`
- Both include:
  - Frappe server debug configuration
  - Worker queue debugging
  - Function execution debugging
  - Python interpreter path
  - Ruff formatter settings
  - Pre-launch tasks to stop frappe server

## 📖 Documentation

Visit our [Wiki](https://github.com/rtCamp/Frappe-Manager/wiki) for:
- 📋 Detailed guides
- ⚙️ Configuration options
- 💡 Best practices
- ❓ Troubleshooting

## 🤝 Support

- 🐛 [Report issues](https://github.com/rtCamp/Frappe-Manager/issues)
- 💬 [Discussions](https://github.com/rtCamp/Frappe-Manager/discussions)
- 🌟 Star us on GitHub!

## 👏 Credits

Based on official [Frappe Docker](https://github.com/frappe/frappe_docker) images.

## 📄 License

MIT License - see [LICENSE](LICENSE) file for details
