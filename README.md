<div align="center">

# 🚀 Frappe Manager

[![Python 3.11+](https://img.shields.io/badge/python-3.11+-blue.svg)](https://www.python.org/downloads/)
[![Docker](https://img.shields.io/badge/docker-%230db7ed.svg?logo=docker&logoColor=white)](https://www.docker.com/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![PyPI version](https://badge.fury.io/py/frappe-manager.svg)](https://badge.fury.io/py/frappe-manager)

A powerful CLI tool that simplifies managing Frappe-based applications using Docker. Streamlines the entire lifecycle of Frappe applications from development to deployment.

</div>

![Frappe-Manager-Create-Site.svg](https://user-images.githubusercontent.com/28294795/283108791-0237d05a-2562-48be-987b-037a200d71a3.svg)

## ✨ Features

<div class="features-grid" style="display: grid; grid-template-columns: repeat(auto-fit, minmax(300px, 1fr)); gap: 20px; margin: 20px 0;">

<div style="border: 1px solid #ddd; border-radius: 8px; padding: 15px; background-color: #f8f9fa;">
<h3>🔥 Easy Setup</h3>
<ul>
<li>Get a new Frappe environment running in minutes</li>
<li>Zero configuration needed</li>
</ul>
</div>

<div style="border: 1px solid #ddd; border-radius: 8px; padding: 15px; background-color: #f8f9fa;">
<h3>🐳 Docker-Based</h3>
<ul>
<li>Consistent environments across all platforms</li>
<li>Isolated development environments</li>
</ul>
</div>

<div style="border: 1px solid #ddd; border-radius: 8px; padding: 15px; background-color: #f8f9fa;">
<h3>🌐 Multi-Bench Support</h3>
<ul>
<li>Manage multiple Frappe benches from one server</li>
</ul>
</div>

<div style="border: 1px solid #ddd; border-radius: 8px; padding: 15px; background-color: #f8f9fa;">
<h3>👨‍💻 Development Tools</h3>
<ul>
<li>VSCode integration with debugger support</li>
<li>Automatic environment switching between dev/prod</li>
</ul>
</div>

<div style="border: 1px solid #ddd; border-radius: 8px; padding: 15px; background-color: #f8f9fa;">
<h3>🔒 SSL Management</h3>
<ul>
<li>Built-in Let's Encrypt integration</li>
<li>Automatic certificate renewal</li>
</ul>
</div>

<div style="border: 1px solid #ddd; border-radius: 8px; padding: 15px; background-color: #f8f9fa;">
<h3>🛠️ Admin Tools</h3>
<ul>
<li>Mailpit for email testing</li>
<li>Redis Queue Dashboard</li>
<li>Database management interface</li>
</ul>
</div>

<div style="border: 1px solid #ddd; border-radius: 8px; padding: 15px; background-color: #f8f9fa;">
<h3>⚙️ Process Management</h3>
<ul>
<li>Supervisor integration for process control</li>
<li>Easy service restarts and monitoring</li>
</ul>
</div>

<div style="border: 1px solid #ddd; border-radius: 8px; padding: 15px; background-color: #f8f9fa;">
<h3>🔌 Extensible</h3>
<ul>
<li>Support for custom apps and configurations</li>
<li>Plugin architecture</li>
</ul>
</div>

</div>

## Requirements

- Python 3.11+
- Docker
- VSCode (optional, for development features)

## Quick Start

1. **Installation**:
   ```bash
   pip install frappe-manager
   ```

2. **Setup Shell Completion**:
   ```bash
   fm --install-completion
   ```
   Restart your shell after installation

3. **Create Your First Site**:
   ```bash
   # Basic site with Frappe v15
   fm create mysite

   # Development site with specific apps
   fm create devsite --apps erpnext:version-15 --apps hrms:version-15 --environment dev
   
   # Production site with SSL
   fm create prodsite --environment prod --ssl le --letsencrypt-email admin@example.com
   ```

## Common Commands

```bash
# Start/Stop sites
fm start mysite
fm stop mysite

# Access development shell
fm shell mysite

# View logs
fm logs mysite --follow

# Open in VSCode with debugger
fm code mysite --debugger

# Manage SSL certificates
fm ssl update mysite
```

## Documentation

Visit our [Wiki](https://github.com/rtCamp/Frappe-Manager/wiki) for:
- Detailed usage examples
- Configuration guides
- Best practices
- Troubleshooting

## Credits

Based on official [Frappe Docker](https://github.com/frappe/frappe_docker) images.

## License

MIT License - see LICENSE file for details
