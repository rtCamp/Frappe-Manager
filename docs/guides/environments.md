# Environments

This guide explains the difference between development and production benches, and how to change the environment for a bench.

Development benches use the Frappe dev server and include development packages and live reload so you can iterate quickly. Production benches use Gunicorn (a production web server), fewer developer packages, and more conservative defaults for stability.

How to choose:

- Use development when you are writing code, testing features, or debugging.
- Use production when you want a stable site that mimics a live server.

Switching environments:

```bash
fm update mybench --environment prod
# or back to dev
fm update mybench --environment dev
```

You can also change the bench config file (`bench_config.toml`) and set `environment_type = "dev"` or `"prod"`, then restart the bench.

!!! warning
    Changing environment usually requires a full restart so services pick up the new server (Gunicorn vs dev server). Run `fm restart mybench` after updating.
