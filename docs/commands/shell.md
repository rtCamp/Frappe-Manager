## `fm shell`

Spawn shell for the bench or execute a command.

Supports multiple input modes:
- Interactive shell (no input)
- Command execution (-c flag)
- Heredoc/piped commands (stdin)
- Passthrough args (-- syntax)

The --bench-console flag provides three modes:
- Interactive: Opens IPython console with Frappe context (no -c or piped input)
- Script: Executes piped Python code (stdin)
- Inline: Executes -c command directly

In interactive mode, provides full terminal support.
Exit code from executed commands is preserved.

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
```bash
fm shell mybench
```

_Open shell as root user_
```bash
fm shell mybench --user root
```

_Execute single command_
```bash
fm shell mybench -c "bench --version"
```

_Execute commands from heredoc_
```bash
fm shell mybench <<'EOF'
ls -la
bench --version
EOF
```

_Open shell in nginx container_
```bash
fm shell mybench --service nginx --user nginx
```

_Run command with passthrough syntax_
```bash
fm shell mybench -- bench migrate
```

_Open interactive bench console with IPython_
```bash
fm shell mybench --bench-console
```

_Open bench console for specific site_
```bash
fm shell mybench --bench-console --site mysite.localhost
```

_Execute Python code in Frappe context_
```bash
fm shell mybench --bench-console -c "import frappe; print(frappe.__version__)"
```

_Execute Python script from heredoc in Frappe context_
```bash
fm shell mybench --bench-console <<'EOF'
import frappe
print(frappe.__version__)
EOF
```

_Execute Python script file in Frappe context_
```bash
fm shell mybench --bench-console < script.py
```

