# Admin tools (Mailpit & Adminer)

Admin tools are useful in development. They are path-routed under your bench URL.

Enable admin tools:

```bash
fm update mybench --admin-tools enable
```

Disable:

```bash
fm update mybench --admin-tools disable
```

Access:

- Mailpit: http://mybench.localhost/mailpit/
- Adminer: http://mybench.localhost/adminer/

Both are protected with HTTP basic auth. Run `fm info mybench` to see the credentials.

Set Mailpit as the default mail server for Frappe:

```bash
fm update mybench --mailpit-as-default-mail-server
```

Mailpit SMTP (inside the Docker network): host mybench-mailpit port 1025

!!! warning
    Admin tools are not enabled by default in production.
