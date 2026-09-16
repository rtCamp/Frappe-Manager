## `fm compose`

Run docker compose against a bench with all of its compose files already wired up.

Everything after the bench name is handed to docker compose untouched, so any subcommand and flag it accepts works here.

docker compose runs with the bench directory as its working directory, so a relative path in the arguments resolves there and not against the directory you called fm from.

**Usage**:

```console
$ fm compose BENCH
```

**Arguments**:

* `BENCH`: Bench to act on. Omit to pick from the benches you have.


## Examples

### Show the bench's containers

```bash
fm compose mybench ps
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

