# Frappe Manager

Frappe Manager is a small, friendly command-line tool to create and run Frappe benches on your local machine. It helps you build, test, and run Frappe sites without needing to know every detail about Docker or Compose.

Quickly create benches, manage SSL, run development tools like VSCode integration, and inspect logs — all with simple commands.

Key benefits:

- Easy local benches: create a working Frappe site in minutes (example bench name: `mybench`).
- Clear commands: common tasks like start, stop, update, and restore are single commands.
- Safe defaults: sensible directories and automatic renewal for certificates so you can focus on development.

Quick install (recommended):

```bash
uvx --from frappe-manager fm create mybench
```

Get started:

- [Get Started](getting-started/installation.md) | [Quick Start](getting-started/quick-start.md) | [Command Reference](commands/index.md)
