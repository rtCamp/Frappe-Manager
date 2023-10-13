# `fm`

FrappeManager for creating frappe development envrionments.

**Usage**:

```console
$ fm [OPTIONS] COMMAND [ARGS]...
```

**Options**:

* `-v, --verbose`: Enable verbose output.
* `--version`: Show Version.
* `--install-completion`: Install completion for the current shell.
* `--show-completion`: Show completion for the current shell, to copy it or customize the installation.
* `--help`: Show this message and exit.

**Commands**:

* `code`: Open site in vscode.
* `create`: Create a new site.
* `delete`: Delete a site.
* `info`: Shows information about given site.
* `list`: Lists all of the available sites.
* `logs`: Show logs for the given site.
* `shell`: Open shell for the give site.
* `start`: Start a site.
* `stop`: Stop a site.

## `fm code`

Open site in vscode. 

**Usage**:

```console
$ fm code [OPTIONS] SITENAME
```

**Arguments**:

* `SITENAME`: Name of the site.  [required]

**Options**:

* `--user TEXT`: Connect as this user.  [default: frappe]
* `-e, --extension TEXT`: List of extensions to install in vscode at startup.Provide extension id eg: ms-python.python  [default: dbaeumer.vscode-eslint, esbenp.prettier-vscode, ms-python.python, ms-python.black-formatter, ms-python.flake8, visualstudioexptteam.vscodeintellicode, VisualStudioExptTeam.intellicode-api-usage-examples]
* `-f, --force-start`: Force start the site before attaching to container.
* `--help`: Show this message and exit.

**Examples**:

```console
$ fm code example
```

## `fm create`

Create a new site.

Frappe will be installed by default.

**Usage**:

```console
$ fm create [OPTIONS] SITENAME
```

**Arguments**:

* `SITENAME`: Name of the site  [required]

**Options**:

* `-a, --apps TEXT`: FrappeVerse apps to install. App should be specified in format <appname>:<branch> or <appname>.
* `--developer-mode / --no-developer-mode`: Enable developer mode  [default: developer-mode]
* `--frappe-branch TEXT`: Specify the branch name for frappe app  [default: version-14]
* `--admin-pass TEXT`: Default Password for the standard 'Administrator' User. This will be used as the password for the Administrator User for all new sites  [default: admin]
* `--enable-ssl / --no-enable-ssl`: Enable https  [default: no-enable-ssl]
* `--help`: Show this message and exit.

**Examples**:

```console
# Install frappe
$ fm create example

# Install frappe
$ fm create example --frappe-branch version-15-beta

# Install frappe, erpnext and hrms
$ fm create example --apps erpnext:version-14 --apps hrms:version-14

# Install frappe, erpnext and hrms
$ fm create example --frappe-branch version-15-beta --apps erpnext:version-15-beta --apps hrms:version-15-beta
```

## `fm delete`

Delete a site. 

**Usage**:

```console
$ fm delete [OPTIONS] SITENAME
```

**Arguments**:

* `SITENAME`: Name of the site  [required]

**Options**:

* `--help`: Show this message and exit.

**Examples**:

```console
$ fm delete example
```


## `fm info`

Shows information about given site.

**Usage**:

```console
$ fm info [OPTIONS] SITENAME
```

**Arguments**:

* `SITENAME`: Name of the site.  [required]

**Options**:

* `--help`: Show this message and exit.

**Examples**:

```console
$ fm info example
```

## `fm list`

Lists all of the available sites. 

**Usage**:

```console
$ fm list [OPTIONS]
```

**Options**:

* `--help`: Show this message and exit.

**Examples**:

```console
$ fm list example
```

## `fm logs`

Show logs for the given site. 

**Usage**:

```console
$ fm logs [OPTIONS] SITENAME
```

**Arguments**:

* `SITENAME`: Name of the site.  [required]

**Options**:

* `--service TEXT`: Specify Service  [default: frappe]
* `--follow / --no-follow`: Follow logs.  [default: no-follow]
* `--help`: Show this message and exit.

**Examples**:

```console
$ fm logs example
```

## `fm shell`

Open shell for the give site. 

**Usage**:

```console
$ fm shell [OPTIONS] SITENAME
```

**Arguments**:

* `SITENAME`: Name of the site.  [required]

**Options**:

* `--user TEXT`: Connect as this user.
* `--service TEXT`: Specify Service  [default: frappe]
* `--help`: Show this message and exit.

**Examples**:

```console
$ fm shell example
```

## `fm start`

Start a site. 

**Usage**:

```console
$ fm start [OPTIONS] SITENAME
```

**Arguments**:

* `SITENAME`: Name of the site  [required]

**Options**:

* `--help`: Show this message and exit.

**Examples**:

```console
$ fm start example
```

## `fm stop`

Stop a site. 

**Usage**:

```console
$ fm stop [OPTIONS] SITENAME
```

**Arguments**:

* `SITENAME`: Name of the site  [required]

**Options**:

* `--help`: Show this message and exit.

**Examples**:

```console
$ fm stop example
```
