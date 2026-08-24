# Reference

Lookup, not learning. Every page here is meant to be searched and deep-linked rather than read front to back. For the mental model start at [Concepts](../concepts/index.md); for workflows see the [Guides](../guides/index.md) and [Deployment](../deploy/index.md).

<div class="grid cards" markdown>

-   :material-cog:{ .lg .middle } **[Configuration Files](configuration.md)**

    ---

    Every key in `fm_config.toml` and `bench_config.toml`: defaults, types, the environment variables that override them, and where the files live. One named anchor per key, for example [`restart_policy`](configuration.md#restart-policy) and [`[workers]`](configuration.md#workers).

-   :material-text-box-search:{ .lg .middle } **[Logs & Debugging](logs.md)**

    ---

    Where every log lives, what `fm.log` and `fm logs` each show you, the JSON access log both nginx hops share, the console verbosity flags, and how to rotate the logs nothing rotates for you.

-   :material-database-arrow-up:{ .lg .middle } **[Migrations](migrations.md)**

    ---

    What `fm migrate` does, why a stale bench is refused rather than used, how backups are grouped per migration version, what `--on-failure` decides, and how to restore by hand.

</div>
