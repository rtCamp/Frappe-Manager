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
