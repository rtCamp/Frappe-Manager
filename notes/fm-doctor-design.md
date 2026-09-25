# `fm doctor` — design note

Status: design only, nothing implemented.
Grounded against the tree as of 2026-09-25; every line reference below was read, not recalled.
Deliberately **not** under `docs/` — `just docs-lint` builds the flag set from the live Typer app
and would fail on a page naming flags (`--deep`, `--strict`) that no command declares yet.

---

## 1. Premise

fm already performs ~50 health checks. Every one of them is (a) buried inside a command path,
(b) fatal at the point of use, and (c) invisible until you trigger the operation it guards.
`db_probe.py` alone has 16 named checks with `ok`/`warn`/`fail` statuses
(`site_manager/modules/db_probe.py:115`) — a complete diagnostic engine that only ever runs
during `fm create --db-host`.

`fm doctor` is therefore **not new logic**. It is an inversion of control: run the checks that
already exist, *before* you need them, *without* dying on the first one, across *every* scope
at once.

That framing dictates the one non-negotiable rule:

> **Doctor calls the same predicate the command path calls. It never forks one.**
> Where a check exists only as inline code inside a command body, doctor's cost is *extracting*
> it into a callable — not reimplementing it. A doctor carrying its own copy of
> `nginx_conf_serves_per_site` is worse than no doctor: it will drift, and then it will lie
> confidently.

## 2. Bootstrap: what `app_callback` does to doctor

`app_callback` (`commands/__init__.py:274`) is the gate every command passes. Re-read in full;
the gates and their exemptions as they stand today:

| Gate | Line | Exempt when |
|---|---|---|
| CLI_DIR exists / is a directory | `:363-369` | never (mkdir is unconditional) |
| `fm_config.toml` parse failure re-raises | `:374-382` | `tolerates_broken_host` → warn + defaults |
| theme/style config | `:389-393` | always advisory |
| docker daemon down → `output.exit` | `:409-410` | `tolerates_broken_host` |
| first-install image prefetch + `rmtree` on failure | `:424-439` | `tolerates_broken_host` **or** `OBSERVE_ONLY_COMMANDS` (`:426-427`), or `invoked_command in STOCK_IMAGE_PREFETCH_SKIP_COMMANDS` (`:429`) |
| services-tier + bench migration gates | `:481-557` | `tolerates_broken_host` (`:483`) or whitelist (`:484-485`) |
| shared host lock → `output.exit` if a migration holds it | `:568-578` | `MIGRATION_COMMANDS` or `OBSERVE_ONLY_COMMANDS` |
| `entrypoint_checks` → `ServicesNotCreated` → `remove_itself()` + exit | `:606-611` | `invoked_command == "bake"` or `tolerates_broken_host` |

### Registration: exactly two changes

1. `cls=BrokenHostCommand` (`commands/gating.py:34`).
   Covers the docker gate (`:409`), the config-parse fatality (`:379`), both migration gates
   (`:483`), the first-install prefetch (`:426`), and `entrypoint_checks` (`:606`).
2. Add `"doctor"` to `OBSERVE_ONLY_COMMANDS` (`frappe_manager/__init__.py:84`).
   Covers the host lock (`:568`) and stack auto-start.

Not needed, contrary to a first reading:

- `STOCK_IMAGE_PREFETCH_SKIP_COMMANDS` — `first_install` already goes False via
  `tolerates_broken_host` at `:426`, and that set is matched on `invoked_command`, not the
  full path.
- `MIGRATION_CHECK_WHITELIST_COMMANDS` — `should_check_migration` already short-circuits on
  `tolerates_broken_host` at `:483`. Adding it would be a second, redundant switch for the
  same behaviour; skip it.
- A change in `services_manager/services.py` — `entrypoint_checks` is not reached at all for a
  broken-host command (`:606`).

### Residual risk

`services_manager.init()` at `:590` runs **unconditionally**, before the `tolerates_broken_host`
guard on `:606`. Checked: it does **not** raise `ServicesException` — that path is in `create()`
(`services_manager/services.py:230-233`), not `init()` (`:147-181`). Two real consequences remain:

