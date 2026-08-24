# Quick Start

Two commands get you a running bench. This assumes fm is already installed; if not, start with the [Installation guide](installation.md).

## 1. Create the bench

```bash
fm create mybench
```

The bench name is also its domain. A bare name has no dot in it, so fm appends `.localhost` and this bench answers on `mybench.localhost`. Creating a bench also starts it, and fm prints the URL, the login credentials and the installed apps when it finishes.

!!! tip "Need ERPNext?"
    `--apps` is repeatable, and each app can be pinned to a branch, tag, or commit:

    ```bash
    fm create mybench --apps erpnext:version-15 --apps hrms
    ```

## 2. Open the site

Visit **http://mybench.localhost** and log in as `Administrator` with the password `admin`.

Change that password before the bench holds anything you care about. `bench set-admin-password` is a Frappe command, so run it inside the bench:

```bash
fm shell mybench -c "bench set-admin-password 'a-better-password'"
```

## Everyday commands

```bash
fm list             # every bench, with status, runtime and installed apps
fm info mybench     # this bench's URL, credentials, apps and service state
fm stop mybench     # free the resources; fm start mybench brings it back
fm logs mybench -f  # follow the web server log
```

!!! note "Names with a dot in them"
    `fm create mybench.test` is used verbatim rather than getting `.localhost` appended, and nothing resolves it for you. Add a `hosts` file entry pointing the name at `127.0.0.1`, or use a real domain with [SSL](../guides/ssl.md).

---

**Where to go next:**

- [Understand what you just made](../concepts/index.md): five minutes on the mental model
- [Guides](../guides/index.md): environments, SSL, app management, VS Code, and more
- [App Management](../guides/app-management.md): install ERPNext or other apps into an existing bench
- [Commands](../commands/index.md): full reference for every `fm` command

**Ready to ship?** fm can bake your bench into an immutable Docker image and deploy it with a zero-downtime rolling swap, and roll back in one command. See the [Deployment guide](../deploy/index.md).
