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
* `--restart`: Update Docker restart policy for all bench services.
* `--allow-domain-conflicts`: Skip domain uniqueness validation when adding aliases (not recommended).


**Examples**:

_Enable admin tools (Mailpit, Adminer)_
Enables admin tools like Mailpit and Adminer for debugging and database access in development benches.
```bash
fm update mybench --admin-tools enable
```

_Disable admin tools_
Disables admin tools for security or production setups.
```bash
fm update mybench --admin-tools disable
```

_Switch to production environment_
Switches the bench to production environment settings and recreates necessary containers.
```bash
fm update mybench -e prod
```

_Switch to development environment_
Switches the bench to development environment settings and enables developer conveniences.
```bash
fm update mybench -e dev
```

_Enable developer mode_
Turns on Frappe developer mode which enables features useful for app development.
```bash
fm update mybench --developer-mode enable
```

_Add alias domains_
Adds alias domains to the bench; remember to add SSL certificates separately with 'fm ssl add'.
```bash
fm update mybench --add-alias www.example.com,api.example.com
```

_Remove alias domains_
Removes alias domains from bench configuration.
```bash
fm update mybench --remove-alias shop.example.com
```

_Update Python version_
Updates the bench Python runtime and recreates virtual environments. May reinstall apps into the new environment.
```bash
fm update mybench --python 3.11
```

_Update Node version_
Updates Node.js runtime used by the bench and rebuilds related assets.
```bash
fm update mybench --node 20
```

_Set upload size limit_
Sets the maximum file upload size for the bench (useful for large attachments).
```bash
fm update mybench --upload-limit 100M
```

