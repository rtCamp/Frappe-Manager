# Your first bench

This assumes fm is installed. If not, start with [Installation](installation.md).

## Create the bench

```bash
fm create mybench
```

The bench name is also its domain. A bare name has no dot in it, so fm appends `.localhost` and this bench answers on `mybench.localhost`. Creating a bench also starts it; fm prints the URL, the login credentials and the installed apps when it finishes.

!!! tip "Need ERPNext?"
    `--apps` is repeatable, and each app can be pinned to a branch, tag, or commit:

    ```bash
    fm create mybench --apps erpnext --apps hrms
    ```

!!! note "Names with a dot in them"
    `fm create mybench.test` is used verbatim rather than getting `.localhost` appended, and nothing resolves it for you. Add a `hosts` file entry pointing the name at `127.0.0.1`, or use a real domain with [HTTPS](../guides/ssl.md).

## Open the site

Visit `http://mybench.localhost` and log in as `Administrator`. fm mints a random password for each site it creates and records it, so read it off the card:

```bash
fm info mybench
```

Pick your own instead with `fm create mybench --admin-pass 'your-password'`, or change it later with Frappe's own command inside the bench:

```bash
fm shell mybench -c "bench set-admin-password 'a-better-password'"
```

## The commands you will use daily

```bash
fm list             # every bench, with status, runtime and installed apps
fm info mybench     # this bench's URL, credentials, apps and service state
fm stop mybench     # free the resources; fm start mybench brings it back
fm logs mybench -f  # follow the web server log
fm shell mybench    # a shell inside the bench container
```

## Next

Read [How fm works](../concepts/index.md). It is five minutes and it explains the two choices, runtime and environment, that every later decision depends on.
