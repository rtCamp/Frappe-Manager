# App Management

This guide shows how to install official apps like ERPNext and private apps from GitHub, or create a new app.

Install apps during create:

```bash
fm create mybench --apps frappe:version-16 --apps erpnext:version-16
```

Install an app from inside the bench shell:

```bash
fm shell mybench
# inside the shell
bench get-app git+https://github.com/frappe/erpnext
bench --site site1.local install-app erpnext
```

Install a private GitHub app using a token:

```bash
fm create mybench --apps private-app:main --github-token YOUR_TOKEN
```

Create a new app scaffold:

```bash
fm shell mybench
# inside
bench new-app my_custom_app
```

!!! tip
    When installing multiple apps, give the CLI a little time to finish downloads — network speed is the usual bottleneck.
