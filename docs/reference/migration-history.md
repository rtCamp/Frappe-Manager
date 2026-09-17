# Migration History

What each shipped migration actually did, in full. This is the archaeology page: read it when you are upgrading an install that sat out several releases, or hunting for when a particular file or key changed shape. How the migration *system* behaves lives in the [Migrations reference](migrations.md).

## The migrations, version by version {#inventory}

| Version | Global services & configuration | Per bench |
|---|---|---|
| **v0.19.0** | `nginx-proxy` image bumped to `jwilder/nginx-proxy:1.11` | the `[ssl]` table becomes a top-level `[[ssl_certificates]]` array and `preferred_challenge` becomes `challenge_type`, though nothing reads that array where it lands (see the v0.20.0 row and the warning below); nginx `SITENAME` becomes `SITE_MAPPINGS`; `alias_domains`, `upload_limit`, `restart_policy` added; runtime moves from pyenv/nvm to uv/fnm and from certbot to acme.sh; supervisor config regenerated |
| **v0.20.0** | `mariadb` moved off end-of-life `mariadb:10.6` to `mariadb:11.8`, with `MARIADB_AUTO_UPGRADE` letting the image upgrade the system tables; the global `[cloudflare]` table in `fm_config.toml` becomes the credential set labelled `cloudflare` under `[ssl.dns_providers]`, so both scopes now store labelled sets and nothing else | Adminer 4 to 5 with the FM login plugin; `admin_tools_username` / `admin_tools_password` move into the `[auth]` table; bench nginx gains the real-IP overlay and re-renders `default.conf` so it logs JSON; the `[ssl]` table is reshaped into the one form the loader reads: v0.19.0's top-level `ssl_certificates` array and `dns_providers` table move under `[ssl]`, `[ssl].dns_challenge_providers` is renamed `dns_providers`, a credential left on a certificate moves into the set labelled `cloudflare`, and the dead certificate keys (`email`, `status`, `cert_path`, `key_path`, `issued_date`, `last_renewal_attempt`, `toml_exclude`) are deleted; every bench gains a `[sites."<site>"]` entry and `[database."<site>"]` and a top-level `alias_domains` list move under it; each recorded deploy's `backup` path becomes a `backups` map keyed by site; `[switch].migrate = "auto"` becomes `true`; `default_site` is recorded in `common_site_config.json` for a bench that has none |
| **v0.21.0** | the shared services are renamed to their engine names, atomically with everything that carries the old names: compose service `global-db` becomes `mariadb` and `global-nginx-proxy` becomes `nginx-proxy` (containers `fm_mariadb`, `fm_nginx-proxy`), the shared networks become `fm-frontend-network` / `fm-backend-network` with their subnets carried over unchanged, and on macOS the data volume is copied to `fm-mariadb-data` (the old volume is kept as the rollback path and reported for manual removal) | every bench is taken down for the network swap; its compose files are rewritten to the renamed external networks and `db_host: global-db` becomes `mariadb` in every site config (external database endpoints are never touched); benches that were running are started again | NewRelic settings also move into `[telemetry.newrelic]`, from either earlier shape: the `[monitoring]` table of the 0.20/0.21 development line, or v0.19.0's flat `newrelic_enabled` / `newrelic_license_key` keys, which no release ever migrated and which the current loader does not read, so a bench upgrading from v0.19.0 silently stopped reporting until this step; the old keys are deleted rather than left beside the new table, because fm's config writers merge and never strip.

The v0.19.0 runtime rebuild is the slow part of any upgrade that crosses it: it recreates the Python virtualenv with `uv`, reinstalls apps, and rebuilds assets.

!!! warning "v0.19.0 left TLS configuration where nothing reads it"
    v0.19.0 writes `ssl_certificates` and `dns_providers` at the **top level** of `bench_config.toml`, and no release has ever read them there: the loader only looks under `[ssl]`. A bench that stopped at v0.19.0 loads with zero certificates, and the next time fm saves the file those orphaned keys are dropped outright, taking the TLS configuration with them. v0.20.0 relocates both into `[ssl].certificates` and `[ssl].dns_providers`, so migrate before you go hunting for a certificate `fm ssl list` says is not there.

## Version-specific backup artifacts {#version-backups}

Beyond the standard set every migration takes (see [Backup & Restore](../guides/backup-restore.md#before-a-migration)), individual versions add their own:

| Artifact | Version | Why |
|---|---|---|
| `supervisor.conf`, `*.fm.supervisor.conf` | v0.19.0 | it regenerates them |
| nginx `default.conf` | v0.19.0, v0.20.0 | both regenerate it |
| `docker-compose.admin-tools.yml` | v0.20.0 | it rewrites it (Adminer 5) |
| all three bench compose files | v0.21.0 | the network rename rewrites them |
| `global-db-all-databases-<timestamp>.sql.gz` | v0.20.0 | whole-server dump taken while the old engine still runs, because the datadir upgrade is one-way (the file keeps the service's pre-v0.21.0 name, which is what it was called when that migration ran) |
| `fm_config.toml` | v0.20.0 | its DNS-credential relocation backs the file up before rewriting; other versions rewind only the version field in place, without a copy |

**See also:** [Migrations reference](migrations.md), [Configuration reference](configuration.md)
