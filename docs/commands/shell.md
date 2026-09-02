## `fm shell`

Open a shell in one of a bench's containers, or run a command in it.

A command can come from -c, from the arguments after --, or from stdin when stdin is not a terminal, and its exit code becomes fm's. --bench-console works on the frappe service only: interactively it is bench console, and with -c or piped input it runs Python with frappe already initialised and connected.

fm shell BENCH/SITE exports FRAPPE_SITE, so every bare bench command in that shell acts on the site you named. A plain fm shell BENCH exports nothing and leaves them on the bench's own default_site, which on a bench serving several sites is whichever one bench use last wrote. Name the site when it matters: bench migrate is not a command to aim by guesswork.

**Usage**:

```console
$ fm shell BENCH(/SITE) [OPTIONS]
```

**Arguments**:

* `BENCH(/SITE)`: Bench, or BENCH/SITE to act on one of its sites. Without a site part, the bench's primary site is used.

**Options**:

* `--command`: Run this command and exit.  [default: -c]
* `--user`: User inside the container. Defaults to frappe on the frappe service, and is ignored with --run.
* `--service`: Container to enter.
* `--shell-path`: Shell to spawn. Defaults to /bin/bash, or sh on images without bash.
* `--run`: Use a throwaway 'docker compose run --rm' container instead of the bench's.
* `--bench-console`: Enter the Frappe context on the frappe service: bench console interactively, Python from -c or stdin.


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

