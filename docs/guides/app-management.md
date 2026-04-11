# App Management

Install apps when you create a bench or later from a bench shell.

Install during create:

```bash
fm create mybench --apps erpnext
```

Install after creation (example for ERPNext):

```bash
fm shell mybench -c "bench get-app erpnext && bench --site mybench.localhost install-app erpnext"
```

Install a private app (pass a GitHub URL or org/repo and a token):

```bash
fm create mybench --apps org/private-app:main --github-token YOUR_TOKEN
```

App string formats you can use:

```
erpnext                                    -> frappe/erpnext, default branch
erpnext:version-15                         -> frappe/erpnext, version-15 branch
frappe/erpnext:version-15                  -> organization/repo and branch
https://github.com/org/repo:main           -> full GitHub URL
git@github.com:org/repo:main               -> SSH URL for private repos
frappe/frappe:version-15#apps/frappe       -> monorepo subdirectory (repo#path/to/subdir)
frappe/erpnext:a1b2c3d                     -> specific commit SHA as the ref
```

!!! tip "Monorepo apps"
    When an app lives inside a subdirectory of a larger repo (common in company monorepos), use the `#` separator: `org/repo:branch#apps/my-app`. FM clones the repo once and picks the correct subdirectory.

Remove an app from a bench using the bench CLI inside a shell:

```bash
fm shell mybench -c "bench --site mybench.localhost remove-app erpnext"
```

!!! tip
    `fm info mybench` shows the list of installed apps and their versions.

## Updating an app

Apps are updated through the standard Frappe `bench update` workflow inside the bench container:

```bash
# Update all apps
fm shell mybench -c "bench update"

# Update a specific app only
fm shell mybench -c "bench update --app erpnext"
```

`bench update` pulls the latest code, runs migrations, and rebuilds assets. This can take a few minutes.

!!! tip
    For a safer update in production, drain workers before running `bench update` so no jobs are interrupted. See the [fmx guide](fmx.md) for how to do this.

## Opening an interactive shell

For anything not covered by a single command, drop into an interactive shell:

```bash
fm shell mybench
```

From here you have full access to the `bench` CLI and all standard Frappe tools (`bench migrate`, `bench build`, `bench console`, etc.).
