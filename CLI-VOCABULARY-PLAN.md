# fm CLI vocabulary: confirmations, force, dry-run

STATUS: IMPLEMENTED (all 8 decisions; the rule lives in docs/commands/index.md
"Flag Conventions" and AGENTS.md). This file remains as the design record.

Working design doc. Not user docs, not committed to the release: this is the thing we
refine, and the final rule lands in `docs/commands/index.md` + AGENTS.md when approved.

## The rule (proposed)

1. **`--yes` / `-y`** answers any confirmation prompt.
   Help text is always "…without asking for confirmation."
   Under `--non-interactive`, an unanswered prompt refuses and names `--yes`.
2. **Dangerous behaviors are separate opt-in flags that name the behavior**
   (`--restore-db`, `--delete-backups`). A stray `--yes` copied from another script can
   never enable a dangerous behavior by itself; it only answers the question for
   behaviors already opted into.
3. **`--force` selects a stronger action** (recreate, interrupt, renew-early).
   It never bypasses a prompt, and may itself sit behind one.
4. **`--dry-run` prints the full plan, changes nothing, exits 0, never prompts.**
   It exists for scripts; interactive users get the same report from plan-first
   prompting by answering No.
5. **Destructive commands are plan-first**: print what will be touched (paths, one row
   each), then prompt, default **No**, then execute exactly the plan that was shown.

### Why uniform `--yes`, not per-command `--confirm-<action>`

- Audit: 5 of 7 fm confirmation flags are already `--yes/-y`
  (`delete`, `reset`, `ssl remove`, `switch`, `self upgrade`); only the migrate pair
  says `--auto-proceed`. There is no third dialect.
- Ecosystem: docker (`-f`), gh (`--yes`), apt (`-y`), terraform (`-auto-approve`) all
  converged on ONE uniform bypass per tool. No major CLI ships per-command
  action-named bypass flags.
