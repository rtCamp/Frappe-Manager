## `fm shell`

Open a shell in one of a bench's containers, or run a command in it.

A command can come from -c, from the arguments after --, or from stdin when stdin is not a terminal, and its exit code becomes fm's. --bench-console works on the frappe service only: interactively it is bench console, and with -c or piped input it runs Python with frappe already initialised and connected.

**Usage**:

```console
$ fm shell BENCHNAME [OPTIONS]
```

**Arguments**:

* `BENCHNAME`: Name of the bench.

**Options**:

* `--command`: Run this command and exit.  [default: -c]
* `--user`: User inside the container. Defaults to frappe on the frappe service, and is ignored with --run.
* `--service`: Container to enter.
* `--shell-path`: Shell to spawn. Defaults to /bin/bash, or sh on images without bash.
* `--run`: Use a throwaway 'docker compose run --rm' container instead of the bench's.
* `--bench-console`: Enter the Frappe context on the frappe service: bench console interactively, Python from -c or stdin.
* `--site`: Site the bench console connects to. Defaults to the bench name.


## Examples

### Open a shell in the bench

```bash
fm shell mybench
```

### Run one command

Everything after -- is joined into one command line and run through the container's shell.

```bash
fm shell mybench -- bench migrate
```

### Pipe a script in

stdin is read as a shell script whenever it is not a terminal.

```bash
fm shell mybench <<'EOF'
bench build --app frappe
bench clear-cache
EOF
```

### Work in the Frappe context

An IPython console with frappe initialised. With -c or piped input it runs Python instead.

```bash
fm shell mybench --bench-console
```

### Use a throwaway container

--run goes through 'docker compose run --rm' rather than the bench's own container.

```bash
fm shell mybench --run -- bench migrate
```

