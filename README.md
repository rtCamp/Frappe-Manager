<div align="center">

# 🚀 Frappe Manager

[![Python 3.13+](https://img.shields.io/badge/python-3.13+-blue.svg)](https://www.python.org/downloads/)
[![GHCR](https://img.shields.io/badge/ghcr-%232496ED.svg?logo=docker&logoColor=white)](https://github.com/orgs/rtCamp/packages?repo_name=Frappe-Manager)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![PyPI version](https://badge.fury.io/py/frappe-manager.svg)](https://badge.fury.io/py/frappe-manager)

### Simplify Your Frappe Development & Deployment Workflow

*A powerful CLI tool that streamlines the entire lifecycle of Frappe applications using Docker - from development to deployment.*

[Quick Start](#-quick-start) • [Documentation](https://opensource.rtcamp.com/Frappe-Manager/dev/) • [Examples](#-examples) • [Support](#-support)

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
• VSCode integration with debugger support<br>
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
• Adminer for db management
</td>
</tr>
</table>

## 🛠️ Requirements

- Python 3.13.+
- Docker
- VSCode (optional, for development features)

## 🚀 Installation

### Stable Release (Recommended)

📦 **For production use** • Matches [stable documentation](https://opensource.rtcamp.com/Frappe-Manager/)

Using uv (recommended):

```bash
# Install with uv tool
uv tool install --python 3.13 frappe-manager

# Try without installing
uvx --from frappe-manager fm --help

# Upgrade to latest version
uv tool upgrade frappe-manager
```

Using pipx:

```bash
# Install stable version
pipx install frappe-manager 

# Upgrade to latest version
pipx upgrade frappe-manager
```

### Development Version

🚧 **For testing and contributors** • Matches [dev documentation](https://opensource.rtcamp.com/Frappe-Manager/dev/)

> **⚠️ Warning**: Development builds may be unstable. Use for testing only.

Using uv:

```bash
# Install latest development version
uv tool install git+https://github.com/rtcamp/frappe-manager@develop

# Run without installing
uvx --from git+https://github.com/rtcamp/frappe-manager@develop fm --help
```

Using pipx:

```bash
# Install latest development version
pipx install git+https://github.com/rtcamp/frappe-manager@develop
```

## ⚡ Quick Start

Create your first Frappe bench:

```bash
# Create a development bench (default)
fm create mybench

# Create with ERPNext
fm create mybench --apps frappe:version-16 --apps erpnext:version-16

# Create with multiple apps
fm create mybench --apps erpnext --apps hrms

# Create production bench
fm create mybench --environment prod
```

That's it! Your bench is ready. Access it at `http://mybench.localhost`

## 📋 Command Reference

| Command | Description | Documentation |
|---------|-------------|---------------|
| `fm code` | Open bench in vscode. | [Docs: Code](https://opensource.rtcamp.com/Frappe-Manager/dev/commands/code/) |
| `fm create` | Create a new bench with apps | [Docs: Create](https://opensource.rtcamp.com/Frappe-Manager/dev/commands/create/) |
| `fm delete` | Delete a bench and optionally its database from global-db service. | [Docs: Delete](https://opensource.rtcamp.com/Frappe-Manager/dev/commands/delete/) |
| `fm info` | Show bench information and configuration | [Docs: Info](https://opensource.rtcamp.com/Frappe-Manager/dev/commands/info/) |
| `fm list` | List all benches. | [Docs: List](https://opensource.rtcamp.com/Frappe-Manager/dev/commands/list/) |
| `fm logs` | Show bench logs (server or container) | [Docs: Logs](https://opensource.rtcamp.com/Frappe-Manager/dev/commands/logs/) |
| `fm migrate` | Migrate Frappe Manager to current version. | [Docs: Migrate](https://opensource.rtcamp.com/Frappe-Manager/dev/commands/migrate/) |
| `fm ngrok` | Create ngrok tunnel for bench | [Docs: Ngrok](https://opensource.rtcamp.com/Frappe-Manager/dev/commands/ngrok/) |
| `fm reset` | Drop database and reinstall all apps | [Docs: Reset](https://opensource.rtcamp.com/Frappe-Manager/dev/commands/reset/) |
| `fm restart` | Restart bench services (web, workers, redis, nginx) | [Docs: Restart](https://opensource.rtcamp.com/Frappe-Manager/dev/commands/restart/) |
| `fm self` | Manage self | [Docs: Self](https://opensource.rtcamp.com/Frappe-Manager/dev/commands/self/) |
| `fm services` | Manage services | [Docs: Services](https://opensource.rtcamp.com/Frappe-Manager/dev/commands/services/) |
| `fm shell` | Spawn shell for the bench or execute a command. | [Docs: Shell](https://opensource.rtcamp.com/Frappe-Manager/dev/commands/shell/) |
| `fm ssl` | Manage ssl | [Docs: Ssl](https://opensource.rtcamp.com/Frappe-Manager/dev/commands/ssl/) |
| `fm start` | Start a bench. | [Docs: Start](https://opensource.rtcamp.com/Frappe-Manager/dev/commands/start/) |
| `fm stop` | Stop a bench. | [Docs: Stop](https://opensource.rtcamp.com/Frappe-Manager/dev/commands/stop/) |
| `fm update` | Update bench configuration and settings | [Docs: Update](https://opensource.rtcamp.com/Frappe-Manager/dev/commands/update/) |

> 💡 **Tip**: Use `fm <command> --help` to see detailed options and examples for any command.

## 📖 Documentation

Visit our documentation site on GitHub Pages:

- https://opensource.rtcamp.com/Frappe-Manager/dev/ for detailed guides, configuration, and troubleshooting.

## 🤝 Support

- 🐛 [Report issues](https://github.com/rtCamp/Frappe-Manager/issues)
- 💬 [Discussions](https://github.com/rtCamp/Frappe-Manager/discussions)
- 🌟 Star us on GitHub!

## 👏 Credits

Based on official [Frappe Docker](https://github.com/frappe/frappe_docker) images.

## 📄 License

MIT License - see [LICENSE](LICENSE) file for details
