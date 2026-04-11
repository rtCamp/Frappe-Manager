# fm reset

Reset a bench. This is destructive: it drops the bench database and reinstalls apps from scratch.

Usage:

```console
$ fm reset BENCHNAME [OPTIONS]
```

Options:

| Flag | Description |
|---|---|
| `--admin-pass` | Set admin password after reset |

!!! warning
    `fm reset` deletes data. Take a backup (`bench backup`) before you run this command.

Example:

```bash
fm reset mybench --admin-pass newsecret
```