- Terraform lived the split (`destroy -force` vs `apply -auto-approve`) and killed it
  (PR #17218): every unique flag name is a special case in every wrapper and CI script.
- The "what am I confirming?" concern is answered structurally, not by the flag name:
  specificity belongs in the PROMPT (the printed plan, the paths), uniformity belongs
  in the FLAG (automation channel). The one place a generic `--yes` looked dangerous
  (`fm switch --yes` gating the DB overwrite) dissolves under rule 2: the danger is
  already named by its own opt-in flag, `--restore-db`.

## Audit: current state

### Confirmation bypass flags

| Command | Today | Under the rule |
|---|---|---|
| `fm delete` | `--yes/-y` | unchanged |
| `fm reset` | `--yes/-y` | unchanged |
| `fm ssl remove` | `--yes/-y` | unchanged |
| `fm self upgrade` | `--yes/-y` | unchanged |
| `fm switch` (`--restore-db` confirm) | `--yes/-y` | unchanged (danger named by `--restore-db`) |
| `fm migrate` | `--auto-proceed` | `--yes/-y`; `--auto-proceed` REMOVED (hard cutover, no alias) |
| `fm services migrate` | `--auto-proceed` | same |
| `fm prune` (new) | none (executes immediately) | plan-first + prompt + `--yes/-y` |
| `fm services prune` (new) | none | same |

### `--force` family (all compliant already; no changes)

| Flag | Meaning | Prompt bypass? |
|---|---|---|
| `fm start --force/-f` | recreate containers instead of reuse | no |
| `fm restart --force` | kill fast, skip worker drain | no |
| `fm ssl renew --force` | renew even when not due | no |
| `fm code --force-start/-f` | start the bench first if stopped | no |
| `fm ssl acme.sh … --force` | acme.sh's own flag (passthrough) | foreign |
| internal `force=` params | library semantics (backup restore, db_import, docker rm/rmi, supervisor) | out of scope |

Rot found: `output_manager/base.py` docstrings describe prompt bypass as
"command flags like `--force`" — stale, rewritten to `--yes`.

### `--dry-run`

| Command | Today | Under the rule |
|---|---|---|
| `fm prune` / `fm services prune` | ✓ report-only | unchanged (becomes "report without the prompt") |
| `fm ssl renew --dry-run` | MISNOMER: performs real issuance against LE *staging* CA | renamed `--staging` (certbot's own spelling); `--dry-run` kept as deprecated hidden alias |
| `fm migrate` / `fm services migrate` | none | **add**: print the existing plan preamble (benches, version arrows, migration list), exit 0 before locks/backups/prompt. CI probe for "is migration pending?" |
| `fm delete` | none | **add**: list what deletion touches (bench dir, per-site schemas on shared mariadb, TLS material, proxy entries, recorded dumps kept-vs-`--delete-backups`). Same listing becomes the body of its confirmation prompt. |
| `fm switch` | none | **deferred**: a truthful dry-run needs the pipeline's mid-flight decisions (migrate? backup? rolling?); a guessing one is worse than none |
| `fm bake` | none | no: expensive but not destructive; inputs already printed at start |
| `fm update`, `ssl add/remove`, `services real-ip`, `reset`, `stop` | none | no: the plan is the flags you typed / one obvious sentence |

## Implementation plan

- **Phase 1 — write the rule down**: conventions blurb in `docs/commands/index.md` +
  AGENTS.md; fix `base.py` docstring wording.
- **Phase 2 — migrate family joins `--yes`**: option `("--yes", "-y")`; `--auto-proceed`
  removed outright (an unknown-option error names the flag, so a broken script fails
  loudly at its first run, not subtly); executor prompt `required_flag` → `--yes`;
  e2e scripts swap.
- **Phase 3 — prune goes plan-first**: report (with paths) → prompt `[y/N]` → execute
  the same plans; `--yes` bypasses; `-n` without `--yes` refuses naming `--yes`;
  nothing-to-do → no prompt, exit 0; `--dry-run` unchanged; exclusive bench lock stays.
  Releases category plans via `prune_releases(dry_run=True)`, executes on yes.
- **Phase 3½ — new `--dry-run`s**: migrate family (plan + exit 0); `fm delete`
  (deletion plan listing, shared with its prompt body).
- **Phase 4 — tests**: prompt matrix for both prunes (default prompts / No exits 0
  untouched / Yes executes / `--yes` skips / `-n` refusal names `--yes` / `--dry-run`
  never prompts); migrate accepts both spellings; delete dry-run listing; ssl renew
  `--staging` + alias.
- **Phase 5 — docs & regen**: command pages, prose mentions of `--auto-proceed` →
  `--yes`, `ssl renew --staging` docs, vocabulary blurb.

## Decisions made

1. **Prompt default (RESOLVED)**: **No, everywhere** -- prune AND migrate. A bare Enter
   never executes anything; the operator types `y` (or, for the whole run, passes
   `--yes`). Every prompt must VISIBLY show its default
   (`[y/N]` for text prompts; the highlighted entry in choice menus). Rationale: one
   rule with zero judgment calls; the cost on migrate (typing one `y` on a routine run)
   buys uniformity, and automation was never going to type anything anyway.
2. **`fm delete` ceremony (RESOLVED)**: adopt the typed-name confirmation, for
   `fm delete` ONLY. Interactive: print the deletion plan (dir with size, schemas on
   the shared mariadb, TLS/proxy entries, recorded dumps kept-vs-`--delete-backups`),
   then `Type the bench name to confirm deletion [default: abort]` -- anything but the
   exact bench name (including bare Enter) aborts. `--yes` bypasses for scripts, as
   today. Not extended to reset/prune/ssl remove: delete is the one command that
   destroys user data with no undo, and the typed name catches the
   wrong-bench/wrong-terminal accident that y/N cannot.
3. **`--auto-proceed` (RESOLVED)**: removed outright, no alias, no deprecation window.
   `--yes`/`-y` is the only spelling. Consistent with fm's hard-cutover policy: one
   vocabulary, no shims; a script still saying `--auto-proceed` fails loudly with
   typer's unknown-option error at first run. Changelog carries the migration note.

4. **`--yes` boundary (RESOLVED, derived)**: prompts come in three kinds, each with ONE
   bypass mechanism -- no per-prompt flags needed:
   - K1 permission ("proceed with what you typed?") -> `--yes`, everywhere.
   - K2 decision ("also/instead do this other thing?") -> a NAMED flag; `--yes` never
     answers these. Every K2 in fm already has its flag (see appendix).
   - K3 missing input (which site? what password?) -> the argument/option carrying it.
   The law: `--yes` may only answer a prompt whose "yes" adds ZERO information beyond
   the typed command line. Any scope-changing question is forbidden as a `--yes` target
   and must be a named flag. Notably: `--yes` on migrate never answers the
   "continue without a DB backup?" prompt -- that stays `--skip-db-backup`'s.

5. **Prune prompts once per run (RESOLVED)**: the full report (all categories, paths)
   then ONE `Proceed?` covering everything shown; `--only` is the granularity
   mechanism, not extra prompts.
6. **`switch --keep N` inline prune (RESOLVED)**: prompt-free. It runs inside a deploy
   the operator launched, and `--keep N` is a named opt-in -- the flag is the consent.
7. **`--dry-run` on plan-first commands (RESOLVED)**: every plan-first command
   (prune, migrate, delete) carries `--dry-run` = print the plan, exit 0, never
   prompt. Interactively it equals answering No; it exists because the non-interactive
   path is otherwise a REFUSAL by design (`-n` without `--yes`), leaving scripts no way
   to see the plan (cron disk reports, runbook captures, CI retention checks).
8. **`fm ssl renew --dry-run` (RESOLVED)**: renamed `--test-ca`, HARD -- no alias, same
   policy as `--auto-proceed`. The flag selects Let's Encrypt's TEST CA (their staging
   endpoint) and runs the REAL renewal -- challenges, credentials, issuance -- while
   the live certificate and system stay untouched (a consequence: a test-CA cert is
   untrusted, installing it would be wrong). A behavior selector, not a dry run; under
   rule 4 the old name is a lie. `--test-ca` names the mechanism in fm's usual
   noun-object flag grammar and needs no Let's Encrypt jargon to understand
   (`--staging` and `--rehearse` considered and rejected). No report-only dry-run is
   added to renew: that report already exists as `fm ssl list`. Same rename in the
   `fm ssl acme.sh` example text and docs.

All questions resolved; the plan above is final and ready to implement.


## Appendix: complete prompt inventory (normative)

Every `prompt_ask` in fm, classified. New commands add rows here.

| Prompt | Kind | Bypassed / answered by |
|---|---|---|
| migrate "Do you want to proceed?" | K1 | `--yes` (plan change) |
| prune "Proceed?" (new) | K1 | `--yes` (plan change) |
| reset "Do you want to reset SITE?" | K1 | `--yes` |
| ssl remove "Remove certificate for DOMAIN?" (bench + external) | K1 | `--yes` |
| self upgrade "upgrade?" | K1 | `--yes` |
| delete "Type the bench name to confirm" (ceremony EXISTS, delete.py:69) | K1 | `--yes` |
| delete site from bench "remove SITE from BENCH?" | K1 | `--yes` |
| switch "Type the schema name to confirm this overwrite" (ceremony EXISTS) | K1 | `--yes`; the behavior was opted in by `--restore-db` |
| migration failure "rollback / halt?" / "roll back?" / "archive or revert?" | K2 | `--on-failure rollback\|halt\|archive` |
| migration "DB name undeterminable -- continue without dump?" | K2 | `--skip-db-backup` / `--skip-backup` |
| delete "also remove the database from mariadb?" | K2 | `--delete-db-from-mariadb` |
| delete recorded dump files (no prompt: keeps + prints paths) | K2 | `--delete-backups` |
| ngrok "save the auth token to config?" | K2 | `--save-token` / `--no-save-token` |
| create-failure cleanup "drop the schema+login this run created?" | K2 (failure path) | stays a prompt; non-interactive takes the safe default (No) |
| gate "Update now / Update later?" | special | the explicit command (`fm migrate` / `fm services migrate`) is the bypass; `-n` refuses naming it |
| "enter admin password" | K3 | `--admin-pass` |
| bench picker (which bench?) | K3 | the positional BENCH argument |

Checklist for any future prompt:
1. "yes" does exactly what the command line says -> K1, `--yes` covers it, no new flag.
2. "yes" would change WHAT happens -> forbidden as a `--yes` target; make it a named flag.
3. It asks for a VALUE -> an option/argument.

Note: Q2's typed-name ceremonies already exist in the code (delete bench name,
switch schema name). Q2's remaining work is only the deletion-plan printout shown
BEFORE delete's ceremony.
