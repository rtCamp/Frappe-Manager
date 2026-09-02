"""
Migration for v0.20.0.

Admin tools: Adminer 4 → 5 with the FM login plugin.

- adminer image: adminer:4 → adminer:5 (upstream revived; ships the pure-PHP
  redis driver used by the plugin)
- drops the ADMINER_DEFAULT_SERVER env — login targets are now discovered at
  request time by the plugin from the mounted sites directory
- adds read-only bind mounts: sites dir (live credentials) and configs/adminer
  (plugin dir mounted over the container's plugins-enabled)
- places configs/adminer/000-fm-login.php — one-click login cards for each
  site database and the bench redis instances, plus the stock manual form

Real client IPs + JSON access logs (bench nginx):

- places configs/nginx/conf/custom/real-ip.conf so bench nginx restores the
  visitor's address from X-Real-IP for traffic arriving from the fm frontend
  network, instead of logging and rate limiting everything as the proxy's IP
- deletes the generated configs/nginx/conf/conf.d/default.conf so the nginx
  entrypoint re-renders it from the new image template, which logs JSON in the
  same format as the global proxy

HTTP basic auth (bench nginx):

- moves the old top-level admin_tools_username / admin_tools_password keys in
  bench_config.toml into the new [auth] table (web = false, tools = true), the
  single credential pair that now drives both auth surfaces
- drops the renamed configs/nginx/conf/http_auth/<bench>-admin-tools.htpasswd;
  the new <bench>.htpasswd is written on the next start

Global database engine:

- moves global-db from mariadb:10.6, which reached end of life on 2026-07-06, to
  the tag frappe's own CI tests against, and lets the image entrypoint upgrade the
  system tables via MARIADB_AUTO_UPGRADE

SSL configuration:

- relocates the top-level ``ssl_certificates`` array and ``dns_providers`` table that 0.19
  wrote into ``[ssl].certificates`` and ``[ssl].dns_providers``, where the loader looks
- renames ``[ssl].dns_challenge_providers`` to ``[ssl].dns_providers``
- moves any credential stored on a certificate into the ``[ssl].dns_providers`` set
  labelled ``cloudflare``, and drops the issuance bookkeeping certificates no longer carry
- relocates the global ``[cloudflare]`` table in fm_config.toml into
  ``[ssl.dns_providers.cloudflare]``, so both scopes store labelled credential sets
"""

import gzip
import json
import shutil
from collections.abc import MutableMapping, MutableSequence
from pathlib import Path

import tomlkit
from ruamel.yaml import YAML

from frappe_manager import CLI_FM_CONFIG_PATH, GLOBAL_DB_IMAGE
from frappe_manager.docker import DockerClient, DockerException
from frappe_manager.migration_manager.migration_base import MigrationBase
from frappe_manager.migration_manager.migration_helpers import MigrationBench
from frappe_manager.migration_manager.version import Version
from frappe_manager.output_manager.context_managers import spinner
from frappe_manager.services_manager.database_service_manager import DatabaseServerServiceInfo, MariaDBManager
from frappe_manager.site_manager.bench_config import REMOVED_CONFIG_KEYS, REMOVED_CONFIG_TABLES, resolve_primary_site
from frappe_manager.ssl_manager.dns_provider import DNSProviderConfig
from frappe_manager.utils import toml_document
from frappe_manager.utils.helpers import get_template_path

# Dropped from the engine command list: it was only ever needed on MariaDB
# 10.6.1 to 10.6.5, where innodb_read_only_compressed defaulted to ON and frappe's
# COMPRESSED core tables became read-only. The engine defaults it off again from
# 10.6.6 onward, so on any tag fm now pins it is a no-op.
STALE_ENGINE_FLAG = "--skip-innodb-read-only-compressed"

# Scratch path INSIDE the engine container, not on the host: the dump is written
# there and copied out with `compose cp`, which needs no extra bind mount and
# leaves nothing behind once the container is recreated.
CONTAINER_TMP = Path("/tmp")  # noqa: S108

# The label a certificate that names no `dns_provider` resolves to, at bench scope then global.
# Credentials relocated by this migration land there, which is where they were already being read
# from, so issuance and renewal keep working across the move.
DEFAULT_DNS_LABEL = "cloudflare"

# Keys 0.20 stopped storing on a certificate. `email` belongs to the credential set, and the rest
# was issuance bookkeeping the certificate files on disk already answer for. Mirrors
# RETIRED_CERTIFICATE_KEYS, which the model drops on read; here they go off disk for good.
DEAD_CERTIFICATE_KEYS = (
    "email",
    "status",
    "cert_path",
    "key_path",
    "issued_date",
    "last_renewal_attempt",
    "toml_exclude",
)

ADMINER_VOLUMES = [
    "./workspace/frappe-bench/sites:/fm-sites:ro",
    "./configs/adminer:/var/www/html/plugins-enabled:ro",
]


