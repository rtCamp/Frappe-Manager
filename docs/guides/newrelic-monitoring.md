# New Relic APM Monitoring

Monitor your Frappe bench in production with New Relic's Application Performance Monitoring (APM).

FM integrates the New Relic Python agent to automatically capture web requests, background jobs, scheduled tasks, database queries, and errors — giving you full visibility into your application's performance.

---

## Prerequisites

- A New Relic account ([sign up free](https://newrelic.com/signup))
- Your New Relic license key (found in **Account Settings → API Keys**)
- A running bench created with `fm create`

---

## Quick Start

Enable New Relic monitoring on any bench with a single command:

```bash
fm update mybench --newrelic enable
```

FM will prompt you for:

1. **License Key** — Your New Relic license key (starts with a hex string ending in `NRAL`)

That's it. Your bench is now reporting to New Relic.

---

## What Gets Monitored

New Relic automatically instruments:

### Web Requests (Gunicorn)
- **Request duration** — Time spent on each HTTP request
- **Throughput** — Requests per minute
- **Error rate** — Failed requests and exceptions
- **External calls** — API calls to third-party services

### Background Jobs (RQ Workers)
- **Job execution time** — How long each worker task takes
- **Queue depth** — Number of pending jobs
- **Job failures** — Failed tasks with full error context
- **Custom transaction names** — Extracts actual Frappe job names (e.g., `frappe.email.queue.flush`)

### Scheduler
- **Scheduled job execution** — Tracks all cron-style tasks
- **Job frequency** — How often each scheduled task runs

### Database Queries
- **SQL statements** — Obfuscated queries with execution time
- **Slow query detection** — Highlights queries taking >0.5s
- **Query explain plans** — Available for slow queries

### Redis Operations
- **Cache hit rates** — How often Redis cache is used
- **Command latency** — Time spent on Redis operations

---

## Configuration

### Viewing Current Settings

Check if New Relic is enabled:

```bash
fm info mybench
```

Look for the `newrelic_enabled` field in the output.

### Disabling New Relic

Stop sending data to New Relic:

```bash
fm update mybench --newrelic disable
```

This removes the New Relic agent from the bench. Data already sent to New Relic remains in your account.

### Changing License Key

To update your license key:

```bash
# Disable first
fm update mybench --newrelic disable

# Re-enable with new key
fm update mybench --newrelic enable
```

---

## Accessing Your Data

After enabling New Relic:

1. Log in to [New Relic](https://one.newrelic.com/)
2. Go to **APM & Services**
3. Find your application: `Frappe - mybench.localhost` (or your bench name)

### Key Dashboards

**Overview** — High-level health: response time, throughput, error rate

**Transactions** — Drill into specific web requests or background jobs

**Databases** — See which SQL queries are slowest

**Errors** — Full stack traces for every exception

**Distributed Tracing** — Follow a request across services

---

## Transaction Traces

FM configures New Relic to capture **every transaction** (not just slow ones).

This means you can inspect the full execution breakdown for any request or job — even fast ones.

**Why?** Frappe background jobs often complete in 0.1–0.2 seconds. Without capturing all transactions, you'd miss most of them.

### Viewing Traces

1. Go to **Transactions** in your New Relic dashboard
2. Select any transaction
3. Click **Transaction traces** → View details
4. See the full call stack with timing for each function

---

## Performance Impact

The New Relic agent adds minimal overhead:

- **Web requests**: ~1-2ms per request
- **Background jobs**: ~5-10ms per job (includes agent registration + shutdown)
- **Memory**: ~10-20MB per process

This overhead is acceptable for production monitoring. If you need to disable it temporarily:

```bash
fm update mybench --newrelic disable
fm restart mybench
```

---

## Advanced: Custom Function Traces

By default, FM does **not** instrument individual Python functions. This keeps the integration generic and avoids noise.

New Relic already auto-instruments:

- Database queries (MariaDB/PostgreSQL)
- Redis operations
- HTTP requests (outbound API calls)
- Standard library operations

If you need **additional function-level detail** (e.g., to debug a slow code path), you can add custom instrumentation.

### Adding Custom Function Traces

!!! warning "User Responsibility"
    Custom function traces are **not** included in FM's default config. You must add them manually and test thoroughly.
    
    Bad instrumentation can cause circular imports or performance issues.

#### Step 1: Identify Target Functions

Use New Relic's **Thread Profiler** first to find actual bottlenecks before adding traces.

1. Go to your app in New Relic
2. Click **Settings → Thread profiler**
3. Run a profile during typical load
4. Identify hot functions from the flame graph

#### Step 2: Add Function Traces to Config

SSH into your bench container:

```bash
fm shell mybench
```

Edit the New Relic config:

```bash
vi /workspace/frappe-bench/config/newrelic.ini
```

Add function trace sections at the end:

```ini
[function-trace:my-custom-trace]
enabled = true
function = my_module.my_function
name = MyApp/my_function
group = Python/MyApp
```

**Example — Trace a specific DocType method:**

```ini
[function-trace:custom-doctype-save]
enabled = true
function = my_custom_app.my_custom_app.doctype.my_doctype.my_doctype.MyDocType:validate
name = MyApp/MyDocType/validate
group = Python/DocTypes
```

#### Step 3: Test Carefully

Restart the bench and monitor logs for errors:

```bash
exit  # Exit container shell
fm restart mybench
fm logs mybench --server
```

Look for `INSTRUMENTATION ERROR` in the logs. If you see any:

1. The function path is wrong
2. The module has circular imports
3. The function is defined too early in the import cycle

Remove the problematic trace and try a different function.

#### Step 4: Verify in New Relic

Go to **Transactions → Select a transaction → Transaction trace**.

Your custom function should now appear in the breakdown with its execution time.

### Example: Common Frappe Functions to Trace

These are examples only — test each one before using in production:

```ini
# Trace email sending
[function-trace:email-send]
enabled = true
function = frappe.email.queue.flush
name = Frappe/Email/flush_queue
group = Python/Email

# Trace report generation
[function-trace:report-execute]
enabled = true
function = frappe.desk.query_report.run
name = Frappe/Report/execute
group = Python/Reports

# Trace API handler
[function-trace:api-handler]
enabled = true
function = frappe.handler.handle
name = Frappe/API/handle_request
group = Python/API
```

!!! danger "Avoid Over-Instrumentation"
    Do **not** trace high-frequency functions like:
    
    - `frappe.get_doc` (called thousands of times per request)
    - `Document.run_method` (called 6–2,792 times per transaction)
    - `frappe.db.get_value` (called hundreds of times)
    
    These will flood your traces with noise and hurt performance.

### When to Use Custom Traces

**Good reasons:**

- Debugging a specific slow endpoint
- Profiling a custom app's business logic
- Measuring time spent in external API calls

**Bad reasons:**

- "I want to see everything" — Use Thread Profiler instead
- Monitoring core Frappe functions — NR already captures DB/Redis/HTTP
- Following best practices from blog posts — Those are for different apps

---

## Troubleshooting

### No Data in New Relic

**Check if the agent is installed:**

```bash
fm shell mybench
/workspace/frappe-bench/env/bin/python -c "import newrelic; print(newrelic.version)"
```

If you see `ModuleNotFoundError`, restart the bench:

```bash
fm restart mybench
```

**Check if services are reporting:**

```bash
fm logs mybench --server | grep -i "reporting to"
```

You should see lines like:

```
Reporting to: https://rpm.newrelic.com/accounts/XXXXXX/applications/XXXXXXXX
```

### Instrumentation Errors

If you see `INSTRUMENTATION ERROR` in logs:

```bash
fm logs mybench --server | grep "INSTRUMENTATION ERROR"
```

This usually means:

1. **Custom function traces are misconfigured** — Remove them from `newrelic.ini`
2. **Circular imports** — The traced function imports a module that imports New Relic
3. **Missing module** — The function path is wrong

**Fix:**

```bash
fm shell mybench
vi /workspace/frappe-bench/config/newrelic.ini
# Remove the [function-trace:*] section causing errors
# Save and exit
exit
fm restart mybench
```

### High Memory Usage

If the agent is using excessive memory (>50MB per process):

1. **Check for custom traces** — Remove any high-frequency function traces
2. **Reduce trace sampling** — Edit `newrelic.ini`:

```ini
[newrelic]
# Reduce traces captured per minute
transaction_tracer.transaction_threshold = 0.5  # Only trace >0.5s
span_events.max_samples_stored = 1000  # Lower from 2000
```

3. **Restart:**

```bash
fm restart mybench
```

### Workers Not Reporting

If web requests appear but background jobs don't:

```bash
fm shell mybench
tail -100 /workspace/frappe-bench/logs/worker.error.log | grep "NR RQ Hook"
```

You should see:

```
[NR RQ Hook] Worker.perform_job instrumented successfully
[NR RQ Hook] Registering app in child PID=1234
[NR RQ Hook] Transaction name: frappe.email.queue.flush
```

If missing, check the RQ hooks file:

```bash
ls -la /workspace/frappe-bench/config/newrelic_rq_hooks.py
```

If the file doesn't exist, disable and re-enable New Relic:

```bash
exit  # Exit container
fm update mybench --newrelic disable
fm update mybench --newrelic enable
```

---

## Removing New Relic

To completely remove New Relic from a bench:

```bash
# Disable monitoring
fm update mybench --newrelic disable

# Restart to clear agent from memory
fm restart mybench
```

The New Relic agent package remains installed in the Python environment but won't be loaded.

To fully uninstall:

```bash
fm shell mybench
uv pip uninstall newrelic --python /workspace/frappe-bench/env/bin/python
exit
```

---

## FAQ

### Does New Relic work with ERPNext?

Yes. New Relic monitors any Frappe-based application, including ERPNext, HRMS, Healthcare, and custom apps.

### Can I use New Relic in development benches?

Yes, but it's usually unnecessary. Use New Relic in production or staging where real traffic patterns matter.

### Does New Relic capture sensitive data?

**SQL queries are obfuscated** — parameter values are replaced with `?`.

**HTTP headers are filtered** — Authorization and Cookie headers are excluded by default.

**Error data includes stack traces** — If your code logs sensitive info, it may appear in error traces.

To exclude additional data, edit `newrelic.ini`:

```ini
[transaction_tracer]
attributes.exclude = request.headers.x-custom-token request.parameters.password
```

### What's the cost?

New Relic offers a free tier with:

- 100 GB data ingest per month
- Full platform access
- 1 free full-access user

Paid plans start at $99/month for more data and users.

See [New Relic pricing](https://newrelic.com/pricing) for details.

### Can I monitor multiple benches with one license key?

Yes. Each bench reports as a separate application in New Relic (named `Frappe - <benchname>`).

All benches under the same license key share the data ingest quota.

---

## Related Commands

- [`fm update`](../commands/update.md) — Enable/disable New Relic
- [`fm info`](../commands/info.md) — View New Relic status
- [`fm logs`](../commands/logs.md) — Check New Relic agent logs
- [`fm shell`](../commands/shell.md) — Access container for config edits

---

## Support

- New Relic Documentation: [Python Agent Guide](https://docs.newrelic.com/docs/apm/agents/python-agent/)
- FM Issues: [Report a problem](https://github.com/rtCamp/Frappe-Manager/issues)
