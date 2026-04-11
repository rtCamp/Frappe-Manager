# fm restart

Restart bench services. Use this when you change configuration or deploy code updates.

Usage:

```console
$ fm restart BENCHNAME [OPTIONS]
```

Options:

| Flag | Description |
|---|---|
| `--web` | Restart only the web service |
| `--workers` | Restart worker containers |
| `--redis` | Restart redis services |
| `--nginx` | Restart nginx for the bench |
| `--container` | Restart a specific container |
| `--supervisor` | Use supervisor for a faster restart |
| `--force` | Force restart even if tasks are running |

!!! note
    Using `--supervisor` is often quicker because it restarts fewer containers and coordinates processes more efficiently.

Example:

```bash
fm restart mybench --workers
```

Examples:

```bash
fm restart mybench                       # default: supervisor restart (web + workers)
fm restart mybench --container           # stop + start Docker containers
fm restart mybench --supervisor          # restart supervisor processes (faster)
fm restart mybench --redis               # include Redis services
fm restart mybench --nginx               # also include nginx
fm restart mybench --web --no-workers    # only restart web (frappe + socketio)
fm restart mybench --force               # force-kill before restart
```

!!! tip
    Use `fm restart mybench --supervisor` for a quick restart that does not recreate containers.
