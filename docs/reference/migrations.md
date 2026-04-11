# Migrations

Migrations change the database schema and site files when you upgrade Frappe or apps.

Automatic migrations

When you run `fm` after upgrading the tool, some migrations run automatically to bring your benches to the expected state.

Manual migrations

For older upgrade paths (for example v0.10.0 or v0.11.0→v0.13.4), follow the documented steps in the original wiki: create a template bench, replace workspace directories, verify environment variables, and run `fm start` then `fm migrate`.

If you are not sure, take a backup first and test the migration on a copy of the bench.