def rewrite_global_db_service(engine: MutableMapping, image: str = GLOBAL_DB_IMAGE) -> None:
    """Point a global-db compose service at ``image``, in place.

    Pure and idempotent so the compose surgery can be reasoned about (and tested)
    without Docker: applying it twice is the same as applying it once.

    - the stale compressed-tables flag goes, since the engine defaults it off from
      10.6.6 onward
    - MARIADB_AUTO_UPGRADE is added, but never overwritten: an operator who set it
      to 0 deliberately keeps that choice
    - every other key is left exactly as found
    """
    engine["image"] = image

    command = engine.get("command")
    if command and STALE_ENGINE_FLAG in command:
        command.remove(STALE_ENGINE_FLAG)

    environment = engine.get("environment")
    if environment is None:
        # Without an environment mapping there is nowhere to put the auto-upgrade
        # switch, and the engine would boot on the new version with the previous
        # one's system tables. Create it rather than silently skip.
        environment = {}
        engine["environment"] = environment
    environment.setdefault("MARIADB_AUTO_UPGRADE", 1)


def _carryable_email(value: object) -> bool:
    """Whether ``[ssl.dns_providers]`` can hold this address.

    The certificate models never had an email field after 0.19, so an ``email`` on a
    certificate entry has gone unvalidated for two releases and can be anything, including
    the ``someone@bench.local`` shape a hand edit produces. The credential set validates it
    as an email address, so writing one it rejects would turn a bench whose config merely
    holds a stale key into one that cannot be loaded at all. The credential itself is what
    keeps DNS-01 working; the address only accompanies a Global API Key, and is re-enterable
    with ``fm ssl dns-config cloudflare --email``.
    """
    try:
        DNSProviderConfig(email=str(value))
    except ValueError:
        return False
    return True


