"""
Migration for v1.0.0.

This squashes the never-released v0.20.0 and v0.21.0 development migrations into one:
neither version ever shipped to PyPI (0.19.0 is still the latest release), so no real
install is stamped at either, and merging them means a reader sees one coherent story
instead of wondering why one migration does two unrelated families of thing.

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

Schema ledger:

- renames `[migration_state]` to `[schema]`, and its `migrated_to` key to `version`, in both
  bench_config.toml and the global fm_config.toml
- the global side additionally folds a leftover `system_migrated_to` into `version`

Global service rename: the compose service `global-db` becomes `mariadb` and
`global-nginx-proxy` becomes `nginx-proxy`; the shared networks and the macOS data volume
drop the `global` token the same way. Engine names are the point: a postgres or traefik
sibling can then arrive as a new service instead of forcing another rename. Nothing legacy
survives -- fm after this migration only knows the new names.

What has to move together, and why the order is what it is:

- `global-db` is not a label, it is the ADDRESS every site dials: `db_host` in each
  `site_config.json`. So the service rename and the site-config rewrite are one atomic
  cutover; done separately, every bench on the host is down until the other half lands.
- docker can neither rename a network nor a volume, and two networks cannot hold the
  same subnet. So: every bench's containers come DOWN (removed, not stopped -- removed
  containers are what frees the old networks), the old stack and its networks are
  removed, and the new stack recreates the networks with the SAME subnets read off the
  old compose (bench `real-ip.conf` trusts that subnet; it must not drift).
- On macOS the databases live in the `services_fm-global-db-data` volume; its content is
  copied into `fm-mariadb-data` while everything is stopped. The old volume is left in
  place as the rollback path and reported, never deleted here.
- Every bench's compose files reference the shared networks as `external`, so they are
  rewritten in the same window; benches that were running are brought back up on the
  new names.

All per-install work happens in `migrate_services`, for EVERY bench, targeted or not: a
partial cutover (one bench renamed, the stack not, or the reverse) leaves benches down.
`migrate_bench` re-applies the same per-bench rewrite idempotently, so a bench that was
missed (restored from backup, or migrated later by the bench gate) heals when its own
migration runs, and the executor's version stamping keeps its usual meaning.

The admin-tools/config work above runs FIRST inside `migrate_bench`/`migrate_services`,
then the service rename; rollback reverses that, undoing the rename before the
admin-tools/config work, since the rename ran last.
"""

import contextlib
import gzip
import json
import platform
import shutil
from collections.abc import MutableMapping, MutableSequence
from pathlib import Path
from typing import Any, cast

import tomlkit
from ruamel.yaml import YAML

from frappe_manager import CLI_BENCHES_DIRECTORY, CLI_FM_CONFIG_PATH, MARIADB_IMAGE
from frappe_manager.docker import DockerClient, DockerException
from frappe_manager.migration_manager.migration_base import MigrationBase
from frappe_manager.migration_manager.migration_constants import DOCKER_COMPOSE_DOWN_TIMEOUT_SECONDS
from frappe_manager.migration_manager.migration_exceptions import MigrationExceptionInBench
from frappe_manager.migration_manager.migration_helpers import MigrationBench
from frappe_manager.migration_manager.version import Version
from frappe_manager.output_manager.context_managers import spinner
from frappe_manager.services_manager.database_service_manager import DatabaseServerServiceInfo, MariaDBManager
from frappe_manager.site_manager.bench_config import (
    REMOVED_CONFIG_KEYS,
    REMOVED_CONFIG_TABLES,
    resolve_primary_site,
)
from frappe_manager.ssl_manager.dns_provider import DNSProviderConfig
from frappe_manager.utils import toml_document
from frappe_manager.utils.docker import run_command_with_exit_code
from frappe_manager.utils.helpers import get_template_path
from frappe_manager.utils.site import host_bench_dir

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

