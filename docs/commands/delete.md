# fm delete

Delete a bench and its workspace. This will remove site files and by default does not remove databases in the global DB unless asked.

Usage:

```console
$ fm delete BENCHNAME [OPTIONS]
```

Options:

| Flag | Description |
|---|---|
| `-y, --yes` | Skip confirmation |
| `--delete-db-from-global-db` | Remove database from the global DB service |

!!! warning
    Deleting a bench can permanently remove site files and data. Make a backup before running this command.

Example:

```bash
fm delete mybench --yes --delete-db-from-global-db
```
