# Reference

Technical reference documentation for Frappe Manager internals. These pages serve lookup — not learning. For how-to guides, see [Guides](../guides/index.md).

---

## Core Reference

<div class="grid cards" markdown>

-   :material-sitemap:{ .lg .middle } **[Architecture](architecture.md)**

    ---

    Service topology, container layout, volume/network architecture, Docker Compose structure.

-   :material-cog:{ .lg .middle } **[Configuration Files](configuration.md)**

    ---

    Every config key in `fm_config.toml` and `bench_config.toml` — defaults, types, env vars, file locations.

-   :material-run-fast:{ .lg .middle } **[Workers & Background Jobs](workers.md)**

    ---

    RQ worker architecture, queue types, concurrency tuning, Gunicorn worker configuration.

</div>

---

## Operations Reference

<div class="grid cards" markdown>

-   :material-text-box-search:{ .lg .middle } **[Logs & Debugging](logs.md)**

    ---

    Log file locations, log levels, CLI verbosity flags, service-specific logging, rotation config.

-   :material-database-arrow-up:{ .lg .middle } **[Migrations](migrations.md)**

    ---

    What `fm migrate` does, backup strategy, failure handling, version upgrade paths, rollback procedures.

</div>

---

## Navigation Tips

!!! tip "Deep Linking"
    All reference pages use named anchors for every configuration key, service name, and log location.

    **Example deep links:**

    - [`configuration.md#restart_policy`](configuration.md#restart_policy) — restart policy options
    - [`workers.md#short-worker`](workers.md#short-worker) — short worker details
    - [`architecture.md#global-services`](architecture.md#global-services) — shared MariaDB and nginx-proxy

    **Fast lookup:** Use browser **Ctrl+F** / **Cmd+F** within each page to jump to specific terms.