- **`init()` can WRITE.** It ends in `set_frappe_headers_conf()` (`services.py:183-202`), which
  rewrites `nginx-proxy`'s `fm_headers.conf` whenever fm's version changed. It returns early if
  the proxy confd dir is absent (`:192-193`) and compares content before writing (`:199-200`), so
  it is a no-op in steady state — but "doctor mutates nothing" is only true because of that
  comparison, not because doctor is exempt. `fm list` and `fm info` already share this;
  accepting it keeps doctor consistent rather than carving a new exemption.
- **A corrupt services compose file** is parsed eagerly at `:154`/`:163-167`
  (`get_services_list`), so a `ComposeFileException` there would kill doctor before any finding
  prints. That, not the docker group, is the case to wrap.

### Corollary

Doctor must run **while a migration is in progress** and report it
(`migration_manager/migration_helpers.py:65 MigrationRunningStatus.running`), because that is
precisely when a user asks "is my install broken?". Holding no lock (change 2) is what permits it.

## 3. Shape

```
fm doctor [BENCH(/SITE)] [--deep] [--strict] [--json]
```

- **Positional**: reuse `BenchSiteArgument` (`commands/arguments.py:147`). Omitted → whole host.
  `fm doctor fm.localhost` → host + services + that bench. Never invent an address grammar.
- **Panel**: `_PANEL_GLOBAL` (`commands/__init__.py:247`), beside `list`.
- **`--deep`**: behavior selector. Default runs static + cheap runtime reads only (file parse,
  x509, `docker ps`, `compose ps`) and must finish in ~1s. `--deep` adds in-container execs:
  `supervisorctl status` (`bench_supervisor.py:113`), `curl /api/method/ping`
  (`deploy_orchestrator.py:469`), `db_probe.probe_stage_two` (`db_probe.py:1041`), `wait-for-it`
  through bench nginx (`bench_admin_tools.py:248`). Those need running containers and cost
  seconds per site.
- **`--strict`**: warnings also exit 1. Default: only failures do.
- **`--json`**: follows `commands/list.py:62-70` exactly — honor global `ctx.obj["json"]` first
  (one `print_data` event), else `output.stop()` + pretty dump on clean stdout.

Flags doctor must **not** have, per the flag-vocabulary law in `docs/commands/index.md`:

- **No `--dry-run`.** Doctor changes nothing; a dry run of a read is meaningless, and its
  presence would imply a hidden apply path.
- **No `--yes`.** Doctor never prompts.
- **No private `--verbose`.** The global `-v` already exists (`ctx.obj["verbose"]`, `:329`) and
  is the natural switch between "findings only" and "every check, including passes".

## 4. The `--fix` question

**Recommendation: no `--fix` in v1.** In order of weight:

1. Every real remedy is already a command: `fm bake && fm switch`, `fm migrate`, `fm ssl renew`,
   `fm services restart`, `fm self update-images`. A `--fix` that shells those out is a worse UI
   than printing them, because the user loses the plan/confirm step each already implements.
2. The two genuinely idempotent heals — re-materializing fm-managed nginx confs
   (`site_manager/site.py:2060 ensure_fm_nginx_confs`) and stripping orphaned `# fm:maintenance`
   blocks (`commands/maintenance.py:24-34`) — **already run on `fm start`**. Doctor's correct
   output is "run `fm start`", not a second heal path.
3. A read-only command is one you run without thinking. The moment doctor can write, it inherits
   the host lock, the migration gate and `--yes`, and it stops being the thing you run when
   everything is on fire.

Printing the exact remedy per finding gets ~95% of the value at 0% of the risk. If `--fix` ever
lands it is a named flag (never `--yes`), with the plan-first + single-confirmation shape
`prune`/`delete` use.

## 5. Check model

