# App Management

Install apps when you create a bench, or add and update them later with `fm update --apps`.

Install during create:

```bash
fm create mybench --apps erpnext
```

Add an app to an existing bench with `fm update --apps`:

```bash
fm update mybench --apps erpnext
```

FM clones the app, installs its dependencies, installs it to the site, builds its assets, and runs `bench migrate`.

!!! note "Mount benches only"
    `--apps` needs an editable workspace (the default `mount` runtime). On an `image` bench, app code is baked into the image - ship changes with `fm deploy`, or demote first with `fm update mybench --runtime mount`. See the [Deployment guide](../deploy/index.md).

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
frappe/erpnext:<40-char-sha>               -> full commit SHA as the ref
```

!!! tip "Monorepo apps"
    When an app lives inside a subdirectory of a larger repo (common in company monorepos), use the `#` separator: `org/repo:branch#apps/my-app`. FM clones the repo once and picks the correct subdirectory.

Remove an app from a bench using the bench CLI inside a shell:

```bash
fm shell mybench -c "bench --site mybench.localhost remove-app erpnext"
```

!!! tip
    `fm info mybench` shows the list of installed apps and their versions.

## Updating or switching an app's version

`fm update --apps` also works for apps that are already installed - it grafts the requested ref onto the running bench:

```bash
# Move an installed app to another branch or tag
fm update mybench --apps erpnext:version-15

# Several apps at once (repeatable)
fm update mybench --apps erpnext:version-15 --apps hrms:version-15
```

For each app FM replaces the app's code with a fresh clone at the requested ref - the old code is **stashed, never deleted** - then reinstalls dependencies, rebuilds that app's assets, and runs `bench migrate`.

!!! tip
    For a safer update in production, drain in-flight jobs first: `fm restart mybench --drain` waits for running background jobs to finish. See the [fmx guide](fmx.md) for finer-grained control from inside the container.

## Opening an interactive shell

For anything not covered by a single command, drop into an interactive shell:

```bash
fm shell mybench
```

From here you have full access to the `bench` CLI and all standard Frappe tools (`bench migrate`, `bench build`, `bench console`, etc.).