# Old -> new, at every layer. The compose network KEYS differ from the docker network
# NAMES (`name:` fields), so both mappings are spelled out rather than derived.
SERVICE_RENAMES = {"global-db": "mariadb", "global-nginx-proxy": "nginx-proxy"}
CONTAINER_RENAMES = {"fm_global-db": "fm_mariadb", "fm_global-nginx-proxy": "fm_nginx-proxy"}
NETWORK_KEY_RENAMES = {"global-frontend-network": "frontend-network", "global-backend-network": "backend-network"}
NETWORK_NAME_RENAMES = {
    "fm-global-frontend-network": "fm-frontend-network",
    "fm-global-backend-network": "fm-backend-network",
}
OLD_VOLUME_KEY = "fm-global-db-data"
NEW_VOLUME_KEY = "mariadb-data"
NEW_VOLUME_NAME = "fm-mariadb-data"
# compose prefixes an unnamed volume with the project (the services directory name), so
# the on-daemon name of the old volume carries `services_`; both spellings are checked.
OLD_VOLUME_CANDIDATES = ("services_fm-global-db-data", "fm-global-db-data")
# NEW compose network key -> OLD daemon network name, for the pre-deletion subnet capture.
NEW_KEY_TO_OLD_NETWORK = {
    "frontend-network": "fm-global-frontend-network",
    "backend-network": "fm-global-backend-network",
}


def rewrite_global_db_service(engine: MutableMapping, image: str = MARIADB_IMAGE) -> None:
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


def _rename_keys(mapping, renames: dict) -> None:
    """Rename dict keys in place, preserving the order of every key.

    ruamel round-trips comments attached to keys; rebuilding the mapping key by key keeps
    untouched entries (and a user's own additions) exactly where they were.
    """
    if not mapping:
        return
    entries = [(renames.get(key, key), mapping[key]) for key in list(mapping.keys())]
    for key in list(mapping.keys()):
        del mapping[key]
    for key, value in entries:
        mapping[key] = value


def _rename_service_networks(service: dict, renames: dict) -> None:
    """A service's `networks` is a list in the services compose and a mapping in bench
    composes; both shapes appear in the wild, so both are handled."""
    networks = service.get("networks")
    if networks is None:
        return
    if isinstance(networks, list):
        service["networks"] = [renames.get(n, n) for n in networks]
    else:
        _rename_keys(networks, renames)


def rewrite_compose_for_rename(compose_data: dict) -> bool:
    """Apply every rename to one loaded compose document; returns whether anything changed.

    Handles the services compose and the three bench composes alike: only the keys that
    are present get touched, everything else (subnets, env, secrets, user additions) is
    left byte-identical.
    """
    changed = False

    services = compose_data.get("services") or {}
    for service in services.values():
        if isinstance(service, dict):
            container = service.get("container_name")
            if container in CONTAINER_RENAMES:
                service["container_name"] = CONTAINER_RENAMES[container]
                changed = True
            if service.get("networks"):
                before = repr(service.get("networks"))
                _rename_service_networks(service, NETWORK_KEY_RENAMES)
                changed = changed or repr(service.get("networks")) != before
            # The macOS mariadb service mounts the data volume by its compose KEY.
            volumes = service.get("volumes")
            if isinstance(volumes, list):
                for i, mount in enumerate(volumes):
                    if isinstance(mount, str) and mount.startswith(f"{OLD_VOLUME_KEY}:"):
                        volumes[i] = f"{NEW_VOLUME_KEY}:" + mount.split(":", 1)[1]
                        changed = True
    if any(old in services for old in SERVICE_RENAMES):
        _rename_keys(services, SERVICE_RENAMES)
        changed = True

    networks = compose_data.get("networks")
    if networks:
        if any(old in networks for old in NETWORK_KEY_RENAMES):
            _rename_keys(networks, NETWORK_KEY_RENAMES)
            changed = True
        for network in networks.values():
            if isinstance(network, dict) and network.get("name") in NETWORK_NAME_RENAMES:
                network["name"] = NETWORK_NAME_RENAMES[network["name"]]
                changed = True

    volumes = compose_data.get("volumes")
    if volumes and OLD_VOLUME_KEY in volumes:
        _rename_keys(volumes, {OLD_VOLUME_KEY: NEW_VOLUME_KEY})
        # An explicit name pins the on-daemon volume to a project-independent, clean
        # name; without it compose would prefix the key with the directory name again.
        volumes[NEW_VOLUME_KEY] = {"name": NEW_VOLUME_NAME}
        changed = True

    return changed


