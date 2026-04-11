# Environments

Frappe Manager supports two environments: dev and prod. They differ in defaults so you can develop quickly or run a stable site.

Summary:

| Feature | dev | prod |
|---|---:|---:|
| Frappe developer mode | auto-enabled | off by default |
| Admin tools (Mailpit, Adminer) | enabled | disabled |
| Container restart policy | no | unless-stopped |
| Access URL | http://mybench.localhost | your custom domain + SSL |

Create a prod bench:

```bash
fm create mybench --environment prod
```

Switch an existing bench between environments:

```bash
fm update mybench --environment prod
fm update mybench --environment dev
```

Enable developer mode separately if you want dev features in a prod environment:

```bash
fm update mybench --developer-mode enable
```

!!! info
    Switching environment recreates the frappe container and restarts all bench services automatically. You do not need to run `fm restart` afterwards.
