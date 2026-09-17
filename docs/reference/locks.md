# Process Locks

fm processes on one host coordinate through lock files under `~/frappe/locks/`, so two of them can no longer corrupt a host or a bench by running blind to each other. The mechanism is the operating system's `flock`: a hold vanishes the instant its process exits or dies, however it dies, so there are no stale locks to clean up, ever; and acquisition never waits, so the losing side is refused instantly with a sentence naming what is running.

Day to day you will not notice any of this. Locks only speak up when two operations genuinely collide, and then one of them gets a message instead of both getting a corrupted state.

## The host lock: `migration.lock` {#host}

One file for the whole host, held two ways:

| Who | Hold | Meaning |
|---|---|---|
| every ordinary command | shared | "don't migrate under me while I run" |
| `fm migrate`, `fm services migrate`, the gate's inline "Update now" | exclusive | "I am rewriting fm's state; everyone out" |

Shared holds never contend with each other, so any number of ordinary commands run together exactly as before. The refusals appear only around migrations:

```
⛔ bake (pid 4242) is running on this host. Let it finish, then re-run.      # migration during a bake
⛔ migration (pid 4242) is in progress on this host; wait for it to finish.  # any command during a migration
```

A migration can take every bench down and rebuild shared networks, which is why *starting one mid-anything* and *starting anything mid-migration* are both refused rather than interleaved.

## The bench lock: `bench-<name>.lock` {#bench}

One file per bench, so bench A's operations never affect bench B:

| Who | Hold | Meaning |
|---|---|---|
| `fm bake BENCH` | shared | "long read in progress; don't mutate this bench under me" |
| `switch`, `restart`, `delete`, `reset`, `update`, `create`, `prune` | exclusive | "mutating this bench; everyone out" |
| `logs`, `info`, `shell`, other quick reads | none | fencing them would be noise |

So a `delete` cannot remove a workspace mid-`bake`, two `switch`es cannot race the `[deploy_state]` rollback ledger, and a second mutator is refused naming the first:

```
⛔ Bench mybench is busy: switch (pid 4242) is running on it. Let it finish, then re-run.
```

Two bakes on one bench may overlap (shared holds coexist), and a standalone bake (`--apps`/`--config`) touches no bench and takes no bench lock.

## Observers are never fenced {#observers}

The observation commands (`list`, `info`, `logs`, `services info`, `ssl list`, `apps list`, `domain list`, `tools status`) hold nothing at either tier and keep working even mid-migration, which is exactly when you want to peek at what is happening. For the same reason they never auto-start a stopped global stack: an observer reports the stack as it finds it.

## Limits, stated plainly

- The locks cover **fm-vs-fm on this host**: not other machines, and not hand-run `docker` commands.
- The name written into a lock file is diagnostic; with several *shared* holders it carries the most recent one. The lock itself is always exact.
- `fm shell` and `fm compose` release their hold when they hand off to docker (the fm process is replaced), so an idle shell left open overnight never blocks a migration; the flip side is that the interactive session itself is not protected from one.

**See also:** [Migrations reference](migrations.md), [Architecture reference](architecture.md)
