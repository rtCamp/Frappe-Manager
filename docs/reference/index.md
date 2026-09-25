# Reference

Lookup, not learning. These pages are written to be searched and deep-linked rather than read front to back. For the mental model start at [How fm works](../concepts/index.md); for workflows see the [Guides](../guides/index.md) and [Deployment](../deploy/index.md).

<div class="grid cards" markdown>

-   :material-cog:{ .lg .middle } **[Configuration files](configuration.md)**

    ---

    Every key in `fm_config.toml` and `bench_config.toml`: default, type, the environment variable that overrides it, and the command that changes it. One named anchor per key, for example [`restart_policy`](configuration.md#restart-policy) and [`[workers]`](configuration.md#workers).

-   :material-text-box-search:{ .lg .middle } **[Logs and debugging](logs.md)**

    ---

    Where every log lives, what `fm.log` and `fm logs` each show you, the JSON access log both nginx hops share, the console verbosity flags, and how to rotate the logs nothing rotates for you.

-   :material-database-arrow-up:{ .lg .middle } **[Migrations](migrations.md)**

    ---

    How the two migration tiers behave: why `fm migrate` refuses a stale services tier, why a stale bench is refused rather than used, what `--on-failure` decides, and which backups can be skipped.

-   :material-history:{ .lg .middle } **[Migration history](migration-history.md)**

    ---

    What each shipped migration actually changed, version by version, plus the extra backup artifacts individual versions take. The archaeology page for installs that sat out several releases.

-   :material-lock:{ .lg .middle } **[Process locks](locks.md)**

    ---

    How fm processes on one host keep out of each other's way: the host-wide migration lock, the per-bench lock behind `switch` and `bake` refusals, and why observation commands are never fenced.

-   :material-sitemap:{ .lg .middle } **[Architecture](architecture.md)**

    ---

    The runtime topology: which containers exist in each tier, the shared networks and volumes, the on-disk layout of a bench, and which process serves what inside the bench container.

</div>

Command flags are documented per command under [Commands](../commands/index.md).
