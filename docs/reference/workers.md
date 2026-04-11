# Workers & Background Jobs

Workers run background tasks like email sending, long-running jobs, and scheduled tasks.

Decision flow:

1. If you are deploying to production, enable the full worker set and use `--wait-workers` when performing migrations.
2. For development, smaller worker sets and `--no-wait-workers` make iteration faster.

Always use `--wait-workers` or `--wait-workers-timeout` for risky migrations. This waits for running jobs to finish before stopping workers.

!!! warning
    Restarting workers while jobs are running can cause partial work or failures. Prefer graceful waits for production changes.