```python
@dataclass(frozen=True)
class Check:
    id: str                      # "nginx.per_site_includes" -- STABLE, scriptable
    scope: Scope                 # HOST | SERVICES | BENCH | SITE
    requires: tuple[str, ...]    # ("docker.daemon",) -- unmet => SKIPPED, never OK
    deep: bool = False

@dataclass(frozen=True)
class Finding:
    check: str
    severity: Severity           # FAIL | WARN | INFO | OK | SKIPPED
    subject: str                 # "fm.localhost" / "shop.example.com"
    message: str                 # what is wrong, one line
    evidence: str | None         # the file:key / value that proves it
    remedy: str | None           # a real fm command or a real path
```

Three properties that make or break it:

- **`SKIPPED` is a first-class severity.** A check whose prerequisite failed says so. Reporting
  `ok` for "certificates valid" when the SSL dir was unreadable is the single worst bug a doctor
  can have.
- **Stable `id`s.** They are the JSON contract, the grep target and the future suppression key.
  Suppression config (`[doctor] ignore = [...]`) is a deliberate **non-goal** for v1; stable IDs
  merely keep the door open.
- **Severity must not cry wolf.** A *stopped* bench is `INFO`, not `WARN` — the user chose that.
  `WARN` = "this will bite you"; `FAIL` = "this is already broken". A doctor that yellow-flags
  normal states is ignored within a week, and is then dead weight.

## 6. Check catalogue

Each row is wired to an existing predicate (path cited) or flagged **NEW** (a real gap).

### host

| id | detection | source |
|---|---|---|
| `docker.daemon` | `"Server"` key in `docker version --format json` | `docker/docker_client.py:66` |
| `docker.group` | Linux user in `docker` group | `utils/docker.py:144` |
| `docker.compose` | compose subcommand resolves | **NEW** — surfaces only as `DockerException` |
| `fm.cli_dir` | CLI_DIR exists and is a directory | `commands/__init__.py:363-369` |
| `fm.config_parses` | `fm_config.toml` loads | `commands/__init__.py:374-382` |
| `fm.config_unknown_keys` | `collect_unknown_keys` dotted paths | `utils/config_keys.py:48`, `metadata_manager.py:467` |
| `fm.ledger_services` | `[schema].version` vs current; `0.0.0` = damaged | `metadata_manager.py:217`, `migration_validator.py:134` |
| `fm.migration_running` | lock held / holder name | `utils/process_lock.py` via `commands/__init__.py:575`, `migration_helpers.py:65` |
| `images.present` | required images vs `docker images` | `bench_docker.py:748` |
| `ports.80_443` | who owns :80/:443 — proxy vs foreign process | **NEW** — documented symptom, no check (`docs/faq.md:107`) |
| `disk.free` | free space under `~/frappe` | **NEW** — `prune` exists, nothing warns first |
| `clock.skew` | host clock vs NTP | **NEW** — named as a cert-failure cause (`docs/guides/ssl.md:321`) |
| `ca.trusted` | probe each store directly, never the `.installed` sentinel | `trust_store_manager.py:289`, `commands/ssl/ca/status.py:18` |

### services

| id | detection | source |
|---|---|---|
| `services.created` | services dir + compose file exist | `services_manager/services.py:78,:92` |
| `services.running` | all `State == "running"` | `services.py:128-132` |
| `services.legacy_names` | pre-1.0 `global-db` naming | `services.py:110` |
| `db.ping` | `mariadb-admin ping` exec | `database_service_manager.py:277` |
| `proxy.network` | frontend network / subnet resolves | `utils/network.py:104` |

### per bench

