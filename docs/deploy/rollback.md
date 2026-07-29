# Rollback

The deploy went bad. Here is the way back:

```bash
fm switch mybench --previous                 # code rollback; migrate disabled automatically
fm switch mybench --previous --restore-db    # code AND database back together
```

Need to go further back than one release?

```bash
fm switch mybench local/mybench:<older-tag> --no-migrate   # further than one release
```

Rollbacks run the same [switch pipeline](index.md#the-switch-pipeline) as forward deploys, pointed backwards: the same snapshots, health gate, and abort safety apply.

## What the flags do

- `--previous` disables migrate for the run (old code must never migrate a newer schema); override with an explicit `--migrate`.
- `--restore-db` finds the DB dump recorded for the **current** (bad) deploy in the history and imports it before the swap; a restore is schema-grade, so it runs under the maintenance window like a migrate. Rows written after the bad deploy went live are discarded; that is why it is never implicit.
- After a rollback, `previous_tag` points at the tag you just left; running `fm switch --previous` again re-deploys it (deliberate: rollback of a rollback is a redo).

## What to check after

```bash
fm info mybench
```

- The **deploys** section should show the release you rolled back to as current, with the history intact.
- Hit the site and confirm it serves normally.
