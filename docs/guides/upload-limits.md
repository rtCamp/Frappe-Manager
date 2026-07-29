# Upload Limits

Use fm to change the maximum file upload size for a bench.

```bash
fm update mybench --upload-limit 100M
```

Valid values look like `50M`, `100M`, or `1G` (digits followed by `M` or `G`). The default and key details live in the [`upload_limit` reference](../reference/configuration.md#upload-limit).

One command updates every layer that enforces the limit:

- `site_config.json`: Frappe's `max_file_size` (in bytes)
- the bench's nginx container: `client_max_body_size`
- the global nginx proxy: per-domain `vhost.d` entries for all of the bench's domains

Both nginx layers are reloaded automatically; no restart needed.

!!! tip
    Avoid editing proxy or nginx files by hand; `fm update` writes all three layers consistently and reloads nginx for you. Frappe's own **System Settings → Max Attachment Size** can still impose a lower limit from inside the application.

See also: [`upload_limit`](../reference/configuration.md#upload-limit) in the configuration reference for the config key this command writes.