| id | detection | source |
|---|---|---|
| `bench.config_parses` / `bench.unknown_keys` | model + hand-read stray union | `bench_config.py:2222` |
| `bench.ledger` | `[schema].version`; `0.0.0` = damaged or half-created | `bench_migration_state.py:50-57` |
| `nginx.per_site_includes` | `custom/<site>/*.conf` not included → **site auth written but never read; fm reports protected, nginx serves open** | `site_manager/site.py:2056` |
| `nginx.auth_basic_duplicate` | bench-wide *and* per-site conf → nginx refuses to load at all | `site.py:2119-2125` |
| `nginx.upstream_auth_stale` | conf missing `$fm_upstream_auth` | `tests/unit/commands/test_auth_migrate_shell_maintenance_contract.py:463` |
| `nginx.orphan_blocks` | `# fm:maintenance` / `# fm:https-redirect` / `# fm:hsts` present with no config backing | `maintenance.py:24-34`, `vhost_config_manager.py:182`, `hsts_manager.py:44` |
| `nginx.confs_missing` | fm-managed confs absent (heal-on-start would fix) | `site.py:2060` |
| `workers.custom_valid` | reserved queue names, regex, bounds | `bench_supervisor.py:44-92` |
| `config.split` | redis in `common_site_config.json`; db keys *not* in common; `db_ssl_ca` not leaked into common | `bench_database.py:107-123`, `bench_site.py:409-413` |
| `redis.identity_collision` | two benches resolving to one live redis | `bench_site.py:228`, `compose_shape.py:371` |
| `domain.conflict` | same domain served by two benches | `site_manager/domain_conflict.py:92` |
| `sites.disk_vs_config` | `missing` vs `unmanaged` | `site.py:1242-1246`, `docs/faq.md` |
| `sites.default_site_drift` | `bench use` moved `default_site` bench-wide | `bench_site.py:380-383` |
| `deploy.current_image_present` | `docker image inspect` of `[deploy_state].current_image` | **NEW** — nothing verifies this; prune protects the tag but never checks it exists |
| `deploy.previous_collapsed` | `previous == current` → `fm switch --previous` is a redeploy | `deploy_orchestrator.py:1286-1293` |
| `ssl.expiry` | x509 of live `fullchain.cer`; `< SSL_RENEW_BEFORE_DAYS` (30) | `utils/helpers.py:368`, `frappe_manager/__init__.py:24` |
| `ssl.staging_contamination` | staging issuer on a cert in a serving path | `acmesh_certificate_service.py:271`, `ssl_certificate_manager.py:37-58` |
| `ssl.ecc_orphan` | leftover `<domain>_ecc` dir after removal | `acmesh_certificate_service.py:481-487` |
| `ssl.behind_proxy_agreement` | certs on one bench disagreeing → `ERR_TOO_MANY_REDIRECTS` | `ssl_manager/certificate.py:75-86` |

### `--deep` only

`supervisord.running` (`bench_supervisor.py:113`) · `web.responds` 200/404
(`bench_orchestrator.py:477`) · `replicas.ping` (`deploy_orchestrator.py:469`) ·
`admin_tools.reachable` through bench nginx (`bench_admin_tools.py:248`) · `db.external_probe`
and `db.app_parity` (`db_probe.py:1041,:957`).

## 7. Output

Rail-card grammar unchanged: `railcard.Card` (headline + facts, `output_manager/railcard.py:21`),
state carried by **text** first so the `mono` theme stays readable
(`output_manager/theme.py:18-21`), `label_width = 9` (`output_manager/style.py:24`).

Something wrong — the common case:

```
$ fm doctor

┃ host                                 ok · 13 checks

┃ services                             warn · 5 checks, 1 warning
  warn      mariadb 10.6.16 · 3 schemas have no recent backup
            fm services info

┃ fm.localhost                         fail · 21 checks, 1 failure, 3 warnings
  fail      nginx conf predates per-site includes
            site auth is written to custom/shop.example.com/ but nginx reads only
            custom/*.conf, so shop.example.com is served UNPROTECTED while fm reports
            auth enabled
            ~/frappe/sites/fm.localhost/configs/nginx/conf/conf.d/fm.localhost.conf
            fm bake fm.localhost && fm switch fm.localhost
  warn      certificate for shop.example.com expires in 9 days
            fm ssl renew shop.example.com
  warn      bench_config.toml has an unrecognised key
            switch.hooks.host.before_restrat
  warn      deploy_state.current_image is not present locally
            ghcr.io/acme/shop:2026-09-19-a91c
            fm bake fm.localhost
  info      bench is stopped

3 scopes · 39 checks · 1 failure · 3 warnings
```

