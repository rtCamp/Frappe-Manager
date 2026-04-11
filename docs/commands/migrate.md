# fm migrate

Run database migrations for a bench or for all benches. Migrations update the site schema and files when you upgrade versions.

Usage:

```console
$ fm migrate [BENCHNAME] [OPTIONS]
```

Options:

| Flag | Description |
|---|---|
| `--all-benches` | Run migrations for every bench |
| `--skip-all-backup` | Skip automatic backups before migrate |
| `--auto-proceed` | Do not prompt on completion |

When you upgrade Frappe Manager, some migrations run automatically on first command. Use `fm migrate` manually if you need to control timing.

Example:

```bash
fm migrate mybench
```
