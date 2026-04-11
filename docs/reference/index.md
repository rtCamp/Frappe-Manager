# Reference

Technical details about how Frappe Manager works under the hood.

<div class="grid cards" markdown>

-   :lucide-boxes:{ .lg .middle } &nbsp; **Architecture**

    ---

    How Frappe Manager is structured — the services it runs, how benches are isolated, and what the directory layout looks like.

    [:octicons-arrow-right-24: Architecture](architecture.md)

-   :lucide-file-sliders:{ .lg .middle } &nbsp; **Configuration Files**

    ---

    Every configuration file Frappe Manager reads and writes, what each setting does, and where the files live.

    [:octicons-arrow-right-24: Configuration Files](configuration.md)

-   :lucide-cpu:{ .lg .middle } &nbsp; **Workers & Background Jobs**

    ---

    How Frappe's background job system works, which worker queues exist, and how to tune them.

    [:octicons-arrow-right-24: Workers & Background Jobs](workers.md)

-   :lucide-arrow-up-circle:{ .lg .middle } &nbsp; **Migrations**

    ---

    What happens when you run `fm migrate`, and how Frappe Manager upgrades existing benches to new versions.

    [:octicons-arrow-right-24: Migrations](migrations.md)

-   :lucide-file-text:{ .lg .middle } &nbsp; **Logs & Debugging**

    ---

    Where logs are stored, how to read them with `fm logs`, and tips for diagnosing common problems.

    [:octicons-arrow-right-24: Logs & Debugging](logs.md)

</div>