Healthy — must be short enough that running it is free:

```
$ fm doctor

┃ host                                 ok · 13 checks
┃ services                             ok · 5 checks
┃ fm.localhost                         ok · 21 checks

3 scopes · 39 checks · no findings
```

Host on fire — the case doctor exists for. Note `skipped`, never a silent pass:

```
$ fm doctor

┃ host                                 fail · 13 checks, 1 failure, 9 skipped
  fail      docker daemon is not reachable
            docker version reports a client but no server
            start Docker Desktop, then rerun fm doctor
  warn      services tier is at 0.9.4, fm is 1.2.0
            fm services migrate
  skipped   9 checks need the docker daemon

1 failure · 1 warning · 9 skipped
```

`-v` shows every check including `ok` rows — same card, no new rendering path.

`--json` is the support artifact: `fm doctor --json > bug-report.json` replaces "paste your
`fm info`", with no new command.

```json
{
  "fm_version": "1.2.0",
  "generated_at": "2026-09-25T11:04:12Z",
  "summary": {"checks": 39, "fail": 1, "warn": 3, "info": 1, "skipped": 0},
  "findings": [
    {
      "check": "nginx.per_site_includes",
      "scope": "bench",
      "subject": "fm.localhost",
      "severity": "fail",
      "message": "nginx conf predates per-site includes; shop.example.com is served unprotected",
      "evidence": "~/frappe/sites/fm.localhost/configs/nginx/conf/conf.d/fm.localhost.conf",
      "remedy": "fm bake fm.localhost && fm switch fm.localhost"
    }
  ]
}
```

Exit codes stay inside the repo's existing 0/1 vocabulary: `0` = no failures (warnings allowed),
`1` = at least one failure, or at least one warning under `--strict`. No third code — nothing
under `commands/` uses one.

## 7a. The "predates" detectors

A distinct family inside the catalogue, worth naming because it is the largest ready-made block:
places where fm detects that an on-disk artifact was written by an OLDER fm and branches. All of
these are already callable predicates — wiring, not extraction.

### Runtime artifacts

| old shape | signal | branch | path:line |
|---|---|---|---|
| nginx conf from the pre-per-site template | regex `include\s+/etc/nginx/custom/[^*\s;]+/\*\.conf\s*;` over `conf.d/default.conf`; an absent file counts as old | probe | `site_manager/site.py:2028` (regex `:2058`) |
| ↳ per-site `fm auth --site` | same | refuse, exit 1 | `commands/auth.py:288-291` |
| ↳ per-site tool routing | same | refuse | `commands/tools/_helpers.py:35-38` |
| ↳ auth conf render | same | fallback to bench-wide + warn when per-site auth is recorded but unenforceable | `site.py:2126-2138` |
| ↳ admin-tools location conf | same | fallback to shared conf, per-site files unlinked | `bench_admin_tools.py:138-144` |
| conf predating the Authorization-header fix (frappe 401s every authed request) | `"$fm_upstream_auth" not in default_conf` | refuse | `commands/auth.py:386-396` |
| bench seeded while the guard was directory-existence: overlays but no base config, nginx dead | absence of the marker FILE `configs/nginx/conf/nginx.conf` | heal at create; heal on every start | `bench_docker.py:386-387`; `site.py:618-648` |
| conf predating a domain now in `[sites]` — unknown Host silently served the primary site's data | each `[sites]` domain regex-searched in the rendered conf | heal: unlink + force-recreate nginx | `site.py:1330-1382` |
| pre-per-site bench-wide `custom/auth.conf` left behind | exists, not wanted, AND starts with `# fm:auth` (unmarked ⇒ hand-written, kept) | heal: unlink + one reload | `site.py:2222-2235` |
| bench predating `upload-limit.conf` (nginx 1M vs advertised 50M → 413s) | content diff | heal on start | `site.py:2107-2112` |
| pre-v1.0.0 services compose | `"global-db" in services and "mariadb" not in services` | refuse except migration whitelist + `self` | `services_manager/services.py:107-113` |
| ↳ same, proxy service name | `"nginx-proxy" not in list and "global-nginx-proxy" in list` | fallback, so `fm migrate` can construct its managers at all | `services.py:164-167` |
| vhost.d written before `# fm:https-redirect` markers: bare redirect as the whole body | `_LEGACY_RE` applied after the marked-block strip | heal | `ssl_manager/vhost_config_manager.py:87` |
| acme.sh cert under `<domain>_ecc` while `<domain>` is absent | directory existence | fallback | `acmesh_certificate_service.py:329-332,:426-429` |
| frappe image predating fmx (no `FMX_PYTHON`) — RQ drain impossible | exec fails with any status other than the drain's own exit 3 | `DrainUnavailable` → warn + continue undrained | `deploy_orchestrator.py:944-958`, `worker_drain.py:55-58` |

