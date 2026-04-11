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
