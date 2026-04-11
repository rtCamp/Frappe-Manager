## `fm shell`

Spawn shell for the bench or execute a command.

Supports interactive shells, single command execution (-c), heredoc or piped input, and bench-console mode
which runs Python code inside an initialized Frappe context.

**Usage**:

```console
$ fm shell BENCHNAME [OPTIONS]
```

**Arguments**:

* `BENCHNAME`: Name of the bench.

**Options**:

* `--command`: Execute command and exit  [default: -c]
* `--user`: User to connect as
* `--service`: Service to connect to
* `--shell-path`: Shell path (e.g., /bin/bash, /bin/sh)
* `--run`: Use 'docker compose run --rm'
* `--bench-console`: Open bench console with Frappe context (interactive IPython or execute code via -c/stdin)
* `--site`: Site name for bench console (defaults to benchname if not specified)


**Examples**:

_Open interactive shell as frappe user_
Opens an interactive shell into the frappe service as the frappe user. Useful for ad-hoc debugging.
```bash
fm shell mybench
```

_Open shell as root user_
Starts a shell session in the container as the root user. Use with caution for administrative tasks.
```bash
fm shell mybench --user root
```

_Execute single command_
Runs a single command inside the service and exits with the command's exit code.
```bash
fm shell mybench -c "bench --version"
```

_Execute commands from heredoc_
Sends a heredoc to the container and executes the provided commands non-interactively.
```bash
fm shell mybench <<'EOF'
ls -la
bench --version
EOF
```

_Open shell in nginx container_
Opens a shell session inside the nginx container for debugging webserver configuration.
```bash
fm shell mybench --service nginx --user nginx
```

_Run command with passthrough syntax_
Passes through arguments after '--' directly to the container's shell or compose command.
```bash
fm shell mybench -- bench migrate
```

_Open interactive bench console with IPython_
Opens an IPython console with Frappe initialized so you can interact with frappe APIs.
```bash
fm shell mybench --bench-console
```

_Open bench console for specific site_
Opens the bench console for a specific site; useful when the bench hosts multiple sites.
```bash
fm shell mybench --bench-console --site mysite.localhost
```

_Execute Python code in Frappe context_
Executes a single Python statement inside the Frappe context and prints the result.
```bash
fm shell mybench --bench-console -c "import frappe; print(frappe.__version__)"
```

_Execute Python script from heredoc in Frappe context_
Executes a multi-line Python script inside the Frappe context provided via heredoc.
```bash
fm shell mybench --bench-console <<'EOF'
import frappe
print(frappe.__version__)
EOF
```

_Execute Python script file in Frappe context_
Reads a Python script from a file and executes it inside the bench's Frappe context.
```bash
fm shell mybench --bench-console < script.py
```

