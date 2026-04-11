# fm shell

Open a shell inside a bench container or run a single command. This is the most flexible command for interacting with the bench workspace.

This page explains the main modes:

- Interactive shell: `fm shell mybench` opens a persistent shell in the container.
- Run a single command: `fm shell mybench -c "bench migrate"` runs the command and exits.
- Heredoc: pass multi-line scripts via heredoc to run complex sequences.
- Bench console: `--bench-console` opens the Frappe bench console for Python-level tasks.

Usage examples:

```bash
# Interactive
fm shell mybench

# Run a single command
fm shell mybench -c "bench migrate"

# Use heredoc (bash)
fm shell mybench -- bash <<'EOF'
bench --site site1.local backup
bench --site site1.local restore /tmp/site1.sql
EOF

# Open bench console
fm shell mybench --bench-console
```

!!! tip
    Use `fm shell mybench --user root` when you need elevated permissions inside the container for admin tasks.
