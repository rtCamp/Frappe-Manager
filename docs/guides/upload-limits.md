# Upload Limits

There are three places upload limits can be set. We list them from global (affects every bench) to the Frappe app setting.

1) Global nginx (all benches)

File: `~/frappe/services/nginx-proxy/confd/nginx.conf` (site-wide proxy config)

Change `client_max_body_size 100M;` and then reload the proxy with a restart of the service or `fm restart`.

2) Bench nginx (one bench)

File: `~/frappe/sites/<bench>/configs/nginx/conf.d/default.conf`

Edit the `client_max_body_size` value and then run `fm restart mybench`.

3) Frappe system settings

Inside Frappe, go to System Settings → Attachments and set the maximum file size. This overrides site-level settings.

Easy option:

```bash
fm update mybench --upload-limit 100M
```

!!! tip
    The `fm update` command updates the right file for you and restarts services, which is the simplest, safest approach.