class MigrationV100(MigrationBase):
    version = Version("1.0.0")

    def init(self):
        super().init()
        # Benches that were up before the cutover; only these are brought back.
        self._was_running: set[str] = set()
        # Daemon-truth subnets of the OLD networks, captured before anything is deleted;
        # keyed by the NEW compose network key. The rollback needs them too.
        self._old_subnets: dict[str, str | None] = {}

    def migrate_bench(self, bench: MigrationBench):
        self._admin_tools_and_config_bench(bench)
        self._service_rename_bench(bench)

    def migrate_services(self):
        self._admin_tools_and_config_services()
        self._service_rename_services()

    def undo_services_migrate(self):
        # The rename ran last, so its rollback must undo first.
        self._undo_service_rename_services()
        self._undo_admin_tools_and_config_services()

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

    def _admin_tools_and_config_bench(self, bench: MigrationBench):
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
        # Before the backups reshape below: a tag-era file gets its keys renamed first, so a
        # config carrying BOTH old shapes leaves this method fully current.
        self._rename_deploy_tag_keys(bench)
        # After the sites table exists: each history row's single dump has to be filed under a
        # SITE, and the primary is the only site a pre-0.20 bench ever dumped.
        self._rewrite_deploy_history(bench)
        self._rewrite_switch_migrate(bench)
        # Last of the config rewrites: it resolves through `resolve_primary_site`, so the sites
        # table it reads has to be written already.
        self._backfill_default_site(bench)
        self._drop_removed_config_keys(bench)
        # Last: renames the ledger table itself, ahead of the bench version stamp the executor
        # writes after `migrate_bench` returns, which only mutates an existing [schema] table.
        self._rename_schema_table(bench)

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
        adminer on every dev-build rerun, since 1.0.0.dev0 sorts below 1.0.0.
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

    def _rename_schema_table(self, bench: MigrationBench):
        """Rename `[migration_state]` to `[schema]`, and its `migrated_to` key to `version`,
        in bench_config.toml.

        Skipped once `[schema]` exists: a bench already on the new spelling, including one this
        step already ran on, must not have a stale `[migration_state]` clobber it on a later run.

        Every other key -- `last_migration_date`, any stray fm does not recognise -- carries
        over untouched: this only touches the table's name and the one key that changed shape.
        """
        config_path = bench.path / "bench_config.toml"
        if not config_path.exists():
            return

        doc = tomlkit.parse(config_path.read_text())
        if "schema" in doc:
            return

        state = doc.get("migration_state")
        if not isinstance(state, MutableMapping):
            return

        if "migrated_to" in state:
            value = state.pop("migrated_to")
            if "version" not in state:
                state["version"] = value

        del doc["migration_state"]
        doc["schema"] = state

        toml_document.save(config_path, doc)
        self.output.print(f"Renamed \\[migration_state] to \\[schema] for {bench.name}")

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

        Idempotent, and it has to be: 1.0.0 is unreleased, so a bench recorded at `1.0.0.dev0`
        re-runs this migration (`1.0.0.dev0 < 1.0.0`) whenever one is triggered at all.
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

    def _rename_deploy_tag_keys(self, bench: MigrationBench):
        """Rename tag-era ``[deploy_state]`` keys to the image spelling the loader reads.

        Early 0.20 dev builds recorded ``current_tag``/``previous_tag`` and a ``tag`` on each
        history row; the pipeline later moved to full image references and renamed the fields
        to ``current_image``/``previous_image``/``image`` -- without a migration, so a tag-era
        bench failed ``DeployStateEntry`` validation (``image Field required``) on every
        ``fm list`` and its rollback data was unreadable by ``fm switch --previous``. The
        recorded values were already full references, so this is a pure key rename.

        A row somehow carrying BOTH spellings keeps ``image`` and drops ``tag``: the old key is
        not an unknown fm retains evidence of, it is the stale spelling of a key fm owns.
        """
        config_path = bench.path / "bench_config.toml"
        if not config_path.exists():
            return

        doc = tomlkit.parse(config_path.read_text())
        state = doc.get("deploy_state")
        if not isinstance(state, MutableMapping):
            return

        renamed = 0
        for old, new in (("current_tag", "current_image"), ("previous_tag", "previous_image")):
            if old in state:
                value = state.pop(old)
                if new not in state:
                    state[new] = value
                renamed += 1

        history = state.get("history")
        if isinstance(history, MutableSequence):
            for row in history:
                if isinstance(row, MutableMapping) and "tag" in row:
                    value = row.pop("tag")
                    if "image" not in row:
                        row["image"] = value
                    renamed += 1

        if renamed:
            toml_document.save(config_path, doc)
            self.output.print(f"Renamed {renamed} tag-era deploy_state key(s) to the image spelling for {bench.name}")

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

    def _admin_tools_and_config_services(self):
        # First: it is a small file rewrite, and it must not be skipped because the engine
        # upgrade below failed on an unrelated container.
        self._relocate_global_dns_credentials()
        # After the credential relocation: a config carrying both a legacy [cloudflare] table
        # and [migration_state] then backs the file up only once -- see the guard inside this
        # step -- from whichever of the two ran first and actually mutated it.
        self._rename_global_schema_table()
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

    def _rename_global_schema_table(self):
        """Rename `[migration_state]` to `[schema]` in fm_config.toml, folding the pre-rename
        `migrated_to` then `system_migrated_to` keys into `version`.

        Skipped once `[schema]` exists, the same guard as the bench-scope rename: a config
        already on the new spelling must not have a stale `[migration_state]` clobber it.

        Backs the file up only when nothing already has this run: `_relocate_global_dns_
        credentials` may already have backed up and rewritten the same file ahead of this call,
        and a second `backup()` for the same path would copy that ALREADY-mutated file over the
        pristine one `BackupManager` keeps at one fixed, unversioned destination per source path.
        """
        config_path = CLI_FM_CONFIG_PATH
        if not config_path.exists():
            return

        doc = tomlkit.parse(config_path.read_text())
        if "schema" in doc:
            return

        state = doc.get("migration_state")
        if not isinstance(state, MutableMapping):
            return

        if not any(backup.src == config_path for backup in self.backup_manager.backups):
            self.backup_manager.backup(config_path)

        for legacy_key in ("migrated_to", "system_migrated_to"):
            if legacy_key in state:
                value = state.pop(legacy_key)
                if "version" not in state:
                    state["version"] = value

        del doc["migration_state"]
        doc["schema"] = state

        toml_document.save(config_path, doc)
        self.output.print("Renamed \\[migration_state] to \\[schema] in the global fm config")

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

        if current_image == MARIADB_IMAGE:
            self.logger.debug(f"[_upgrade_global_db_engine] already on {MARIADB_IMAGE}")
            return

        self.output.print(f"Upgrading global database engine: {current_image} -> {MARIADB_IMAGE}")

        database_manager = MariaDBManager(
            DatabaseServerServiceInfo.import_from_compose_file("global-db", compose_file_manager),
            compose_file_manager,
            self.services_manager.docker,
            output_handler=self.output,
        )

        # Additive v0.21+ amendment, default behavior unchanged: --skip-backup /
        # --skip-db-backup on `fm services migrate` skips this dump. It exists because a
        # host whose data is too large to dump was otherwise unable to upgrade at all --
        # but the dump is the ONLY route back from the one-way datadir upgrade, so the
        # skip is loud about what it costs. This dump is also the one engine-scale backup
        # that bypasses the shared chokepoints (BackupManager.backup, bench_db_backup),
        # hence the local guard. CONVENTION for future engine-scale migrations: fm owns
        # the datadir and stops the engine anyway, so take a PHYSICAL datadir snapshot
        # through a shared MigrationBase helper (build it with its first caller) instead
        # of a logical dump - bit-perfect rollback, an order of magnitude faster, and the
        # kind policy reaches it centrally.
        skip_dump = bool(self.migration_executor) and (
            self.migration_executor.skip_backup or self.migration_executor.skip_db_backup
        )
        if skip_dump:
            dump_path = None
            self.output.warning(
                "Skipping the whole-engine dump: the datadir upgrade is one-way and this dump "
                "is the only route back. Proceeding WITHOUT a rollback path."
            )
        else:
            dump_path = self._dump_whole_engine(database_manager)

        # A version change is only safe from a clean shutdown; crash recovery across
        # engine versions is not supported. compose stop sends SIGTERM, which is what
        # the server treats as a graceful shutdown request.
        with spinner(self.output, "Stopping global-db for the engine upgrade"):  # type: ignore[arg-type]
            self.services_manager.compose.stop(services=["global-db"], timeout=120)

        rewrite_global_db_service(engine)

        compose_file_manager.write_to_file()

        with spinner(self.output, f"Starting global-db on {MARIADB_IMAGE}"):  # type: ignore[arg-type]
            self.services_manager.compose.up(
                services=["global-db"],
                force_recreate=True,
                detach=True,
                pull="missing",
            )
            database_manager.wait_till_db_start()

        self.output.print(f"Global database engine is now {MARIADB_IMAGE}")
        if dump_path is not None:
            self.output.print(f"Pre-upgrade dump of every database kept at {dump_path}")
        self.output.warning(
            f"{MARIADB_IMAGE} is the engine frappe v16 tests against. Benches still on frappe v15 will print a "
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
        # Additive guard, not a behavior change: the session dir used to be created eagerly
        # by BackupManager's constructor; it is lazy now, and this dump writes into the dir
        # directly rather than through backup() (which mkdirs its own dest parent).
        host_dump_path.parent.mkdir(parents=True, exist_ok=True)

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

    def _undo_admin_tools_and_config_services(self):
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

    def _service_rename_bench(self, bench: MigrationBench):
        # Idempotent re-application: normally `migrate_services` already rewrote this
        # bench; this run covers a bench restored from backup or migrated later through
        # the bench gate, and stamps its version through the executor as usual.
        self._rewrite_bench_files(bench)

    def _service_rename_services(self):
        compose_file_manager = self.services_manager.compose_file_manager
        if not compose_file_manager.exists():
            self.logger.debug("[rename] services compose not found, skipping")
            return

        services = compose_file_manager.yml.get("services") or {}
        if "mariadb" in services and "global-db" not in services:
            # Dev builds re-run this migration (1.0.0.dev0 sorts below 1.0.0); an
            # already-renamed install has nothing to cut over.
            self.logger.debug("[rename] services already on engine names, skipping")
            self._migrate_all_benches_files()
            return

        self.output.print("Renaming the global services to their engine names (mariadb, nginx-proxy)")

        # Daemon truth for the subnets, captured while the old networks still exist. The
        # compose file cannot be trusted for this on a legacy install: it marks the
        # networks `external`, and compose IGNORES ipam on an external network, so that
        # block was free to rot while the daemon's allocation moved on.
        self._old_subnets = {
            new_key: self._daemon_subnet(old_name) for new_key, old_name in NEW_KEY_TO_OLD_NETWORK.items()
        }

        # 1. Every bench's containers must be REMOVED: removed containers are what free
        #    the old networks, and the bench composes are about to reference new ones.
        all_benches = self.benches_manager.get_all_benches()
        with spinner(self.output, "Taking every bench down for the rename"):  # type: ignore[arg-type]
            for bench_name, compose_path in all_benches.items():
                bench = MigrationBench(bench_name, compose_path.parent, output=self.output)
                if bench.running:
                    self._was_running.add(bench_name)
                try:
                    bench.compose.down(
                        remove_orphans=True,
                        volumes=False,
                        timeout=DOCKER_COMPOSE_DOWN_TIMEOUT_SECONDS,
                        stream=False,
                    )
                except DockerException as e:
                    raise MigrationExceptionInBench(
                        f"Could not take bench {bench_name} down for the rename: {e}"
                    ) from e

        # 2. The old stack goes down with its networks (it owns them).
        with spinner(self.output, "Taking the global stack down"):  # type: ignore[arg-type]
            self.services_manager.compose.down(
                remove_orphans=True,
                volumes=False,
                timeout=DOCKER_COMPOSE_DOWN_TIMEOUT_SECONDS,
                stream=False,
            )
        for network in NETWORK_NAME_RENAMES:
            # Belt and braces: a lingering old network would collide with the new one on
            # the same subnet. Best-effort, because compose down normally removed them.
            with contextlib.suppress(Exception):
                run_command_with_exit_code(["docker", "network", "rm", network], stream=False)

        # 3. macOS: the databases live in a named volume; copy them to the new name
        #    while everything is stopped. Linux bind-mounts services/mariadb/data and
        #    needs nothing.
        if platform.system() == "Darwin":
            self._copy_data_volume()

        # 4. Rewrite the services compose in place (secrets, envs and user edits travel
        #    untouched), then NORMALIZE network ownership: the services compose owns the
        #    shared networks (the template shape), benches only consume them as external.
        #    Legacy installs marked them external HERE too, which left the networks with
        #    no owner at all -- nothing would recreate them after the deletion above.
        rewrite_compose_for_rename(compose_file_manager.yml)
        self._normalize_services_networks(compose_file_manager.yml)
        compose_file_manager.yml["x-version"] = str(self.version)
        compose_file_manager.write_to_file()

        # 5. Up: recreates the networks (same subnets, new names) and both containers.
        with spinner(self.output, "Starting the renamed global stack"):  # type: ignore[arg-type]
            self.services_manager.compose.up(services=[], detach=True, pull="missing")
            MariaDBManager(
                DatabaseServerServiceInfo.import_from_compose_file("mariadb", compose_file_manager),
                compose_file_manager,
                self.services_manager.docker,
                output_handler=self.output,
            ).wait_till_db_start()

        # 6. Every bench, targeted or not: composes and site configs, then back up if it
        #    was running. A bench left half-cut here would be down until someone migrated
        #    it by hand, which is exactly the partial state this migration must not leave.
        self._migrate_all_benches_files()

        self.output.print("Global services renamed: mariadb, nginx-proxy")

    def _copy_data_volume(self):
        old_volume = next((name for name in OLD_VOLUME_CANDIDATES if self._volume_exists(name)), None)
        if old_volume is None:
            self.logger.debug("[rename] no old data volume found, skipping copy")
            return
        if self._volume_exists(NEW_VOLUME_NAME):
            self.output.print(f"Data volume {NEW_VOLUME_NAME} already exists, keeping it")
            return
        with spinner(self.output, f"Copying database volume {old_volume} -> {NEW_VOLUME_NAME}"):  # type: ignore[arg-type]
            # `cp -a` preserves ownership and permissions, which mariadb checks on boot.
            # The new named volume is created by the mount itself.
            self.services_manager.docker.run(
                image="alpine:3",
                volume=[f"{old_volume}:/from:ro", f"{NEW_VOLUME_NAME}:/to"],
                rm=True,
                # shlex-split into ["sh", "-c", "cp -a /from/. /to/"]; a single unsplit
                # string would be handed to docker as the binary name itself.
                command="sh -c 'cp -a /from/. /to/'",
                stream=False,
            )
        self.output.print(
            f"Databases copied to {NEW_VOLUME_NAME}. The old volume {old_volume} was kept as the "
            f"rollback path; once satisfied, remove it with: docker volume rm {old_volume}"
        )

    @staticmethod
    def _volume_exists(name: str) -> bool:
        try:
            run_command_with_exit_code(["docker", "volume", "inspect", name], stream=False)
        except Exception:
            return False
        return True

    def _normalize_services_networks(self, compose_data: dict) -> None:
        """Make the services compose the OWNER of the (renamed) shared networks.

        Drops `external: true` where a legacy install carried it, and writes the ipam
        subnet from the daemon capture -- the only source that could not lie, since
        compose ignores ipam on an external network and that block was free to rot.
        A pinned `ipv4_address` on the proxy stays valid: the subnet is the same one
        the daemon allocated it from.
        """
        networks = compose_data.get("networks") or {}
        for key in NETWORK_KEY_RENAMES.values():
            network = networks.get(key)
            if not isinstance(network, dict):
                continue
            network.pop("external", None)
            subnet = self._old_subnets.get(key)
            if subnet:
                network["ipam"] = {"config": [{"subnet": subnet}]}

    @staticmethod
    def _daemon_subnet(network_name: str) -> str | None:
        try:
            out = run_command_with_exit_code(
                ["docker", "network", "inspect", "--format", "{{(index .IPAM.Config 0).Subnet}}", network_name],
                stream=False,
            )
            subnet = "".join(out.stdout).strip()  # type: ignore[union-attr]
        except Exception:
            return None
        return subnet or None

    def _migrate_all_benches_files(self):
        for bench_name, compose_path in self.benches_manager.get_all_benches().items():
            bench = MigrationBench(bench_name, compose_path.parent, output=self.output)
            self._rewrite_bench_files(bench)
            if bench_name in self._was_running:
                self._start_bench(bench)

    def _rewrite_bench_files(self, bench: MigrationBench):
        changed_any = False
        for compose_name in ("docker-compose.yml", "docker-compose.workers.yml", "docker-compose.admin-tools.yml"):
            compose_path = bench.path / compose_name
            if not compose_path.exists():
                continue
            # docker-compose.yml is backed up by bench_basic_backup for targeted benches;
            # the sweep over ALL benches backs up everything it is about to touch itself.
            self.backup_manager.backup(compose_path, bench_name=bench.name)
            yaml = YAML()
            yaml.preserve_quotes = True
            compose_data = yaml.load(compose_path.read_text())
            if compose_data and rewrite_compose_for_rename(compose_data):
                compose_data["x-version"] = str(self.version)
                with compose_path.open("w") as f:
                    yaml.dump(compose_data, f)
                changed_any = True

        changed_any |= self._rewrite_db_hosts(bench)

        if changed_any:
            self.output.print(f"Renamed shared services in {bench.name}'s compose and site configs")

        # Reported separately, and deliberately not folded into `changed_any`: this is the
        # telemetry table rename, not the shared-service rename, and it prints its own line.
        self._rewrite_telemetry_table(bench)

    def _rewrite_db_hosts(self, bench: MigrationBench) -> bool:
        """`db_host: global-db` -> `mariadb`, in every site config and the legacy common
        fallback. Only the exact shared-service value is touched: an external endpoint
        (RDS, a DNS name, an IP) is someone else's server and stays exactly as written.
        """
        import json

        changed = False
        sites_dir = host_bench_dir(bench.path) / "sites"
        candidates = [sites_dir / "common_site_config.json"]
        if sites_dir.exists():
            candidates += sorted(p / "site_config.json" for p in sites_dir.iterdir() if p.is_dir())
        for config_path in candidates:
            if not config_path.is_file():
                continue
            try:
                config = json.loads(config_path.read_text())
            except ValueError:
                self.output.warning(f"{bench.name}: {config_path.name} is not valid JSON, leaving it untouched")
                continue
            if config.get("db_host") == "global-db":
                self.backup_manager.backup(config_path, bench_name=bench.name)
                config["db_host"] = "mariadb"
                config_path.write_text(json.dumps(config, indent=1, sort_keys=True))
                changed = True
        return changed

    def _rewrite_telemetry_table(self, bench: MigrationBench) -> bool:
        """Carry NewRelic settings to `[telemetry.newrelic]`, from BOTH earlier shapes.

        Two hops land here because the first one never shipped with a migration:

        * v0.19.0 wrote flat `newrelic_enabled` / `newrelic_license_key` at the top level;
        * the 0.20/0.21 development line moved them to `[monitoring.newrelic]` and the reader
          stopped understanding the flat keys, WITHOUT a migration to carry them over.

        `BenchConfig` is `extra="allow"`, so the orphaned keys neither raised nor were read:
        every bench that was reporting to NewRelic silently stopped on upgrade, with no error
        and nothing in the output. That is the same failure this cycle fixed in
        `fm telemetry disable` (an omitted env var read as a retained one), arriving by the
        upgrade path instead.

        The old keys are DELETED, not left beside the new table: writers merge and never strip
        (`save_dict_to_file`), so anything left here would outlive every later write and the
        next reader to grow a compatibility branch would find two disagreeing sources.
        """
        config_path = bench.path / "bench_config.toml"
        if not config_path.is_file():
            return False

        doc = tomlkit.parse(config_path.read_text())

        legacy_table = doc.get("monitoring")
        flat_enabled = doc.get("newrelic_enabled")
        flat_key = doc.get("newrelic_license_key")
        if legacy_table is None and flat_enabled is None and flat_key is None:
            return False

        self.backup_manager.backup(config_path, bench_name=bench.name)

        telemetry = doc.get("telemetry")
        if telemetry is None:
            telemetry = tomlkit.table()
            doc["telemetry"] = telemetry

        if legacy_table is not None:
            # Every provider sub-table moves, not just newrelic: `[monitoring]` is extra="allow",
            # so a hand-written sibling is a thing a user could already have.
            for provider, table in dict(cast("dict[str, Any]", legacy_table)).items():
                if provider not in telemetry:
                    telemetry[provider] = table
            del doc["monitoring"]

        if (flat_enabled is not None or flat_key is not None) and "newrelic" not in telemetry:
            newrelic = tomlkit.table()
            newrelic["enabled"] = bool(flat_enabled) if flat_enabled is not None else False
            if flat_key is not None:
                newrelic["license_key"] = flat_key
            telemetry["newrelic"] = newrelic

        for stale in ("newrelic_enabled", "newrelic_license_key"):
            if stale in doc:
                del doc[stale]

        toml_document.save(config_path, doc)
        # `\[` is required: `output.print` interprets rich markup, and `[telemetry.newrelic]` is
        # shaped exactly like a style tag, so an unescaped one renders as NOTHING -- this line
        # printed "Moved audit's NewRelic settings to " on a real bench before the escape.
        self.output.print(f"Moved {bench.name}'s NewRelic settings to \\[telemetry.newrelic]")
        return True

    def _start_bench(self, bench: MigrationBench):
        """Bring a previously-running bench back on the renamed networks. Best-effort
        with a loud report: the cutover itself is complete at this point, and failing
        the whole migration because one bench would not start would roll back every
        other bench's working state."""
        try:
            with spinner(self.output, f"Starting {bench.name}"):  # type: ignore[arg-type]
                for compose_name in (
                    "docker-compose.yml",
                    "docker-compose.workers.yml",
                    "docker-compose.admin-tools.yml",
                ):
                    compose_path = bench.path / compose_name
                    if not compose_path.exists():
                        continue
                    client = DockerClient(compose_file_path=compose_path)
                    assert client.compose is not None
                    client.compose.up(services=[], detach=True, pull="never", stream=False)
        except DockerException as e:
            self.output.warning(
                f"{bench.name} was renamed but did not come back up ({e}); start it with: fm start {bench.name}"
            )

    def _undo_service_rename_services(self):
        """Put the RUNNING state back after the framework restored the backed-up files.

        The services compose is additionally restored EXPLICITLY (the same defense
        `_undo_admin_tools_and_config_services` carries): the framework's restore loop is not
        something this rollback can afford to assume ran first. The macOS data needs nothing --
        the old volume was never touched. On a legacy install the restored compose declares the
        networks `external`, so nothing would recreate them on up: they are recreated here from
        the subnets captured before deletion.
        """
        try:
            self.services_manager.compose.down(
                remove_orphans=True, volumes=False, timeout=DOCKER_COMPOSE_DOWN_TIMEOUT_SECONDS, stream=False
            )
        except DockerException:
            self.logger.exception("[rename-rollback] down of the renamed stack failed")
        for network in NETWORK_NAME_RENAMES.values():
            with contextlib.suppress(Exception):
                run_command_with_exit_code(["docker", "network", "rm", network], stream=False)

        compose_path = self.services_manager.compose_file_manager.compose_path
        for backup in self.backup_manager.backups:
            if backup.src == compose_path:
                self.backup_manager.restore(backup, force=True)
                self.output.print("Restored the services compose file")
                break

        self._recreate_external_networks(compose_path)

        try:
            self.services_manager.compose.up(services=[], detach=True, pull="missing", stream=False)
        except DockerException:
            self.output.warning(
                "Could not start the restored global stack; run 'fm services start all' after checking docker."
            )
        for bench_name in self._was_running:
            bench_path = CLI_BENCHES_DIRECTORY / bench_name
            if (bench_path / "docker-compose.yml").exists():
                self._start_bench(MigrationBench(bench_name, bench_path, output=self.output))

    def _recreate_external_networks(self, compose_path) -> None:
        """Recreate networks a restored legacy compose expects to pre-exist."""
        try:
            yaml = YAML()
            networks = (yaml.load(compose_path.read_text()) or {}).get("networks") or {}
        except Exception:
            return
        for key, network in networks.items():
            if not (isinstance(network, dict) and network.get("external") and network.get("name")):
                continue
            cmd = ["docker", "network", "create"]
            subnet = self._old_subnets.get(NETWORK_KEY_RENAMES.get(key, key))
            if subnet:
                cmd += ["--subnet", subnet]
            with contextlib.suppress(Exception):
                run_command_with_exit_code([*cmd, network["name"]], stream=False)