class MigrationV0200(MigrationBase):
    version = Version("0.20.0")

    def migrate_bench(self, bench: MigrationBench):
        # Bench nginx config applies to every bench, before the admin-tools
        # early returns below.
        self._place_realip_conf(bench)
        self._refresh_nginx_default_conf(bench)
        self._move_admin_tools_credentials(bench)
        # Ahead of the key drop: this one renames keys the drop list may later be told to remove.
        self._rewrite_ssl_table(bench)
        # Also ahead of it, and for the same reason: this moves `[database]` rather than dropping
        # it, so it has to run while the table is still there.
        self._write_sites_table(bench)
        # After the sites table exists: each history row's single dump has to be filed under a
        # SITE, and the primary is the only site a pre-0.20 bench ever dumped.
        self._rewrite_deploy_history(bench)
        self._rewrite_switch_migrate(bench)
        # Last of the config rewrites: it resolves through `resolve_primary_site`, so the sites
        # table it reads has to be written already.
        self._backfill_default_site(bench)
        self._drop_removed_config_keys(bench)

        compose_path = bench.path / "docker-compose.admin-tools.yml"
        if not compose_path.exists():
            return

        self.backup_manager.backup(compose_path, bench_name=bench.name)

        yaml = YAML()
        yaml.preserve_quotes = True
        compose_data = yaml.load(compose_path.read_text())

        adminer = (compose_data.get("services") or {}).get("adminer")
        if adminer is None:
            return

        adminer["image"] = "adminer:5"

        environment = adminer.get("environment")
        if environment is not None:
            environment.pop("ADMINER_DEFAULT_SERVER", None)
            if not environment:
                del adminer["environment"]

        adminer["volumes"] = ADMINER_VOLUMES

        # Update x-version to current version (plain semver — no ``v`` prefix)
        compose_data["x-version"] = str(self.version)

        with compose_path.open("w") as f:
            yaml.dump(compose_data, f)

        self._heal_adminer_mount(bench, compose_path)
        self.output.print(f"Updated admin tools (Adminer 5 + login plugin) for {bench.name}")

    def _heal_adminer_mount(self, bench: MigrationBench, compose_path: Path) -> None:
        """Place the login plugin, and recreate the container if that just recreated its directory.

        Only when THIS call had to recreate the directory, and only while the tools it feeds are
        actually turned on: docker resolves the bind mount to an inode at container start (see
        docker-compose.admin-tools.tmpl), so a directory that came back from nothing under a
        container that is still running the OLD inode leaves the login plugin invisible to it --
        the exact gap `undo_bench_migrate` used to leave open by rmtree-ing this directory out from
        under a running container. Recreating unconditionally would instead bounce a healthy
        adminer on every dev-build rerun, since 0.20.0.dev0 sorts below 0.20.0.
        """
        if self._place_adminer_plugin(bench) and self._admin_tools_enabled(bench):
            self._recreate_adminer_container(bench, compose_path)

    def _place_adminer_plugin(self, bench: MigrationBench) -> bool:
        """Write the login plugin, and report whether its directory had to be created fresh.

        That directory is a bind-mount SOURCE (docker-compose.admin-tools.tmpl): docker resolves
        it to an inode once, when the adminer container starts, and keeps that inode for the
        container's whole life. Reporting "created fresh" is what lets `migrate_bench` tell a
        directory that was already there -- whatever container is running still has the right
        inode -- from one that just came back from nothing, which a running container has to be
        told about or it keeps serving the vanished one and the login plugin silently disappears.
        """
        adminer_config_dir = bench.path / "configs" / "adminer"
        created = not adminer_config_dir.exists()
        adminer_config_dir.mkdir(parents=True, exist_ok=True)
        plugin_template = get_template_path("adminer/000-fm-login.php")
        (adminer_config_dir / "000-fm-login.php").write_bytes(plugin_template.read_bytes())
        return created

    def _admin_tools_enabled(self, bench: MigrationBench) -> bool:
        """Raw TOML, like the other per-bench reads here: a migration runs against whatever is on
        disk, which `BenchConfig` may not parse yet."""
        config_path = bench.path / "bench_config.toml"
        if not config_path.exists():
            return False
        try:
            return bool(tomlkit.parse(config_path.read_text()).get("admin_tools", False))
        except Exception:
            return False

    def _recreate_adminer_container(self, bench: MigrationBench, compose_path: Path) -> None:
        """Re-resolve the adminer bind mount after its source directory came back from nothing.

        `force_recreate` is the same lever `BenchAdminTools.enable(force_recreate_container=True)`
        pulls for this exact reason (site.py, bench_orchestrator.py, update.py); only `adminer` is
        named because mailpit does not bind-mount this directory and has nothing stale to
        re-resolve. Best-effort: a schema migration failing outright because docker happened to be
        unreachable during this heal would roll back changes that have nothing to do with the
        container, which is worse than leaving the mount stale for the operator to restart by hand.
        """
        try:
            DockerClient(compose_file_path=compose_path, output=self.output).compose.up(
                services=["adminer"],
                detach=True,
                pull="never",
                force_recreate=True,
            )
        except DockerException as e:
            self.output.warning(
                f"Recreated the adminer plugin directory for {bench.name} but could not recreate "
                f"its container ({e}); its login cards will stay stale until it is restarted "
                f"(`docker compose -f {compose_path} up -d --force-recreate adminer`)."
            )

    def _place_realip_conf(self, bench: MigrationBench):
        from frappe_manager import CLI_SERVICES_DIRECTORY
        from frappe_manager.site_manager.modules.realip import build_bench_realip_conf

        subnet = None
        try:
            yaml = YAML()
            data = yaml.load((CLI_SERVICES_DIRECTORY / "docker-compose.yml").read_text())
            ipam = ((data.get("networks") or {}).get("global-frontend-network") or {}).get("ipam") or {}
            subnet = (ipam.get("config") or [{}])[0].get("subnet")
        except Exception:
            subnet = None
        if not subnet:
            try:
                from frappe_manager.utils.network import detect_running_network

                info = detect_running_network()
                subnet = info.get("subnet_cidr") if info else None
            except Exception:
                subnet = None
        if not subnet:
            return
        conf_dir = bench.path / "configs" / "nginx" / "conf" / "custom"
        conf_dir.mkdir(parents=True, exist_ok=True)
        (conf_dir / "real-ip.conf").write_text(build_bench_realip_conf(str(subnet)))
        self.output.print(f"Placed bench nginx real-ip conf for {bench.name}")

    def _refresh_nginx_default_conf(self, bench: MigrationBench):
        """Drop the generated default.conf so the entrypoint re-renders it from
        the new image template (JSON access log). Regenerating it is routine in
        fm (see bench_orchestrator), and every host-side addition lives in
        conf.d/ or custom/ instead of in this file."""
        default_conf = bench.path / "configs" / "nginx" / "conf" / "conf.d" / "default.conf"
        if not default_conf.exists():
            return
        self.backup_manager.backup(default_conf, bench_name=bench.name)
        default_conf.unlink()
        self.output.print(f"Removed generated nginx default.conf for {bench.name} (re-rendered on start)")

    def _move_admin_tools_credentials(self, bench: MigrationBench):
        """Move the old top-level admin tools credentials into the [auth] table.

        The per-bench htpasswd file was renamed to <bench>.htpasswd, so the
        admin-tools one is dropped here; Bench.ensure_fm_nginx_confs() writes
        the new one on the next start or compose regeneration.
        """
        old_htpasswd = bench.path / "configs" / "nginx" / "conf" / "http_auth" / f"{bench.name}-admin-tools.htpasswd"
        if old_htpasswd.exists():
            old_htpasswd.unlink()

        config_path = bench.path / "bench_config.toml"
        if not config_path.exists():
            return

        doc = tomlkit.parse(config_path.read_text())
        if "admin_tools_username" not in doc and "admin_tools_password" not in doc:
            return

        old_user = doc.get("admin_tools_username")
        old_password = doc.get("admin_tools_password")

        if "admin_tools_username" in doc:
            del doc["admin_tools_username"]
        if "admin_tools_password" in doc:
            del doc["admin_tools_password"]

        # An [auth] table already present wins: it is the newer format.
        if "auth" not in doc:
            auth = tomlkit.table()
            auth["user"] = str(old_user) if old_user else "admin"
            if old_password:
                auth["password"] = str(old_password)
            auth["web"] = False
            auth["tools"] = True
            doc["auth"] = auth

        toml_document.save(config_path, doc)
        self.output.print(f"Moved admin tools credentials into \\[auth] for {bench.name}")

    def _drop_removed_config_keys(self, bench: MigrationBench):
        """Strip keys and whole tables that no longer exist from bench_config.toml.

        Driven by ``REMOVED_CONFIG_KEYS`` and ``REMOVED_CONFIG_TABLES``, the same tables the
        loader consults, so deleting a field or a table from the config models needs one line
        there and nothing here.

        ``[switch].search_replace`` never did anything: the switch pipeline runs no
        search-and-replace. ``[registry]`` went entirely, because every field in it existed
        only to run ``docker login``, and docker already owns that: ``~/.docker/config.json``
        holds credentials with multi-registry support and credential helpers fm cannot reach.
        A private registry is now a one-time ``docker login`` on the host, or a login step in
        CI, which every pull and push here inherits.

        Removing them here is what stops the file carrying them forward into a version that
        might read the names again and mean something different by them.
        """
        config_path = bench.path / "bench_config.toml"
        if not config_path.exists():
            return

        doc = tomlkit.parse(config_path.read_text())
        dropped = []
        for table, keys in REMOVED_CONFIG_KEYS.items():
            section = doc.get(table)
            if not isinstance(section, MutableMapping):
                continue
            for key in sorted(keys):
                if key in section:
                    del section[key]
                    dropped.append(f"[{table}].{key}")

        for table in sorted(REMOVED_CONFIG_TABLES):
            if table in doc:
                del doc[table]
                dropped.append(f"[{table}]")

        if not dropped:
            return

        toml_document.save(config_path, doc)
        self.output.print(f"Dropped removed config {', '.join(dropped)} for {bench.name}")

    def _write_sites_table(self, bench: MigrationBench):
        """Give every bench a `[sites."<site>"]` entry, and move `[database."<site>"]` under it.

        Two things, because they are the same write. A bench holds exactly one site today and its
        name is the bench's, so the `[database]` table already had a site as its key; and a bench
        with no external database had nowhere at all that named its site. After this, every bench
        records its site, which is the only fact that survives the bench name and the site name
        coming apart. An entry with no keys is a bare `[sites."<name>"]` header, which round-trips
        and is exactly the record wanted for a bench on the global-db container.

        `import_from_toml` reads only the new spelling, which is the established pattern here
        rather than a new risk: the same release renamed `dns_challenge_providers` to
        `dns_providers` and reads only that. A bench that gets this far carries the new shape, so
        there is nothing to fall back to, and a compatibility branch would land in the one function
        whose dual paths caused three separate bugs in this cycle.

        Idempotent, and it has to be: 0.20.0 is unreleased, so a bench recorded at `0.20.0.dev0`
        re-runs this migration (`0.20.0.dev0 < 0.20.0`) whenever one is triggered at all.
        """
        config_path = bench.path / "bench_config.toml"
        if not config_path.exists():
            return

        doc = tomlkit.parse(config_path.read_text())
        sites = doc.get("sites")
        if not isinstance(sites, MutableMapping):
            sites = tomlkit.table(is_super_table=True)
            doc["sites"] = sites

        def site_entry(name: str) -> MutableMapping:
            entry = sites.get(name)
            if not isinstance(entry, MutableMapping):
                # A real table, NOT a super table: an empty super table renders only through its
                # children, so tomlkit drops it and the site with no external database, which is
                # exactly the one that needs recording, would leave no trace in the file.
                entry = tomlkit.table()
                sites[name] = entry
            return entry

        old = doc.get("database")
        moved = []
        if isinstance(old, MutableMapping):
            for site_name, database in old.items():
                if not isinstance(database, MutableMapping):
                    continue
                entry = site_entry(site_name)
                # A `database` already under the site wins: it is the migrated copy, and overwriting
                # it with the stale top-level one would undo a previous run of this step.
                if "database" not in entry:
                    entry["database"] = database
                    moved.append(site_name)
            del doc["database"]

        # The bench's own site, named after the bench. Runs whether or not there was a `[database]`
        # table, because this is the entry a global-db bench never had.
        created = bench.name not in sites
        primary = site_entry(bench.name)

        # `alias_domains` was bench-level, so it had no site to belong to and the routing table had
        # to send every alias to the primary site. The bench's own site IS that primary, so moving
        # the list there preserves exactly the routing the bench had, with the attribution now
        # recorded instead of inferred.
        aliases = doc.get("alias_domains")
        moved_aliases = False
        if isinstance(aliases, MutableSequence):
            # An existing per-site list wins, same rule as `database`: overwriting it would undo a
            # previous run of this step.
            if aliases and "alias_domains" not in primary:
                primary["alias_domains"] = aliases
                moved_aliases = True
            del doc["alias_domains"]

        if not moved and not created and not moved_aliases and not isinstance(old, MutableMapping):
            return

        toml_document.save(config_path, doc)
        if moved:
            self.output.print(f"Moved \\[database] under \\[sites] for {', '.join(moved)}")
        elif created:
            self.output.print(f"Recorded site {bench.name} under \\[sites]")
        elif not moved_aliases:
            self.output.print(f"Dropped the empty \\[database] table for {bench.name}")
        if moved_aliases:
            self.output.print(f'Moved alias_domains under \\[sites."{bench.name}"]')

    def _backfill_default_site(self, bench: MigrationBench):
        """Write `default_site` when the bench has none, so the answer stops being a guess.

        `default_site` in `common_site_config.json` is the one place "which site is meant when
        none is named" is written down: `bench use` writes it, frappe's CLI reads it, and
        `resolve_primary_site` now reads it ahead of its own name-shaped rules. Those rules
        reconstruct fm's creation convention from string shapes, and on a bench recording a site
        named after itself they picked a site `bench --site` could not open.

        So resolve once by those rules and record the answer. A bench created before fm wrote the
        key, or one whose file was lost, gets a fact instead of a guess that has to be re-derived
        on every command.

        Written host-side, not through `bench use`: this is a key in a host-mounted JSON file, so
        it needs no running container, which a migration cannot assume it has.

        Never overwrites. An existing value is the operator's or frappe's answer, including one set
        by `bench use` after create, and this step exists to fill a gap rather than to take the
        decision back.
        """
        common = bench.path / "workspace" / "frappe-bench" / "sites" / "common_site_config.json"
        if not common.exists():
            return

        try:
            data = json.loads(common.read_text())
        except Exception as e:
            self.output.warning(f"{bench.name}: could not read common_site_config.json ({e}); leaving default_site alone.")
            return

        if data.get("default_site"):
            return

        # `site_names` falls back to the bench's own name, so this is never empty and
        # `resolve_primary_site` therefore answers with a recorded site or with None. There is no
        # third case to guard against: an earlier `resolved not in sites` arm here was unreachable.
        sites = {name: {} for name in bench.site_names}
        resolved = resolve_primary_site(bench.name, sites)
        if not resolved:
            # Genuinely ambiguous: several sites, none named after the bench. Recording a guess
            # here would put fm's choice beyond the operator's sight, and the address form
            # (`fm shell BENCH/SITE`) is what resolves it instead.
            return

        data["default_site"] = resolved
        common.write_text(json.dumps(data, indent=1))
        self.output.print(f"Recorded default_site = {resolved} for {bench.name}")

    def _rewrite_switch_migrate(self, bench: MigrationBench):
        """Turn a surviving `[switch].migrate = "auto"` into `true`.

        The value is gone from the model: it probed the new image for pending patches and app
        version drift and skipped the migration when it found neither, but a DocType field change
        ships with neither, so it reported clean while `bench migrate` would still have synced the
        schema. `SwitchConfig.migrate` is a plain bool now, and `"auto"` fails validation, which
        takes the WHOLE bench config down rather than just that key.

        It becomes `true`, never `false`. `"auto"` meant "migrate when it is needed", so the only
        reading that cannot lose a schema change is the one that migrates. Turning it off silently
        would do exactly what deleting the mode was meant to prevent.
        """
        config_path = bench.path / "bench_config.toml"
        if not config_path.exists():
            return

        doc = tomlkit.parse(config_path.read_text())
        switch = doc.get("switch")
        if not isinstance(switch, MutableMapping) or switch.get("migrate") != "auto":
            return

        switch["migrate"] = True
        toml_document.save(config_path, doc)
        self.output.print(f'Rewrote \\[switch].migrate from "auto" to true for {bench.name}')

    def _rewrite_deploy_history(self, bench: MigrationBench):
        """File each recorded deploy's DB dump under the SITE it was taken from.

        A deploy row carried one `backup = "<path>"` because the pipeline dumped one database.
        It now carries `backups = {"<site>" = "<path>"}`, because a bench serving several sites
        gets a dump per schema and a rollback has to restore all of them.

        The primary site is the correct key for every existing row, and not by assumption: a
        pre-0.20 bench served exactly one site, and the dump in that row came from the only
        schema there was. `_write_sites_table` has already run, so that name is recorded.

        `DeployStateEntry` forbids extra keys, so a row still spelling `backup` does not load
        with a stale field, it refuses the whole config. That is why this rewrites rather than
        leaving the old key for a reader to ignore.
        """
        config_path = bench.path / "bench_config.toml"
        if not config_path.exists():
            return

        doc = tomlkit.parse(config_path.read_text())
        state = doc.get("deploy_state")
        if not isinstance(state, MutableMapping):
            return
        history = state.get("history")
        if not isinstance(history, MutableSequence):
            return

        primary = bench.name
        sites = doc.get("sites")
        if isinstance(sites, MutableMapping) and sites:
            primary = next(iter(sites))

        moved = 0
        for row in history:
            if not isinstance(row, MutableMapping) or "backup" not in row:
                continue
            dump = row.pop("backup")
            # A row recorded with no dump (`backup = None`, a deploy that skipped the backup)
            # becomes an empty mapping rather than one pointing at nothing.
            table = tomlkit.inline_table()
            if dump:
                table[primary] = dump
                moved += 1
            row["backups"] = table

        if moved or any(isinstance(r, MutableMapping) and "backups" in r for r in history):
            toml_document.save(config_path, doc)
            if moved:
                self.output.print(f'Filed {moved} recorded deploy dump(s) under \\[sites."{primary}"] for {bench.name}')

    def _rewrite_ssl_table(self, bench: MigrationBench):
        """Bring a bench's TLS configuration into the one shape the loader reads: an [ssl]
        table holding a certificate array and labelled DNS-01 credential sets.

        0.19 wrote the certificate array and the credential table at the TOP LEVEL of
        bench_config.toml and no later migration moved them under [ssl]. The loader only
        ever looks in [ssl], so one of those benches loads with zero certificates and the
        next save deletes the orphaned keys outright: the whole TLS configuration gone,
        silently. Relocating them is the point of this step.

        A credential stored on a certificate moves too. It was unreachable at issuance,
        because the resolver reads the credential sets and never the certificate, and it
        outlived revocation of the certificate carrying it.
        """
        config_path = bench.path / "bench_config.toml"
        if not config_path.exists():
            return

        doc = tomlkit.parse(config_path.read_text())
        changes: list[str] = []

        ssl = doc.get("ssl")
        if ssl is not None and not isinstance(ssl, MutableMapping):
            # 0.19 tolerated an `ssl` that was an array rather than a table (its own transform
            # bailed on one). Nothing can be merged into that shape, and replacing it would throw
            # away whatever it holds, so the file is left for a human to look at.
            self.logger.debug(f"[_rewrite_ssl_table] {bench.name}: [ssl] is not a table, skipping")
            return

        def ssl_table() -> MutableMapping:
            nonlocal ssl
            if ssl is None:
                ssl = tomlkit.table()
                doc["ssl"] = ssl
            return ssl

        def dns_providers_table() -> MutableMapping:
            target = ssl_table()
            providers = target.get("dns_providers")
            if not isinstance(providers, MutableMapping):
                providers = tomlkit.table()
                target["dns_providers"] = providers
            return providers

        for old_key, new_key in (("ssl_certificates", "certificates"), ("dns_providers", "dns_providers")):
            if old_key not in doc:
                continue
            relocated = doc[old_key]
            del doc[old_key]
            target = ssl_table()
            if new_key in target:
                # [ssl] already holds the copy the loader reads, so this one has never been loaded
                # by any version and there is nothing in it to keep.
                changes.append(f"dropped the orphaned top-level {old_key}")
            else:
                target[new_key] = relocated
                changes.append(f"moved top-level {old_key} into \\[ssl].{new_key}")

        if ssl is not None and "dns_challenge_providers" in ssl:
            renamed = ssl["dns_challenge_providers"]
            del ssl["dns_challenge_providers"]
            if "dns_providers" in ssl:
                changes.append("dropped \\[ssl].dns_challenge_providers, shadowed by dns_providers")
            else:
                ssl["dns_providers"] = renamed
                changes.append("renamed \\[ssl].dns_challenge_providers to dns_providers")

        certificates = ssl.get("certificates") if ssl is not None else None

        for certificate in certificates or []:
            if not isinstance(certificate, MutableMapping):
                continue

            domain = certificate.get("domain") or bench.name
            cert_changes: list[str] = []

            if "preferred_challenge" in certificate:
                challenge = certificate["preferred_challenge"]
                del certificate["preferred_challenge"]
                # An existing challenge_type wins: it is the newer spelling, so it is the one a
                # later fm wrote, and preferred_challenge is whatever it superseded.
                if "challenge_type" not in certificate:
                    certificate["challenge_type"] = challenge
                cert_changes.append("preferred_challenge -> challenge_type")

            # Before the sweep below, which drops the email that belongs with these credentials.
            api_token = certificate.get("api_token")
            api_key = certificate.get("api_key")

            if api_token or api_key:
                providers = dns_providers_table()
                if DEFAULT_DNS_LABEL in providers:
                    cert_changes.append(f"dropped a redundant {DEFAULT_DNS_LABEL} credential")
                else:
                    entry = tomlkit.table()
                    entry["provider"] = DEFAULT_DNS_LABEL
                    email = certificate.get("email")
                    if email and _carryable_email(email):
                        entry["email"] = email
                    if api_token:
                        entry["api_token"] = api_token
                    if api_key:
                        entry["api_key"] = api_key
                    providers[DEFAULT_DNS_LABEL] = entry
                    cert_changes.append(f"credential moved into \\[ssl].dns_providers.{DEFAULT_DNS_LABEL}")
                for key in ("api_token", "api_key"):
                    if key in certificate:
                        del certificate[key]

            dropped = []
            for key in DEAD_CERTIFICATE_KEYS:
                if key in certificate:
                    del certificate[key]
                    dropped.append(key)
            if dropped:
                cert_changes.append(f"dropped {', '.join(dropped)}")

            if cert_changes:
                changes.append(f"{domain}: {'; '.join(cert_changes)}")

        if not changes:
            return

        toml_document.save(config_path, doc)
        self.output.print(f"Rewrote \\[ssl] config for {bench.name}: {', '.join(changes)}")

    def migrate_services(self):
        # First: it is a small file rewrite, and it must not be skipped because the engine
        # upgrade below failed on an unrelated container.
        self._relocate_global_dns_credentials()
        self._upgrade_global_db_engine()

    def _relocate_global_dns_credentials(self):
        """Move the global [cloudflare] table in fm_config.toml into [ssl.dns_providers.cloudflare].

        Global credentials are labelled sets now, exactly like the bench-scoped ones, so the
        default Cloudflare account becomes the set labelled cloudflare instead of a table of
        its own. `FMCloudflareConfig` and the table it read are gone from the model, so a file
        left unconverted loses its credential the next time fm writes the file.

        Raw tomlkit rather than FMConfigManager on purpose: a migration runs against whatever
        version is on disk, and the model can no longer represent what is being read here.
        """
        config_path = CLI_FM_CONFIG_PATH

        if not config_path.exists():
            return

        doc = tomlkit.parse(config_path.read_text())
        legacy = doc.get("cloudflare")

        if not isinstance(legacy, MutableMapping):
            return

        ssl = doc.get("ssl")
        if ssl is not None and not isinstance(ssl, MutableMapping):
            self.logger.debug("[_relocate_global_dns_credentials] [ssl] is not a table, skipping")
            return

        self.backup_manager.backup(config_path)

        if ssl is None:
            ssl = tomlkit.table()
            doc["ssl"] = ssl

        providers = ssl.get("dns_providers")
        if not isinstance(providers, MutableMapping):
            providers = tomlkit.table()
            ssl["dns_providers"] = providers

        api_token = legacy.get("api_token")
        api_key = legacy.get("api_key")

        if DEFAULT_DNS_LABEL in providers:
            self.output.print(
                f"Dropped the legacy global \\[cloudflare] table; \\[ssl.dns_providers.{DEFAULT_DNS_LABEL}] already "
                "holds a credential and wins",
            )
        elif api_token or api_key:
            entry = tomlkit.table()
            entry["provider"] = DEFAULT_DNS_LABEL
            email = legacy.get("email")
            if email:
                entry["email"] = email
            if api_token:
                entry["api_token"] = api_token
            if api_key:
                entry["api_key"] = api_key
            providers[DEFAULT_DNS_LABEL] = entry
            self.output.print(f"Moved the global Cloudflare credential into \\[ssl.dns_providers.{DEFAULT_DNS_LABEL}]")
        else:
            # Nothing to carry over. A credential-less set is not written, because the config
            # writer drops one anyway and the resolver treats it as absent.
            self.output.print("Dropped the legacy global \\[cloudflare] table, which held no credential")

        del doc["cloudflare"]

        if len(providers) == 0:
            del ssl["dns_providers"]
        if len(ssl) == 0:
            del doc["ssl"]

        toml_document.save(config_path, doc)

    def _upgrade_global_db_engine(self):
        """Move global-db onto the engine tag frappe tests against.

        New installs get it straight from the services template. Existing ones are
        only ever moved here, deliberately and once, because an InnoDB datadir
        upgrade cannot be undone: a downgrade needs the dump this step takes first.
        A direct 10.6 to 11.x jump is supported for a single node (the one-major-at-
        a-time rule applies to rolling Galera upgrades), and the engine's own
        MARIADB_AUTO_UPGRADE handles the system tables on first boot.
        """
        compose_file_manager = self.services_manager.compose_file_manager

        if not compose_file_manager.exists():
            self.logger.debug("[_upgrade_global_db_engine] services compose not found, skipping")
            return

        services = compose_file_manager.yml.get("services") or {}
        engine = services.get("global-db")

        if not engine or "image" not in engine:
            self.logger.debug("[_upgrade_global_db_engine] no global-db image to upgrade, skipping")
            return

        current_image = str(engine["image"])

        if current_image == GLOBAL_DB_IMAGE:
            self.logger.debug(f"[_upgrade_global_db_engine] already on {GLOBAL_DB_IMAGE}")
            return

        self.output.print(f"Upgrading global database engine: {current_image} -> {GLOBAL_DB_IMAGE}")

        database_manager = MariaDBManager(
            DatabaseServerServiceInfo.import_from_compose_file("global-db", compose_file_manager),
            compose_file_manager,
            self.services_manager.docker,
            output_handler=self.output,
        )

        dump_path = self._dump_whole_engine(database_manager)

        # A version change is only safe from a clean shutdown; crash recovery across
        # engine versions is not supported. compose stop sends SIGTERM, which is what
        # the server treats as a graceful shutdown request.
        with spinner(self.output, "Stopping global-db for the engine upgrade"):  # type: ignore[arg-type]
            self.services_manager.compose.stop(services=["global-db"], timeout=120)

        rewrite_global_db_service(engine)

        compose_file_manager.write_to_file()

        with spinner(self.output, f"Starting global-db on {GLOBAL_DB_IMAGE}"):  # type: ignore[arg-type]
            self.services_manager.compose.up(
                services=["global-db"],
                force_recreate=True,
                detach=True,
                pull="missing",
            )
            database_manager.wait_till_db_start()

        self.output.print(f"Global database engine is now {GLOBAL_DB_IMAGE}")
        self.output.print(f"Pre-upgrade dump of every database kept at {dump_path}")
        self.output.warning(
            f"{GLOBAL_DB_IMAGE} is the engine frappe v16 tests against. Benches still on frappe v15 will print a "
            "MariaDB version warning when creating or restoring a site, because v15 is tested on 10.6 and warns from "
            "10.9 up. Nothing else changes for them, and a v15 bench that needs an older engine can be pointed at its "
            "own database server instead of the shared one.",
        )

    def _dump_whole_engine(self, database_manager: MariaDBManager) -> Path:
        """Logical backup of the entire server, taken while the old engine still runs.

        This is the rollback path: the datadir upgrade is one way, so without this
        there is no route back to the previous engine.
        """
        # Same timestamp the migration's other backups use, so one run's artifacts
        # group together instead of drifting by a second.
        dump_name = f"global-db-all-databases-{self.backup_manager.migration_timestamp}.sql"

        container_dump_path = CONTAINER_TMP / dump_name
        host_dump_path = self.backup_manager.backup_dir / dump_name

        with spinner(self.output, "Backing up every database before the engine upgrade"):  # type: ignore[arg-type]
            database_manager.db_export_all(container_dump_path)
            self.services_manager.compose.cp(
                f"global-db:{container_dump_path}",
                str(host_dump_path),
                stream=False,
            )

            compressed_dump_path = host_dump_path.with_suffix(".sql.gz")
            with host_dump_path.open("rb") as plain, gzip.open(compressed_dump_path, "wb") as compressed:
                shutil.copyfileobj(plain, compressed)
            host_dump_path.unlink()

        return compressed_dump_path

    def undo_services_migrate(self):
        """Put the compose file and the global fm config back; the datadir stays on the newer engine.

        A restored compose alone would point an older engine at a datadir it cannot
        read, so this only rewinds the file and tells the operator where the dump is.
        Rolling the data back is a deliberate restore, not something to do implicitly
        during a rollback.
        """
        compose_path = self.services_manager.compose_file_manager.compose_path

        for backup in self.backup_manager.backups:
            if backup.src == compose_path:
                self.backup_manager.restore(backup, force=True)
                self.output.print("Restored the services compose file")
                break

        for backup in self.backup_manager.backups:
            if backup.src == CLI_FM_CONFIG_PATH:
                self.backup_manager.restore(backup, force=True)
                self.output.print("Restored the global fm config")
                break

        self.output.warning(
            "The global database datadir was upgraded in place and is NOT rolled back. To return to the previous "
            f"engine, restore the dump in {self.backup_manager.backup_dir} into a fresh datadir.",
        )

    def undo_bench_migrate(self, bench: MigrationBench):
        compose_path = bench.path / "docker-compose.admin-tools.yml"

        for backup in self.backup_manager.backups:
            if backup.src == compose_path:
                self.backup_manager.restore(backup, force=True)
                self.output.print(f"Restored admin tools compose for {bench.name}")
                break

        # Only the FILE this migration placed, never the directory: docker resolves that
        # directory as a bind-mount SOURCE to an inode when the adminer container starts
        # (docker-compose.admin-tools.tmpl) and keeps that inode for its whole life. rmtree-ing
        # it here, while a real container may still be running against it, strands that inode --
        # the container keeps serving a directory that no longer exists on disk, the login
        # plugin vanishes from its view, and Adminer falls back to its stock form with nothing to
        # warn the operator. That is exactly what a rolled-back `--rerun` did to a live bench.
        # The directory itself belongs to `BenchAdminTools`, not to this migration step, so there
        # is nothing here that needs a docker call, and nothing that can fail because docker is
        # unreachable.
        adminer_plugin = bench.path / "configs" / "adminer" / "000-fm-login.php"
        adminer_plugin.unlink(missing_ok=True)
