# Upload Limits

Change the maximum file upload size for a bench with one command. The bench has to be running: `fm update` refuses a stopped one.

```bash
fm update mybench --upload-limit 100M
```

Valid values are digits followed by `M` or `G`, in either case: `50M`, `100m`, `1G`. The units are binary, so `100M` becomes `104857600` bytes in Frappe's config. The default lives in the [`upload_limit` reference](../reference/configuration.md#upload-limit).

That one command writes every layer that enforces the limit:

- `bench_config.toml`: `upload_limit`, normalised to uppercase
- `workspace/frappe-bench/sites/<bench>/site_config.json`: Frappe's `max_file_size`, in bytes
- `configs/nginx/conf/custom/upload-limit.conf`: `client_max_body_size` for the bench's own nginx
- `services/nginx-proxy/vhostd/<domain>`: `client_max_body_size` for each of the bench's domains, on the global proxy

Both nginx layers are reloaded in place, so nothing restarts, and no container is recreated.

!!! info "There is no compose variable for this"
    The bench's nginx service used to also carry a `CLIENT_MAX_BODY_SIZE` environment variable, described here as the value the proxy reads. It was read by nothing: the pinned `jwilder/nginx-proxy` template contains no `client_max_body_size`, and neither does any fm template. Because `fm update --upload-limit` does not re-render compose, that copy also went stale the moment the limit changed. It is no longer written, and it is removed from a bench's compose file the next time anything regenerates it.

!!! tip
    Avoid editing proxy or nginx files by hand; `fm update` writes every layer consistently and reloads nginx for you. Frappe's own **System Settings → Max Attachment Size** can still impose a lower limit from inside the application.
