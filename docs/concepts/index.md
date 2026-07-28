# Concepts

Five minutes here saves hours later. Everything fm does hangs off one mental model:

A **bench** is one Frappe site with everything it needs - web server, workers, Redis, nginx - running as an isolated set of containers. Two independent axes describe every bench:

| Axis | Question it answers | Values |
|---|---|---|
| **[Runtime](runtimes.md)** | *Where does the code live?* | `mount` - an editable workspace on your disk · `image` - an immutable, pre-built Docker image |
| **[Environment](../guides/environments.md)** | *How does the web process run?* | `dev` - auto-reloading dev server · `prod` - Gunicorn, restart-on-crash |

The axes combine freely:

| | `dev` | `prod` |
|---|---|---|
| **`mount`** | The daily development loop - edit code, see it live | Simple production: editable code, production web server |
| **`image`** | Testing a release image locally | Immutable production - deploys, rollbacks, rolling swaps |

Read them in this order:

<div class="grid cards" markdown>

-   :lucide-box:{ .lg .middle } &nbsp; **[Runtimes - Mount vs Image](runtimes.md)**

    ---

    The fundamental choice: editable workspace or immutable image. What each can do, and how to move between them.

-   :lucide-toggle-left:{ .lg .middle } &nbsp; **[Environments - Dev vs Prod](../guides/environments.md)**

    ---

    The web-process mode: auto-reloading dev server vs Gunicorn, and the defaults that come with each.

-   :lucide-network:{ .lg .middle } &nbsp; **[Architecture](../reference/architecture.md)**

    ---

    What's actually running: containers per bench, shared global services, networks, and volumes.

</div>
