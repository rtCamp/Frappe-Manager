# Reference

Technical reference documentation for Frappe Manager internals. These pages serve lookup - not learning. For the mental model start at [Concepts](../concepts/index.md); for workflows see the [Guides](../guides/index.md) and [Deployment](../guides/deployment.md).

---

## Core Reference

<div class="grid cards" markdown>

-   :material-run-fast:{ .lg .middle } **[Workers & Background Jobs](workers.md)**

    ---

    RQ worker architecture, queue types, concurrency tuning, Gunicorn worker configuration.

-   :material-cog:{ .lg .middle } **[Configuration Files](configuration.md)**

    ---

    Every config key in `fm_config.toml` and `bench_config.toml` - defaults, types, env vars, file locations.

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

    - [`configuration.md#restart-policy`](configuration.md#restart-policy) - restart policy options
    - [`../guides/deployment.md#configuration-reference`](../guides/deployment.md#configuration-reference) - deploy config tables

    **Fast lookup:** Use browser **Ctrl+F** / **Cmd+F** within each page to jump to specific terms.
