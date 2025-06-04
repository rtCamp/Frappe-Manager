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

- Python 3.11 or higher
- Docker
- VSCode (optional, for development features)

## 🚀 Quick Start

```bash
# Install Frappe Manager
pip install frappe-manager

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

# Start coding (in VSCode)
fm code devsite --debugger
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

### Worker Process Management
```bash
# Production-safe worker operations
fm restart mysite \
  --wait-workers \
  --wait-workers-timeout 300 \  # 5 minute timeout
  --wait-workers-poll 5         # Check every 5 seconds

# Worker suspension with Redis
fm restart mysite \
  --suspend-rq \               # Use Redis suspension
  --wait-workers \
  --wait-workers-verbose      # Show detailed progress
```

For safety reasons:
- Default to `--wait-workers` in production
- Monitor jobs before operations
- Set appropriate timeouts
- Have rollback procedures ready

### Daily Operations
```bash
# Common commands
fm start mysite      # Start site
fm stop mysite       # Stop site
fm logs mysite -f    # View logs
fm shell mysite      # Access shell
```

### Worker Management
```bash
# Production-Safe Worker Management
fm restart mysite --wait-workers --suspend-rq    
# - Uses Redis to suspend new jobs
# - Waits for current jobs to finish
# - Ensures clean worker shutdown
# - Recommended for production

# Development/Testing Worker Management
fm restart mysite --no-wait-workers
# - Uses Signal 34 for worker detachment
# - Waits 3 seconds for cleanup
# - Sends SIGTERM for RQ graceful shutdown
# - Faster but less controlled

# Monitor Worker Status
fm status mysite -v                
# - Shows detailed worker states
# - Displays process hierarchies
# - Reports job queue status
```

Safety Notes:
- Use --wait-workers --suspend-rq in production
- Monitor active jobs before operations
- Set appropriate timeouts (--wait-workers-timeout)
- Have rollback procedures ready
- Never use --no-wait-workers in production

### Worker Process Handling

Frappe Manager supports two approaches for worker management:

1. Redis-Based Suspension (Production Safe)
   - Suspends new job intake via Redis flag
   - Allows current jobs to complete
   - Monitors worker states
   - Ensures data consistency
   - Uses RQ's built-in suspension mechanism
   - Required when schema changes are involved

2. Signal-Based Management
   - Uses Signal 34 for worker detachment
   - Preserves running jobs
   - Sends SIGTERM after 3-second delay
   - Allows RQ's graceful shutdown
   - Monitor process tracks completion
   - Suitable for:
     • Development/testing environments
     • When no schema changes are involved
     • Code-only updates
     • Configuration changes
     • Quick service restarts

The Signal 34 handler in bench-wrapper.sh:
- Detaches worker from supervisor
- Forks monitor process
- Waits 3 seconds
- Sends SIGTERM for graceful shutdown
- Tracks process completion

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
