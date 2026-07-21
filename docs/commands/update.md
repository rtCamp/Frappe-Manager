## `fm update`

Update bench configuration and settings.

Adjusts environment type, developer mode, runtime versions, alias domains, and other bench settings.

**Usage**:

```console
$ fm update BENCHNAME [OPTIONS]
```

**Arguments**:

* `BENCHNAME`: Name of the bench.

**Options**:

* `--admin-tools`: Toggle admin-tools.
* `-e, --environment`: Switch bench environment.
* `--developer-mode`: Toggle frappe developer mode.
* `--mailpit-as-default-mail-server`: Configure Mailpit as default mail server
* `--add-alias`: Add alias domains to the site (comma-separated, e.g., www.example.com,api.example.com)
* `--remove-alias`: Remove alias domains from the site (comma-separated, e.g., shop.example.com)
* `--upload-limit`: Set maximum upload size for files (e.g., '50M', '100M', '500M', '1G')
* `--python`: Update Python version (e.g., '3.11', '3.12', '>=3.11,<3.14'). Will recreate virtual environment.
* `--node`: Update Node version (e.g., '18', '20', '>=18'). Will install and set as default.
* `--skip-version-check`: Skip validation of Python/Node versions against Frappe requirements. Use with caution.
* `--recreate-python-env/--no-recreate-python-env`: Recreate the Python virtual environment. Use --no-recreate-python-env to skip if current version already satisfies the requirement.
* `--restart`: Update Docker restart policy for all bench services.
* `--allow-domain-conflicts`: Skip domain uniqueness validation when adding aliases (not recommended).
* `--newrelic/--no-newrelic`: Enable or disable NewRelic APM monitoring for the web process.
* `--newrelic-license-key`: NewRelic ingest license key.


## Examples

### Enable admin tools (Mailpit, Adminer)

Enables admin tools like Mailpit and Adminer for debugging and database access in development benches.

```bash
fm update mybench --admin-tools enable
```

### Disable admin tools

Disables admin tools for security or production setups.

```bash
fm update mybench --admin-tools disable
```

### Switch to production environment

Switches the bench to production environment settings and recreates necessary containers.

```bash
fm update mybench -e prod
```

### Switch to development environment

Switches the bench to development environment settings and enables developer conveniences.

```bash
fm update mybench -e dev
```

### Enable developer mode

Turns on Frappe developer mode which enables features useful for app development.

```bash
fm update mybench --developer-mode enable
```

### Add alias domains

Adds alias domains to the bench; remember to add SSL certificates separately with 'fm ssl add'.

```bash
fm update mybench --add-alias www.example.com,api.example.com
```

### Remove alias domains

Removes alias domains from bench configuration.

```bash
fm update mybench --remove-alias shop.example.com
```

### Update Python version

Updates the bench Python runtime, recreates the virtual environment, and reinstalls all apps into the new environment.

```bash
fm update mybench --python 3.11
```

### Install Python without recreating venv

Installs Python 3.14 via uv and sets it as default without touching the existing virtual environment.

```bash
fm update mybench --python 3.14 --no-recreate-python-env
```

### Update Node version

Updates Node.js runtime used by the bench and rebuilds related assets.

```bash
fm update mybench --node 20
```

### Set upload size limit

Sets the maximum file upload size for the bench (useful for large attachments).

```bash
fm update mybench --upload-limit 100M
```

