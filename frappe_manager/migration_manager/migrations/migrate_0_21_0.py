"""v0.21.0: rename the global services to their engine names, everywhere.

The compose service `global-db` becomes `mariadb` and `global-nginx-proxy` becomes
`nginx-proxy`; the shared networks and the macOS data volume drop the `global` token the
same way. Engine names are the point: a postgres or traefik sibling can then arrive as a
new service instead of forcing another rename. Nothing legacy survives -- fm after this
migration only knows the new names.

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
"""

import contextlib
import platform
from typing import Any, cast

import tomlkit
from ruamel.yaml import YAML

from frappe_manager import CLI_BENCHES_DIRECTORY
from frappe_manager.docker import DockerClient, DockerException
from frappe_manager.migration_manager.migration_base import MigrationBase
from frappe_manager.migration_manager.migration_constants import DOCKER_COMPOSE_DOWN_TIMEOUT_SECONDS
from frappe_manager.migration_manager.migration_exceptions import MigrationExceptionInBench
from frappe_manager.migration_manager.migration_helpers import MigrationBench
from frappe_manager.migration_manager.version import Version
from frappe_manager.output_manager.context_managers import spinner
from frappe_manager.services_manager.database_service_manager import DatabaseServerServiceInfo, MariaDBManager
from frappe_manager.utils import toml_document
from frappe_manager.utils.docker import run_command_with_exit_code
from frappe_manager.utils.site import host_bench_dir

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


class MigrationV0210(MigrationBase):
    version = Version("0.21.0")

    def init(self):
        super().init()
        # Benches that were up before the cutover; only these are brought back.
        self._was_running: set[str] = set()
        # Daemon-truth subnets of the OLD networks, captured before anything is deleted;
        # keyed by the NEW compose network key. The rollback needs them too.
        self._old_subnets: dict[str, str | None] = {}

    # ------------------------------------------------------------- services

    def migrate_services(self):
        compose_file_manager = self.services_manager.compose_file_manager
        if not compose_file_manager.exists():
            self.logger.debug("[rename] services compose not found, skipping")
            return

        services = compose_file_manager.yml.get("services") or {}
        if "mariadb" in services and "global-db" not in services:
            # Dev builds re-run this migration (0.21.0.dev0 sorts below 0.21.0); an
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

    # ------------------------------------------------------------- benches

    def _migrate_all_benches_files(self):
        for bench_name, compose_path in self.benches_manager.get_all_benches().items():
            bench = MigrationBench(bench_name, compose_path.parent, output=self.output)
            self._rewrite_bench_files(bench)
            if bench_name in self._was_running:
                self._start_bench(bench)

    def migrate_bench(self, bench: MigrationBench):
        # Idempotent re-application: normally `migrate_services` already rewrote this
        # bench; this run covers a bench restored from backup or migrated later through
        # the bench gate, and stamps its version through the executor as usual.
        self._rewrite_bench_files(bench)

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

    # ------------------------------------------------------------- rollback

    def undo_services_migrate(self):
        """Put the RUNNING state back after the framework restored the backed-up files.

        The services compose is additionally restored EXPLICITLY (same defense v0.20.0
        carries): the framework's restore loop is not something this rollback can afford
        to assume ran first. The macOS data needs nothing -- the old volume was never
        touched. On a legacy install the restored compose declares the networks
        `external`, so nothing would recreate them on up: they are recreated here from
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
