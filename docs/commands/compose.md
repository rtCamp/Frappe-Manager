## `fm compose`

Run docker compose against a bench with all of its compose files already wired up.

Everything after the bench name is handed to docker compose untouched, so any subcommand and flag it accepts works here.

Put '--' in the bench position to pick the bench interactively instead of naming it: fm compose -- ps. This is also the only way to hand docker compose a flag fm would otherwise claim for itself, such as --help.

docker compose runs with the bench directory as its working directory, so a relative path in the arguments resolves there and not against the directory you called fm from.

**Usage**:

```console
$ fm compose BENCH
```

**Arguments**:

* `BENCH`: Bench to act on. Omit to pick from the benches you have; 'fm compose -- ARGS' also picks, passing ARGS to docker compose.


## Examples

### Show the bench's containers

```bash
fm compose mybench ps
```

### Pick the bench interactively

A bare '--' in the bench position means: pick from the benches you have (the current directory's bench wins) and pass everything after it to docker compose.

```bash
fm compose -- ps
```

### Follow the frappe logs

```bash
fm compose mybench logs -f frappe
```

### Open a shell in a container

```bash
fm compose mybench exec frappe bash
```

### Restart one service

```bash
fm compose mybench restart frappe
```

