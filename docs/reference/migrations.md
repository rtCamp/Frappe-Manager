# Migrations

When you update the fm CLI itself, run `fm migrate` to bring your benches and configuration up to date.

What migrate does:

- Backs up bench config files and a MariaDB dump before changing a bench.
- Applies required configuration and compose file changes.

Common usage:

```bash
# Migrate a single bench
fm migrate mybench

# Migrate all benches
fm migrate --all-benches

# Skip confirmation prompts
fm migrate --all-benches --auto-proceed

# On failure behaviour: archive or rollback
fm migrate --on-failure archive
fm migrate --on-failure rollback
```

Notes:

- Always run `fm self update` first, then `fm migrate`.
- Migration creates backups under `~/frappe/backups/`.
- v0.19.0 notable changes: switched from pyenv+nvm to uv+fnm for toolchains and updated SSL configuration format.