No compose schema/template version field exists anywhere; `docker/compose_file.py:92-98` is
missing-file bootstrap only.

### Config / ledger

All read-only tolerance: loaders accept old spellings, writers only ever emit the new shape.

| old shape | signal | branch | path:line |
|---|---|---|---|
| `[migration_state]` table (global) | `"schema" if "schema" in data else "migration_state"` | fallback | `metadata_manager.py:412` |
| legacy ledger keys, precedence newest→oldest: `migrated_to` > `system_migrated_to` > top-level `version` | — | fallback; the ONE place legacy spellings are understood | `metadata_manager.py:448-453` |
| no ledger at all | absent `[schema].version` | `0.0.0` | `metadata_manager.py:233` |
| pre-1.0.0 top-level `[cloudflare]` | dict present AND no newer `ssl.dns_providers.cloudflare` | heal **at load**, not at migration — any write would otherwise silently drop the credential | `metadata_manager.py:394-405` |
| legacy keys on a not-yet-migrated file | recognised sets include them by hand | suppress the unknown-key warning | `metadata_manager.py:163,:180` |
| stale spellings surviving a stamp | `pop("migrated_to")` / `pop("system_migrated_to")` | heal on write | `metadata_manager.py:249-250` |
| `[schema].migrated_to` (bench) | `AliasChoices("version", "migrated_to")` — the only AliasChoices in the config models | fallback | `bench_config.py:847`, helper `:1263` |
| `[migration_state]` (bench) | `data.get("schema") or data.get("migration_state")` | fallback | `bench_config.py:1300,:2118`; raw-tomlkit twin `bench_migration_state.py:34` |
| file written by an older OR newer fm | strict `PackagingVersion(recorded) == current` | suppress unknown-key warnings off-equality | `bench_config.py:1266-1311` |
| tag-era `[deploy_state].current_tag` / `previous_tag` | `_DEPLOY_STATE_STALE_KEYS & keys()` | warn (unconditional) + ignore; rollback history unreadable | `bench_config.py:1196,:2233-2241` |
| `environment_type` → `environment`, `apps_list` → `apps` | `data.get(new, data.get(old))` | fallback read | `bench_config.py:2158-2166` |
| retired `[[ssl.certificates]]` keys (`api_token`, `preferred_challenge`, `cert_path`, `issued_date`, …) | `RETIRED_CERTIFICATE_KEYS` + `_drop_retired_keys` `mode="before"` | dropped pre-validation, never warned — retired is not a typo | `ssl_manager/certificate.py:30-49,:88-92` |

Plus the version gates: services ledger behind current (`migration_executor.py:128`),
`effective_prev = min(services, oldest bench)` (`:138-139`), and `0.0.0`-or-below-minimum refused
**before** discovery (`:158`), because discovery from `0.0.0` selects every migration ever
shipped. The validator names each culprit file (`migration_validator.py:115,:125`).

