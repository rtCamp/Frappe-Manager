# Concepts

Five minutes here saves hours later. Everything fm does hangs off one mental model:

A **bench** is one Frappe site with everything it needs (web server, workers, Redis, nginx) running as an isolated set of containers. Two independent axes describe every bench:

| Axis | Question it answers | Values |
|---|---|---|
| **[Runtime](runtimes.md)** | *Where does the code live?* | `mount`: an editable workspace on your disk · `image`: an immutable, pre-built Docker image |
| **[Environment](../guides/environments.md)** | *How does the web process run?* | `dev`: auto-reloading dev server · `prod`: Gunicorn, restart-on-crash |

One machine runs many benches, and they share two **global services**: a single MariaDB server (`global-db`) holding every bench's database, and one `nginx-proxy` on ports 80/443 routing requests to the right bench by domain. `fm services` manages these; everything else is per-bench.

The axes combine freely:

| | `dev` | `prod` |
|---|---|---|
| **`mount`** | The daily development loop: edit code, see it live | Simple production: editable code, production web server |
| **`image`** | Testing a release image locally | Immutable production: deploys, rollbacks, rolling swaps |

---

## The Model

Read the two axes first:

<div class="grid cards" markdown>

-   :lucide-box:{ .lg .middle } &nbsp; **[Runtimes: Mount vs Image](runtimes.md)**

    ---

    The fundamental choice: editable workspace or immutable image. What each can do, and how to move between them.

-   :lucide-toggle-left:{ .lg .middle } &nbsp; **[Environments: Dev vs Prod](../guides/environments.md)**

    ---

    The web-process mode: auto-reloading dev server vs Gunicorn, and the defaults that come with each.

</div>

---

## The Machinery

Then how a bench actually runs:

<div class="grid cards" markdown>

-   :lucide-globe:{ .lg .middle } &nbsp; **[Web Serving & Concurrency](web-serving.md)**

    ---

    The request path and the web process: dev server vs Gunicorn, workers and threads, when to tune.

-   :lucide-list-checks:{ .lg .middle } &nbsp; **[Background Jobs & Workers](background-jobs.md)**

    ---

    RQ queues, the worker containers, the scheduler, safe restarts and draining.

-   :lucide-network:{ .lg .middle } &nbsp; **[Architecture](../reference/architecture.md)**

    ---

    What's actually running: containers per bench, shared global services, networks, and volumes.

</div>
