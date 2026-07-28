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

Set Mailpit as the default mail server for Frappe (writes `mail_server`, `mail_port`, and `disable_mail_smtp_authentication` into `common_site_config.json`):

```bash
fm update mybench --mailpit-as-default-mail-server
```

If you need the SMTP endpoint manually (inside the Docker network): host `fm__<benchname>__mailpit`, port 1025, where `<benchname>` is the full bench name with dots replaced by underscores. For example, bench `mybench` (full name `mybench.localhost`) → host `fm__mybench_localhost__mailpit`.

!!! note
    Mailpit keeps up to 5,000 messages. Older messages are automatically deleted when the limit is reached.

!!! warning
    Admin tools are enabled by default on `dev` benches and disabled on `prod` benches at create time. Switching environments later does not toggle them — disable explicitly before going live.
