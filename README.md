# Frappe Manager

A powerful CLI tool that simplifies managing Frappe-based applications using Docker. Designed for developers and system administrators, Frappe Manager (FM) streamlines the entire lifecycle of Frappe applications from development to deployment.

![Frappe-Manager-Create-Site.svg](https://user-images.githubusercontent.com/28294795/283108791-0237d05a-2562-48be-987b-037a200d71a3.svg)

## Features

- **Easy Setup**: Get a new Frappe environment running in minutes
- **Docker-Based**: Consistent environments across all platforms
- **Multi-Site Support**: Manage multiple Frappe sites from one interface
- **Development Tools**:
  - VSCode integration with debugger support
  - Hot-reload for rapid development
  - Automatic environment switching between dev/prod
- **SSL Management**: Built-in Let's Encrypt integration
- **Admin Tools**:
  - Mailpit for email testing
  - Redis Queue Dashboard
  - Database management interface
- **Process Management**: 
  - Supervisor integration for process control
  - Easy service restarts and monitoring
- **Extensible**: Support for custom apps and configurations

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

## Contributing

We welcome contributions! See our [Contributing Guide](CONTRIBUTING.md) for details.

## Credits

Based on official [Frappe Docker](https://github.com/frappe/frappe_docker) images.

## License

MIT License - see LICENSE file for details
