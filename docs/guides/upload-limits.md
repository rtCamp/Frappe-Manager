# Upload Limits

Use fm to change the maximum file upload size for a bench.

```bash
fm update mybench --upload-limit 100M
```

Valid values look like `50M`, `100M`, or `1G`.

Where the setting takes effect:

- Global proxy (affects all benches) and per-bench nginx proxy are updated by fm when you use `fm update`.
- Frappe's System Settings → Attachments can also limit uploads from inside the application.

!!! tip
    Avoid editing proxy or nginx files by hand. Use `fm update` so FM updates the right configuration and restarts services.
