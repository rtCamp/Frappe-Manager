
# Restarting Frappe Services Without Breaking Things

So you need to restart your Frappe services. Easy enough, right? Just run `fmx restart` and you're done!

Well... not quite. If you're running a production system, there's a bit more to consider. Background workers might be crunching through important tasks, and if you kill them mid-job, you could end up with corrupted data or angry users.

This guide will help you understand your options so you can restart services without shooting yourself in the foot.

## What Actually Happens During a Restart?

When Frappe restarts, several things get recycled:
- **Web server** (Gunicorn/Nginx): Stops serving requests, then starts back up
- **Background workers**: Get killed and replaced with fresh ones  
- **Scheduler**: The thing that triggers your cron jobs also gets restarted

During this process, your site is temporarily unavailable. The question is: how do you handle the background jobs that might be running?

## Background Workers: The Hidden Heroes

These workers handle the stuff that happens behind the scenes:
- Sending emails
- Generating reports
- Processing bulk operations
- Running scheduled tasks

If you kill a worker while it's halfway through updating 10,000 records... well, you get the idea.

## Check What's Running First

Before you restart, take a quick look at what's happening:

**Quick check with bench:**
```bash
bench doctor
# or for a specific site:
bench --site your.site.name doctor
```

**Check the web interface:**
Go to `/app/rq-jobs` in your Frappe site to see active jobs and queues.

## Your Restart Options

### 1. `fmx restart` (The Default)

**What it does:** Sends a "please stop" signal to all processes. Workers get a grace period to finish, then get forcibly killed if they don't.

**When to use:** 
- Development environments
- Emergencies where you need services back ASAP
- When you know no critical jobs are running

**Pros:** Fast
**Cons:** Jobs might get cut off mid-way

### 2. `fmx restart --wait-workers` (The Safe Option)

**What it does:** 
- Tells Redis to stop accepting new jobs
- Waits for all current jobs to finish
- Only then restarts services
- Resumes normal operation afterward

**When to use:**
- Production environments
- When critical jobs are running
- Before database migrations
- Basically anytime you care about data integrity

**Pros:** Maximum safety, no job interruption
**Cons:** Could take a while if jobs are long-running

### 3. `fmx restart --no-wait-workers` (The Middle Ground)

**What it does:**
- Tells Redis to stop accepting new jobs
- Signals workers to finish current jobs gracefully
- Restarts services immediately
- Old workers might still be finishing up in the background

**When to use:**
- You want RQ coordination but can't wait
- Routine updates where immediate service availability matters
- When jobs are typically short-running

**Pros:** Faster than waiting, still provides some coordination
**Cons:** No guarantee jobs finish before workers are replaced

### 4. `fmx restart --suspend-rq` (The Coordinator)

**What it does:**
- Suspends job processing via Redis flag
- Does normal restart without waiting
- Resumes job processing afterward

**When to use:**
- You want RQ coordination without the waiting
- Testing restart procedures

## Database Migrations

If you're running `fmx restart --migrate`, you're changing the database structure. This is where things get really important:

**Always use `--wait-workers` with migrations.** Here's why:

- **Good:** `fmx restart --migrate --wait-workers`
  - Jobs finish with the old database structure
  - Database gets updated
  - New workers start with the new structure
  - Everything stays consistent

- **Bad:** `fmx restart --migrate --no-wait-workers`
  - Database structure changes while old workers are still running
  - Old workers try to access data that no longer matches their expectations
  - Errors, corruption, sadness

## How to Choose

Here's the decision process, simplified:

**Is your system completely broken?**
→ Use `fmx restart` and fix it fast

**Are critical jobs running that MUST finish?**
→ Use `fmx restart --wait-workers`

**Is this a database migration?**
→ Use `fmx restart --migrate --wait-workers` (seriously, don't skip this)

**Is this production and you want to be safe?**
→ Use `fmx restart --wait-workers`

**Is this development and you want to move fast?**
→ Use `fmx restart`

**Need a compromise between speed and safety?**
→ Use `fmx restart --no-wait-workers`

## Common Scenarios

**"I'm deploying new code to production"**
- If no database changes: `fmx restart --no-wait-workers`
- If database changes: `fmx restart --migrate --wait-workers`

**"I'm developing and restarting constantly"**
- Use `fmx restart` for speed

**"Something's broken and users are complaining"**
- If system is down: `fmx restart` (fast recovery)
- If system is limping: `fmx restart --wait-workers` (protect running jobs)

**"I need to restart but there's a big report running"**
- Use `fmx restart --wait-workers` and grab some coffee

## Useful Options

- `--wait-workers-timeout 300`: Don't wait forever (default: 5 minutes)
- `--migrate-timeout 600`: Give migrations more time if needed  
- `--wait-workers-verbose`: See what workers are doing while you wait
- `--force-kill-timeout 30`: Kill stubborn processes after 30 seconds

## When Things Go Wrong

**Workers won't stop:**
- Check `fmx status -v` to see what's stuck
- Look at Background Jobs in the web interface
- Increase the timeout or use `--force-kill-timeout`

**RQ suspension fails:**
- Check your Redis connection in `common_site_config.json`
- Make sure Redis is actually running

**Jobs got interrupted:**
- Check Background Jobs for failed jobs
- Use `bench --site your.site.name rq retry-failed` to retry them

**Services won't start after restart:**
- Check logs with `fmx logs [service_name] --tail 100`

## The Bottom Line

When in doubt, err on the side of caution. A few extra seconds of downtime beats explaining to users why their data got corrupted.

- **Development**: Speed matters, use defaults
- **Production**: Safety matters, use `--wait-workers`  
- **Migrations**: Always use `--wait-workers`, no exceptions
- **Emergencies**: Fix fast, clean up later

The `fmx restart` command is powerful, but with great power comes great responsibility. Choose your options wisely, and your users (and your sleep schedule) will thank you.
