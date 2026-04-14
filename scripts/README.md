# Frappe Manager Scripts

This directory contains helper scripts for installing and maintaining Frappe Manager.

| Script | Purpose |
|--------|---------|
| [`install.sh`](#installation-script-installsh) | Install Frappe Manager and all its dependencies |
| [`update_cli_docs.py`](#cli-documentation-generator-update_cli_docspy) | Generate Markdown CLI docs from the live Typer app |

---

## Installation Script (`install.sh`)

Tested on:
- Ubuntu 24.04

The install script sets up all dependencies needed for Frappe Manager (fm), including:
- Docker Engine & Docker Compose
- [uv](https://docs.astral.sh/uv/) (Python package manager — also manages Python 3.13)
- Frappe Manager CLI tool (`fm`)

---

## Installation as root

When run as root, the script will:
1. Create a new user (default: `frappe`), or configure an existing one
2. Ensure the user is in the `sudo` and `docker` groups
3. Install all system dependencies
4. Re-run itself as that user to complete the setup

```bash
# Ubuntu
bash <(curl -s https://raw.githubusercontent.com/rtCamp/Frappe-Manager/develop/scripts/install.sh)

# macOS
zsh <(curl -s https://raw.githubusercontent.com/rtCamp/Frappe-Manager/develop/scripts/install.sh)
```

Customize username and password:

```bash
bash <(curl -s https://raw.githubusercontent.com/rtCamp/Frappe-Manager/develop/scripts/install.sh) \
  --username myuser --password mypass
```

---

## Installation as non-root user

When run as a normal user, the script will:
1. Use `sudo` to install system dependencies
2. Add the current user to the `docker` group if needed
3. Install Frappe Manager for the current user

```bash
# Ubuntu
bash <(curl -s https://raw.githubusercontent.com/rtCamp/Frappe-Manager/develop/scripts/install.sh)

# macOS
zsh <(curl -s https://raw.githubusercontent.com/rtCamp/Frappe-Manager/develop/scripts/install.sh)
```

---

## Development / branch install

Install from the `develop` branch:

```bash
bash <(curl -s https://raw.githubusercontent.com/rtCamp/Frappe-Manager/develop/scripts/install.sh) --dev
```

Install from any specific branch (implies `--dev`):

```bash
bash <(curl -s https://raw.githubusercontent.com/rtCamp/Frappe-Manager/develop/scripts/install.sh) \
  --branch my-feature-branch
```

Re-running with `--dev` or `--branch` always replaces an existing stable install.

---

## Non-interactive mode

Skip all prompts and use defaults (or provided values):

```bash
bash <(curl -s https://raw.githubusercontent.com/rtCamp/Frappe-Manager/develop/scripts/install.sh) \
  --username frappe --password mypass --non-interactive
```

Non-interactive mode is also implied automatically when stdin is not a terminal (e.g. piped execution).

---

## Options

Every flag has an equivalent environment variable. CLI flags take precedence over env vars.

| Flag | Env var | Default | Description |
|------|---------|---------|-------------|
| `--username <name>` | `FM_USERNAME` | `frappe` | User to create or configure (root only) |
| `--password <pass>` | `FM_PASSWORD` | `frappemanager` | Password for the user (root only). Alias: `--pass` |
| `--dev` | `FM_DEV` | — | Install from the `develop` branch instead of PyPI |
| `--branch <name>` | `FM_BRANCH` | `develop` | Install from a specific git branch (implies `--dev`). Also controls which branch the install script is downloaded from on pipe execution |
| `--force` | `FM_FORCE` | — | Force reinstall/update of all components |
| `--non-interactive` | `FM_NON_INTERACTIVE` | — | Skip all prompts, use provided or default values |
| `--help` | — | — | Show help message |

---

## Notes

- **Ubuntu**: Log out and back in after installation for Docker group changes to take effect.
- **macOS**: Complete Docker Desktop setup before using `fm`.
- Installation log is written to the directory where the script was invoked (e.g. `/root/fm-install-<timestamp>.log` for root, or the current working directory for non-root). The child re-run (as the created user) writes its log to that user's home directory (e.g. `/home/frappe/fm-install-<timestamp>.log`).
- The script is idempotent — safe to re-run. Steps already up-to-date are skipped.

---

## Examples

```bash
# Show help
bash <(curl -s .../install.sh) --help

# Interactive install as root (prompts for username and password)
bash <(curl -s .../install.sh)

# Non-interactive install as root with defaults
bash <(curl -s .../install.sh) --non-interactive

# Install as root with custom username and password
bash <(curl -s .../install.sh) --username myuser --password mypass

# Install stable version (PyPI latest)
bash <(curl -s .../install.sh)

# Install development version (develop branch)
bash <(curl -s .../install.sh) --dev

# Install from a specific branch
bash <(curl -s .../install.sh) --branch my-feature-branch

# Force reinstall everything
bash <(curl -s .../install.sh) --force

# Force reinstall development version as root with custom username
bash <(curl -s .../install.sh) --username myuser --dev --force
```

---

## CLI Documentation Generator (`update_cli_docs.py`)

`update_cli_docs.py` introspects the live Frappe Manager Typer CLI app and generates up-to-date Markdown documentation for every command and command group.

### What it does

- Generates one `.md` file per command/group under `<output_dir>/commands/`
- Loads usage examples from `frappe_manager/utils/examples.json` and embeds them in each doc
- If `Home.md` and `_Sidebar.md` exist in the output directory, updates their commands sections in-place
- With `--update-readme`, also updates the `## 📋 Command Reference` table in the project root `README.md`

### Requirements

`frappe-manager` (or a development install of it) must be importable in the current Python environment, along with `typer` and `rich`.

### Usage

```bash
# Auto-detect output directory (see resolution order below)
python scripts/update_cli_docs.py

# Also update the command reference table in the project README.md
python scripts/update_cli_docs.py --update-readme

# Write docs to an explicit directory
python scripts/update_cli_docs.py --output-dir /path/to/wiki
```

### Output directory resolution

The script picks an output directory in this order:

1. `--output-dir <path>` CLI flag
2. `WIKI_DIR` environment variable
3. `/tmp/frappe-manager-wiki` — if that path exists (convenient for a local wiki clone)
4. `docs/cli/` inside the project root — created automatically if nothing else matches

### Typical wiki update workflow

```bash
# 1. Clone the GitHub wiki locally
git clone https://github.com/rtCamp/Frappe-Manager.wiki.git /tmp/frappe-manager-wiki

# 2. Run the generator (auto-detects /tmp/frappe-manager-wiki)
python scripts/update_cli_docs.py

# 3. Optionally also refresh the project README command table
python scripts/update_cli_docs.py --update-readme

# 4. Review, commit, and push the wiki
cd /tmp/frappe-manager-wiki
git add -A
git commit -m "Update CLI docs"
git push
```