### Consequences for doctor

- **`nginx.per_site_includes` is one probe with four different branches**, not one check. Doctor
  must report the consequence *at each callsite*: the auth-render branch (`site.py:2126-2138`) is
  silent and unsafe — the site serves unprotected while `fm auth --status` reports it protected —
  while the command branches merely refuse. One `FAIL` for the first, `INFO` at most for the rest.
- **Four of these self-heal on `fm start`** (`site.py:618-648`, `:1330-1382`, `:2222-2235`,
  `:2107-2112`). Doctor's finding there is `INFO` + "run `fm start`", never `WARN`. Flagging a
  state that fixes itself on the next start is precisely the cry-wolf failure of §5.
- **Copy the equality gate at `bench_config.py:1266-1311`.** Unknown-key warnings are suppressed
  when a file is behind *or ahead* of current fm. Doctor reporting stray keys on an unmigrated
  bench would re-introduce exactly the noise that gate exists to kill.
- `migrate_1_0_0.py` contains ~18 further detect-then-migrate steps (schema-table renames `:649`,
  `:1144`; admin-tools creds `:567`; `[database]` → `[sites]` `:706`; deploy tag keys `:849`;
  ssl relocation `:963`; mariadb engine `:1189`; `db_host: "global-db"` → `mariadb` `:1530`;
  NewRelic shapes `:1562`; nginx `depends_on` backfill `:433`). Those are the *remedy*, not doctor
  material — but they define the old shapes the detectors above look for.

## 8. Boundaries

- **`fm info` is not `fm doctor`.** `info` states facts about one bench; `doctor` renders
  judgments about correctness across the host. Never merge them; never add findings to `info`.
- **Not a status command.** "Is my bench up?" is `fm list`. Doctor answers "is my install
  *coherent*?" — disk vs runtime vs config disagreement.
- **Not a fixer.** Prints the command, runs nothing.
- **Not a benchmark or monitor.** No latency numbers, no trends, no history.
- **No new config surface** in v1.

## 9. Testing

The suite already constructs the exact broken states doctor must detect —
`tests/unit/commands/test_migration_gate_cli_contract.py:214` (stale bench),
`tests/unit/commands/test_maintenance_vhost_merge.py:199` (orphaned marker block),
`tests/unit/commands/test_auth_migrate_shell_maintenance_contract.py:463` (stale nginx conf),
`tests/unit/site_manager/test_nginx_conf_heal_on_start.py`,
`tests/unit/site_manager/test_redis_identity_collision.py`,
`tests/unit/site_manager/test_fm_config_unrecognised_keys.py`. Each is a ready-made fixture:
build the broken state, assert the `Finding.check` id and severity.

Two tests that must exist and are not padding:

1. **An unmet prerequisite yields `SKIPPED`, never `OK`** — the failure mode that makes a doctor
   actively harmful.
2. **Doctor runs to completion with docker down, a stale ledger and an unparseable
   `bench_config.toml` simultaneously** — the §2 gating contract, which a future callback
   refactor would otherwise break silently.

## 10. Open calls

1. **`--deep` split** — recommended; the default must stay ~1s, and the db probe alone costs
   seconds per site. The alternative is one always-thorough command people stop running.
2. **`ports.80_443`, `disk.free`, `clock.skew`, `docker.compose`, `deploy.current_image_present`**
   are genuinely new logic (~5 checks). Everything else is wiring. In or out of v1?
3. **Extraction budget** — several checks live inline in command bodies, not as callable
   predicates. Doctor's honest cost is that refactor. Doing it is right; doing it *by copy* is
   what kills the command in six months.
4. **Wrapping `services_manager.init()`** (`commands/__init__.py:590`) — resolved to a narrower
   question by §2: not the docker group, but whether a corrupt services compose file should be a
   doctor *finding* instead of an uncaught `ComposeFileException`. Wrapping it is ~3 lines and
   turns the worst broken-host case into a report.
