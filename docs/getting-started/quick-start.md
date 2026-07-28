# Quick Start

Create your first bench in three steps.

1) Create a bench (fm will start it automatically):

```bash
fm create mybench
```

2) Open the site in your browser:

```bash
open http://mybench.localhost
```

3) Check bench details:

```bash
fm info mybench
```

Notes:

- The default Administrator account after creation is `Administrator` / `admin`. Change this before using a bench for production.
- To add ERPNext during creation: `fm create mybench --apps erpnext`.

!!! tip
    Use `fm list` to see all benches and their status.

---

**Ready to ship?** fm can bake your bench into an immutable Docker image and deploy it with a zero-downtime rolling swap:

```bash
fm deploy mybench             # bake an image from the bench and deploy it
fm switch mybench --previous  # roll back if anything goes wrong
fm prune mybench              # clean up old releases
```

See the [Deployment guide](../guides/deployment.md) for the full pipeline.

---

**Where to go next:**

- [Guides](../guides/index.md) - environments, SSL, app management, VSCode, and more
- [App Management](../guides/app-management.md) - install ERPNext or other apps
- [Commands](../commands/index.md) - full reference for every `fm` command
