# Frappe Manager Scripts

This directory contains helper scripts for installing and maintaining Frappe Manager.

| Script | Purpose |
|--------|---------|
| [`install.sh`](#installation-script-installsh) | Install Frappe Manager and all its dependencies |
| [`update_cli_docs.py`](#cli-documentation-generator-update_cli_docspy) | Generate Markdown CLI docs from the live Typer app |
| [`deploy-preflight.sh`](#deploy-preflight-deploy-preflightsh) | Check a host can receive `fm switch`, before anything is built |
| [`expand-config.py`](#config-expansion-expand-configpy) | Substitute `FM_ACTION_*` environment variables into a config file, for CI |
| `docslint.py` | Docs checks mkdocs does not do: dash style, link hygiene, flags that no longer exist. Run via `just docs-lint` |
| `mutation_test.py` | Mutation testing: does a bug in covered code get CAUGHT. Run via `just mutate` |

---

## Installation Script (`install.sh`)

Tested on:
- Ubuntu 24.04

The install script sets up all dependencies needed for Frappe Manager (fm), including:
- Docker Engine & Docker Compose
- [uv](https://docs.astral.sh/uv/) (Python package manager - also manages Python 3.13)
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
| `--dev` | `FM_DEV` | - | Install from the `develop` branch instead of PyPI |
| `--branch <name>` | `FM_BRANCH` | `develop` | Install from a specific git branch (implies `--dev`). Also controls which branch the install script is downloaded from on pipe execution |
| `--force` | `FM_FORCE` | - | Force reinstall/update of all components |
| `--non-interactive` | `FM_NON_INTERACTIVE` | - | Skip all prompts, use provided or default values |
| `--help` | - | - | Show help message |

---

## Notes

- **Ubuntu**: Log out and back in after installation for Docker group changes to take effect.
- **macOS**: Complete Docker Desktop setup before using `fm`.
- Installation log is written to the directory where the script was invoked (e.g. `/root/fm-install-<timestamp>.log` for root, or the current working directory for non-root). The child re-run (as the created user) writes its log to that user's home directory (e.g. `/home/frappe/fm-install-<timestamp>.log`).
- The script is idempotent - safe to re-run. Steps already up-to-date are skipped.

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
3. `/tmp/frappe-manager-wiki` - if that path exists (convenient for a local wiki clone)
4. `docs/cli/` inside the project root - created automatically if nothing else matches

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

---

## Deploy preflight (`deploy-preflight.sh`)

Checks that a host can receive `fm switch`, before anything is built.

```bash
scripts/deploy-preflight.sh --host prod.example.com --user deploy
```

Prints the absolute path of `fm` on that host to stdout, or exits 1 explaining what is
wrong. The GitHub Action runs it before the bake, so a target that cannot deploy is caught
in seconds rather than after a build and a registry push.

It exists because of one non-obvious failure mode. `ssh host "fm switch ..."` gets a
**non-interactive** shell, which reads neither `.bashrc` nor `.profile`, so `PATH` is the
bare system default. An `fm` installed by `uv tool install` lives in `~/.local/bin` and is
invisible there: `command -v fm` returns nothing on a host where fm is installed and
working. So the script resolves fm explicitly, against `PATH` then `~/.local/bin`,
`/usr/local/bin` and `/usr/bin`, and runs `--version` on what it finds, because an
interrupted install still leaves the shim behind.

Connection problems are reported separately from a missing fm. Both used to look the same
from the outside, which sent the reader after the wrong thing.

| Option | Meaning |
|--------|---------|
| `--host`, `--user` | required |
| `--port` | default 22 |
| `--key-file` | private key file; or pass the key **content** in `SSH_PRIVATE_KEY` |
| `--known-hosts` | known_hosts file; or its **content** in `SSH_KNOWN_HOSTS` |
| `--keyscan` | no known_hosts supplied: accept what `ssh-keyscan` returns (trust on first use). Without it, ssh uses your own config |
| `--fm-path` | absolute path to fm, skipping discovery |
| `--workdir` | where generated files go; a temp dir is used and removed otherwise |
| `--github-output` | append `key=`, `known-hosts=` and `fm-bin=` to a file |

Secrets go through the environment, not argv, because argv is readable by every other
process on the machine.

---

## Config expansion (`expand-config.py`)

Substitutes `FM_ACTION_*` environment variables into a config file, so a file committed to the
repo can reference a secret instead of carrying it.

```bash
FM_ACTION_REGISTRY_TOKEN=... scripts/expand-config.py --in ci/prod.toml --out /tmp/prod.toml
```

```toml
# before
password = "${FM_ACTION_REGISTRY_TOKEN}"
# after
password = "ghp_..."
```

Used by the GitHub Action on every overlay it passes to `fm bake`. Three rules, each one a
reaction to how `os.path.expandvars` behaves:

- **Only `FM_ACTION_*` names.** `expandvars` rewrites anything, so a legitimate `$HOME` or `$PATH`
  in a config value would silently become a host path. Everything without the prefix is
  left exactly as written.
- **An unset reference is an error.** `expandvars` leaves `${FM_ACTION_TOKENN}` as literal text,
  so a typo becomes a password of `${FM_ACTION_TOKENN}` and surfaces much later as a registry
  rejection. Every missing name is listed at once.
- **Values are never printed.** Only the names that were substituted.

`${FM_ACTION_TOKEN:-fallback}` is refused rather than passed through: shell defaults are not
supported, and forwarding the text verbatim would look like it worked. Output is written
mode 600.

`--prefix` changes the prefix. `--in -` and `--out -` use stdin and stdout, which is how
the action expands an inline overlay without ever putting it in a command line.

This deliberately does not live in fm. Expanding at config-load time would write the
plaintext back out on the next save, since `export_to_toml` builds from the model and
normal operation rewrites `bench_config.toml` from dozens of call sites.
