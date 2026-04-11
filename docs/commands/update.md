## `fm update`

Update bench configuration and settings

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
```bash
fm update mybench --admin-tools enable
```

_Disable admin tools_
```bash
fm update mybench --admin-tools disable
```

_Switch to production environment_
```bash
fm update mybench -e prod
```

_Switch to development environment_
```bash
fm update mybench -e dev
```

_Enable developer mode_
```bash
fm update mybench --developer-mode enable
```

_Add alias domains_
```bash
fm update mybench --add-alias www.example.com,api.example.com
```

_Remove alias domains_
```bash
fm update mybench --remove-alias shop.example.com
```

_Update Python version_
```bash
fm update mybench --python 3.11
```

_Update Node version_
```bash
fm update mybench --node 20
```

_Set upload size limit_
```bash
fm update mybench --upload-limit 100M
```

