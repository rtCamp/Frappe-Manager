<div align="center">

# 🚀 Frappe Manager

[![Python 3.13+](https://img.shields.io/badge/python-3.13+-blue.svg)](https://www.python.org/downloads/)
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
• Redis Queue Dashboard<br>
• Adminer for db management 
</td>
</tr>
</table>

## 🛠️ Requirements

- Python 3.13.+
- Docker
- VSCode (optional, for development features)

## 🚀 Installation

### Using uv (Recommended)

[uv](https://docs.astral.sh/uv/) is a fast Python package installer and resolver.

```bash
# Run directly without installation (requires uv)
uvx --from frappe-manager fm create mysite

# Run latest development version without installation
uvx --from git+https://github.com/rtcamp/frappe-manager@develop fm --help

# Install with uv tool (persistent installation)
uv tool install --python 3.13 frappe-manager

# Install latest development version
uv tool install git+https://github.com/rtcamp/frappe-manager@develop

# Upgrade to latest version
uv tool upgrade frappe-manager
```

### Using pipx (Alternative)

```bash
# Install stable version
pipx install frappe-manager 

# Install latest development version
pipx install git+https://github.com/rtcamp/frappe-manager@develop

# Upgrade to latest version
pipx upgrade frappe-manager
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
| `fm create` | Create a new bench with apps | [Wiki: Create](https://github.com/rtCamp/Frappe-Manager/wiki/Create) |
| `fm start` | Start a bench | [Wiki: Start](https://github.com/rtCamp/Frappe-Manager/wiki/Start) |
| `fm stop` | Stop a bench | [Wiki: Stop](https://github.com/rtCamp/Frappe-Manager/wiki/Stop) |
| `fm restart` | Restart bench services | [Wiki: Restart](https://github.com/rtCamp/Frappe-Manager/wiki/Restart) |
| `fm delete` | Delete a bench and its database | [Wiki: Delete](https://github.com/rtCamp/Frappe-Manager/wiki/Delete) |
| `fm list` | List all benches | [Wiki: List](https://github.com/rtCamp/Frappe-Manager/wiki/List) |
| `fm info` | Show bench information | [Wiki: Info](https://github.com/rtCamp/Frappe-Manager/wiki/Info) |
| `fm logs` | Show bench logs | [Wiki: Logs](https://github.com/rtCamp/Frappe-Manager/wiki/Logs) |
| `fm shell` | Spawn shell or execute commands | [Wiki: Shell](https://github.com/rtCamp/Frappe-Manager/wiki/Shell) |
| `fm code` | Open bench in VSCode | [Wiki: Code](https://github.com/rtCamp/Frappe-Manager/wiki/Code) |
| `fm update` | Update bench configuration | [Wiki: Update](https://github.com/rtCamp/Frappe-Manager/wiki/Update) |
| `fm reset` | Drop database and reinstall apps | [Wiki: Reset](https://github.com/rtCamp/Frappe-Manager/wiki/Reset) |
| `fm ngrok` | Create ngrok tunnel | [Wiki: Ngrok](https://github.com/rtCamp/Frappe-Manager/wiki/Ngrok) |
| `fm ssl` | Manage SSL certificates | [Wiki: SSL](https://github.com/rtCamp/Frappe-Manager/wiki/SSL) |
| `fm services` | Manage global services | [Wiki: Services](https://github.com/rtCamp/Frappe-Manager/wiki/Services) |
| `fm migrate` | Migrate to current version | [Wiki: Migrate](https://github.com/rtCamp/Frappe-Manager/wiki/Migrate) |
| `fm self` | Operations on fm itself | [Wiki: Self](https://github.com/rtCamp/Frappe-Manager/wiki/Self) |

> 💡 **Tip**: Use `fm <command> --help` to see detailed options and examples for any command.

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
