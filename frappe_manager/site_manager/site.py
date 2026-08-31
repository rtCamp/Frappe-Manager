import json
import shutil
import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any, cast

from frappe_manager import (
    CLI_BENCH_CONFIG_FILE_NAME,
    CLI_BENCHES_DIRECTORY,
    SiteServicesEnum,
)
from frappe_manager.docker import DOCKER_LINE_NOISE, ComposeFile, DockerClient, DockerException
from frappe_manager.logger import get_logger, set_context
from frappe_manager.migration_manager.backup_manager import BackupManager
from frappe_manager.output_manager import OutputHandler
from frappe_manager.output_manager.rich_output import RichOutputHandler
from frappe_manager.services_manager.services import ServicesManager
from frappe_manager.site_manager.bench_config import (
    AuthConfig,
    BenchConfig,
    DatabaseConfig,
    FMBenchEnvType,
    resolve_primary_site,
)
from frappe_manager.site_manager.exceptions import (
    BenchException,
    BenchRemoveDirectoryError,
    BenchServiceNotRunning,
)
from frappe_manager.site_manager.modules.bench_admin_tools import BenchAdminTools
from frappe_manager.site_manager.modules.bench_app import BenchAppManager
from frappe_manager.site_manager.modules.bench_database import BenchDatabase
from frappe_manager.site_manager.modules.bench_devtools import BenchDevTools
from frappe_manager.site_manager.modules.bench_docker import BenchDockerOps
from frappe_manager.site_manager.modules.bench_info import BenchInfo
from frappe_manager.site_manager.modules.bench_orchestrator import BenchOrchestrator
from frappe_manager.site_manager.modules.bench_site import BenchSiteManager
from frappe_manager.site_manager.modules.bench_ssl import BenchSSL
from frappe_manager.site_manager.modules.bench_supervisor import BenchSupervisor
from frappe_manager.site_manager.modules.bench_workers import BenchWorkerCoordinator, BenchWorkers
from frappe_manager.site_manager.modules.upload_limit_manager import UploadLimitManager
from frappe_manager.ssl_manager.certificate import SSLCertificate
from frappe_manager.ssl_manager.certificate_link_manager import CertificateLinkManager
from frappe_manager.ssl_manager.nginx_controller import NginxController
from frappe_manager.ssl_manager.proxy_storage import ProxyStoragePaths
from frappe_manager.ssl_manager.service_factory import create_certificate_service
from frappe_manager.ssl_manager.ssl_certificate_manager import SSLCertificateManager
from frappe_manager.ssl_manager.storage_config import SSLStorageConfig
from frappe_manager.utils.helpers import (
    save_dict_to_file,
)
from frappe_manager.utils.site import domain_level


def orphaned_database_error(bench: "Bench", cause: Exception) -> BenchException:
    """The refusal raised when a bench's schema could not be dropped.

    Removing the bench directory after a failed drop destroys `bench_config.toml` and
    `sites/<site>/site_config.json`, which hold the only record of the schema name and its
    password, so the schema left behind in global-db can afterwards only be found by hand.
    The directory stays put and the operator is handed the statements that finish the job.
    """
    try:
        db_info = bench.get_db_connection_info()
    except Exception:
        db_info = {}

    db_name = db_info.get("name")
    db_user = db_info.get("user")

    lines = [
        f"Database deletion failed: {cause}",
        "",
        f"The bench directory was kept at {bench.path}: it carries the only record of the schema,",
        "so removing it now would leave a database in global-db that nothing points at.",
        "",
    ]

    if db_name and db_user:
        lines += [
            "Drop it on global-db, then delete the bench again:",
            f"  DROP DATABASE IF EXISTS `{db_name}`;",
            f"  DROP USER IF EXISTS '{db_user}'@'%';",
            f"  fm delete {bench.name} --yes --no-delete-db-from-global-db",
        ]
    else:
        # `bench.path` is the BENCH directory; the `sites/<X>/` segment inside it is the SITE. One
        # statement, both identities, which is the shape this whole split exists to separate.
        site_config = bench.path / "workspace" / "frappe-bench" / "sites" / bench.site_name / "site_config.json"
        lines += [
            f"The schema name could not be read from {site_config}. Find it there, or in global-db,",
            f"drop it, then run: fm delete {bench.name} --yes --no-delete-db-from-global-db",
        ]

    return BenchException(bench.name, message="\n".join(lines))


class Bench:
    def __init__(
        self,
        path: Path,
        name: str,
        bench_config: BenchConfig,
        compose_file_manager: ComposeFile,
        docker_client: DockerClient,
        services: ServicesManager,
        workers_check: bool = True,
        admin_tools_check: bool = True,
        verbose: bool = False,
        output_handler: OutputHandler | None = None,
    ) -> None:
        self.path = path
        self.name = name
        self.output = output_handler or RichOutputHandler()
        self.services = services
        self.backup_path = self.path / "backups"
        self.bench_config: BenchConfig = bench_config
        self.logger = get_logger(component="bench")

        self.compose_file_manager = compose_file_manager
        self.docker_client = docker_client

        # Initialize specialized modules
        self.docker_ops = BenchDockerOps(
            docker_client=docker_client,
            compose_file_manager=compose_file_manager,
            config=bench_config,
            path=path,
            output_handler=self.output,
        )
        self.supervisor = BenchSupervisor(
            docker_client=docker_client,
            config=bench_config,
            bench_name=name,
            output_handler=self.output,
        )

        # Initialize local nginx proxy components
        self.bench_proxy_storage = ProxyStoragePaths("nginx", self.compose_file_manager)
        self.bench_nginx_controller = NginxController("nginx", self.compose_file_manager, self.docker_client)

        # For backward compatibility with admin_tools
        # Create a simple proxy manager object with required attributes
        self.proxy_manager = type(
            "ProxyManager",
            (),
            {
                "dirs": self.bench_proxy_storage.dirs,
                "restart": self.bench_nginx_controller.restart,
                "reload": self.bench_nginx_controller.reload,
            },
        )()

        self.admin_tools = BenchAdminTools(self, self.proxy_manager, verbose=verbose, output_handler=self.output)

        # Get global nginx-proxy storage config from services
        global_proxy_storage = services.proxy_storage
        webroot_dir = self.bench_proxy_storage.dirs.html.host

        ssl_storage_config = SSLStorageConfig(
            ssl_dir=global_proxy_storage.dirs.ssl.host,
            ssl_dir_container=global_proxy_storage.dirs.ssl.container,
            certs_dir=global_proxy_storage.dirs.certs.host,
            certs_dir_container=global_proxy_storage.dirs.certs.container,
            vhostd_dir=global_proxy_storage.dirs.vhostd.host,
            webroot_dir=webroot_dir,
        )

        link_manager = CertificateLinkManager(ssl_storage_config)

        def certificate_service_factory(cert, storage_cfg, output_handler):
            # bench_config is what makes this bench's `[ssl.dns_providers]` reachable at
            # issuance and renewal; the standalone factory in commands/ssl/external_helpers.py has
            # no bench and correctly passes nothing.
            return create_certificate_service(cert, storage_cfg, output_handler, self.bench_config)

        self.certificate_manager = SSLCertificateManager(
            certificates=self.bench_config.ssl_certificates,
            service_factory=certificate_service_factory,
            link_manager=link_manager,
            nginx_controller=services.nginx_controller,
            storage_config=ssl_storage_config,
            config_save_callback=self.save_bench_config,
            output_handler=self.output,
        )

        self.ssl = BenchSSL(
            certificate_manager=self.certificate_manager,
            bench_name=name,
            is_service_running_fn=self._is_service_running,
        )

        self.devtools = BenchDevTools(
            docker_client=docker_client,
            compose_file_manager=compose_file_manager,
            bench_path=path,
            bench_name=name,
            is_running_fn=lambda: self.running,
            output_handler=self.output,
        )

        self.database = BenchDatabase(
            bench_name=name,
            bench_path=path,
            bench_config=bench_config,
            services=services,
            set_common_bench_config_fn=self.set_common_bench_config,
            output_handler=self.output,
        )

        self.site_manager = BenchSiteManager(
            bench_name=name,
            bench_path=path,
            docker_client=docker_client,
            bench_config=bench_config,
            services=services,
            compose_file_manager=compose_file_manager,
            output_handler=self.output,
        )

        self.app_manager = BenchAppManager(
            bench_name=name,
            bench_path=path,
            docker_client=docker_client,
            bench_config=bench_config,
            output_handler=self.output,
        )

        self.workers = BenchWorkers(self, not verbose, output_handler=self.output)

        self.info_display = BenchInfo(
            bench_name=name,
            bench_path=path,
            bench_config=bench_config,
            services=services,
            workers=self.workers,
            admin_tools=self.admin_tools,
            certificate_manager=self.certificate_manager,
            get_db_connection_info_fn=self.get_db_connection_info,
            has_certificate_fn=lambda: self.has_certificate(),
            is_running_fn=lambda: self.running,
            get_services_running_status_fn=self._get_services_running_status,
            docker_client=docker_client,
            output_handler=self.output,
        )

        self.worker_coordinator = BenchWorkerCoordinator(
            bench_name=name,
            workers=self.workers,
            supervisor=self.supervisor,
            bench_path=self.path,
            restart_supervisor_service_fn=self.restart_supervisor_service,
            is_running_fn=lambda: self.running,
            docker_ops=self.docker_ops,
            output_handler=self.output,
        )

        # For complex workflows
        self.orchestrator = BenchOrchestrator(bench=self, output_handler=self.output)

        if workers_check:
            self.ensure_workers_running_if_available()

        if admin_tools_check:
            self.ensure_admin_tools_running_if_available()

    @classmethod
    def get_object(
        cls,
        bench_name: str,
        services: ServicesManager,
        benches_path: Path = CLI_BENCHES_DIRECTORY,
        bench_config_file_name: str = CLI_BENCH_CONFIG_FILE_NAME,
        workers_check: bool = False,
        admin_tools_check: bool = False,
        verbose: bool = False,
        output_handler: OutputHandler | None = None,
    ) -> "Bench":
        # Same rule as the CLI's resolver: a bench name is taken as typed, and the `.localhost`
        # form is only a fallback for benches created before the bench name and the site name came
        # apart. Appending unconditionally here overrode whatever the resolver decided, so a bench
        # genuinely called `shop` could not be opened at all.
        bench_path = benches_path / bench_name
        if not bench_path.exists() and domain_level(bench_name) == 0:
            legacy_name = bench_name + ".localhost"
            legacy_path = benches_path / legacy_name
            if legacy_path.exists():
                bench_name, bench_path = legacy_name, legacy_path

        bench_config_path: Path = bench_path / bench_config_file_name

        if not bench_path.exists():
            from frappe_manager.site_manager.exceptions import BenchNotFoundError

            raise BenchNotFoundError(bench_name, bench_path)

        compose_file_manager = ComposeFile(bench_path / "docker-compose.yml")
        docker_client = DockerClient(compose_file_path=bench_path / "docker-compose.yml", output=output_handler)

        bench_config: BenchConfig = BenchConfig.import_from_toml(bench_config_path)

        # Ambient logging context: every record from here on is bench-tagged.
        set_context(bench=bench_name)

        parms: dict[str, Any] = {
            "name": bench_name,
            "path": bench_path,
            "bench_config": bench_config,
            "compose_file_manager": compose_file_manager,
            "docker_client": docker_client,
            "services": services,
            "workers_check": workers_check,
            "admin_tools_check": admin_tools_check,
        }

        if output_handler is not None:
            parms["output_handler"] = output_handler

        return cls(**parms)

    def _is_service_running(self, service: str) -> bool:
        """Check if a specific service is running."""
        return self.docker_ops._is_service_running(service)

    @property
    def running(self) -> bool:
        """Check if all bench services are running."""
        return self.docker_ops.is_running()

    @property
    def site_name(self) -> str:
        """The Frappe site this bench acts on: the schema, the `sites/<name>/` directory, the
        `--site` argument.

        Delegates to `BenchConfig.primary_site`, which reads `[sites]`. It used to carry its own
        copy of that rule, and the copies drifted the moment the config learned to recognise the
        site named after the bench in its FQDN form: a two-site bench called `shop` resolved to
        `shop.localhost` there and refused here.

        `self.name` is the BENCH: its directory under `~/frappe/sites/`, its compose project, the
        address a user types. Reading the two from different places is what lets them differ.

        A bench with no config falls back to its own name. Not a compatibility branch for an old
        file shape: `Bench` objects are built mid-create, before the config is assembled, and during
        a migration part-way through writing the table.
        """
        config = getattr(self, "bench_config", None)
        sites = getattr(config, "sites", None) if config is not None else None
        resolved = resolve_primary_site(self.name, sites)
        if resolved is not None:
            return resolved
        # A bench-scoped command cannot proceed without knowing its site, and guessing would
        # silently target someone else's schema. The address form is what resolves it.
        known = ", ".join(sorted(sites or {}))
        raise BenchException(
            self.name,
            message=f"bench_config.toml records {len(sites or {})} sites ({known}) and none is named after "
            f"the bench, so fm cannot tell which one this command means. Name the site explicitly as "
            f"{self.name}/<site>, or repair \\[sites] so one entry matches the bench.",
        )

    @property
    def primary_domain(self) -> str:
        """The hostname this bench's primary site is served on: nginx, certificates, `Host:`.

        The SITE's name, not the bench's. A site is a Frappe schema addressed by hostname, so the
        two are the same string, and a bench called `shop` serves `shop.localhost`: returning
        `self.name` here would put an unroutable host into `VIRTUAL_HOST`, a certificate subject
        nothing resolves, and a `Host:` header the readiness probe cannot match.
        """
        return self.site_name

    @property
    def domains(self) -> list[str]:
        """Every hostname this bench serves, primary first, then aliases."""
        return [self.primary_domain, *(self.bench_config.alias_domains or [])]

    def _get_services_running_status(self) -> dict:
        """Get the running status of all services."""
        return self.docker_ops.get_services_running_status()

    def sync_bench_config_configuration(self):
        extra = {"operation": "config_sync_bench_config", "bench_name": self.name}
        self.logger.debug(f"Syncing bench config configuration: {self.name}", extra_fields=extra)
        try:
            # set developer_mode based on config
            self.set_common_bench_config({"developer_mode": self.bench_config.developer_mode})

            # ssl
            certificate_updated = self.update_certificate(
                self.bench_config.get_primary_certificate(),
                raise_error=False,
            )
            if certificate_updated:
                self.output.print("Certificate Updated")

            # admin tools
            if self.bench_config.admin_tools:
                if not self.admin_tools.compose_file_manager.compose_path.exists():
                    self.sync_admin_tools_compose()
                else:
                    self.admin_tools.enable(force_configure=True)
                self.output.print("Enabled Admin-tools")

            elif not self.admin_tools.compose_file_manager.compose_path.exists():
                self.output.print("Admin tools is already disabled")
            else:
                self.admin_tools.disable()
                self.output.print("Disabled Admin-tools")

            self.output.change_head("Restarting frappe server")
            self.restart_supervisor_service("frappe")
            self.output.print("Restarted frappe server")
            self.logger.info(f"Bench config synchronized: {self.name}", extra_fields=extra)
        except Exception as e:
            extra["error"] = str(e)
            self.logger.exception(f"Failed to sync bench config: {self.name}", extra_fields=extra)
            raise

    def save_bench_config(self, print_message: bool = True):
        extra = {"operation": "config_save_bench_config", "bench_name": self.name, "print_message": print_message}
        self.logger.debug(f"Saving bench config: {self.name}", extra_fields=extra)
        try:
            if print_message:
                self.output.change_head("Saving bench config changes")
            self.bench_config.export_to_toml(self.bench_config.root_path)
            if print_message:
                self.output.print("Saved bench config")
            self.logger.info(f"Bench config saved: {self.name}", extra_fields=extra)
        except Exception as e:
            extra["error"] = str(e)
            self.logger.exception(f"Failed to save bench config: {self.name}", extra_fields=extra)
            raise

    @property
    def exists(self):
        return self.path.exists()

    def create(self, bench_only: bool = False):
        """
        Create this bench.

        Args:
            bench_only: If True, build the bench and stop: no site is created in it. Sites are added
                afterwards with `fm create BENCH/SITE`.

        Returns:
            None
        """
        extra = {"operation": "bench_create", "bench_name": self.name, "bench_only": bench_only}
        self.logger.debug(f"Starting bench creation: {self.name}", extra_fields=extra)
        try:
            self.orchestrator.create_bench(bench_only)
            self.logger.info(f"Bench created successfully: {self.name}", extra_fields=extra)
        except Exception as e:
            extra["error"] = str(e)
            self.logger.exception(f"Failed to create bench: {self.name}", extra_fields=extra)
            raise

    def set_common_bench_config(self, config: dict):
        """
        Sets the values in the common_site_config.json file.

        Args:
            config (dict): A dictionary containing the key-value pairs
        """
        extra = {"operation": "config_set_common", "bench_name": self.name, "config_keys": list(config.keys())}
        self.logger.debug(f"Setting common bench configuration: {self.name}", extra_fields=extra)
        try:
            common_bench_config_path = self.path / "workspace/frappe-bench/sites/common_site_config.json"
            if not common_bench_config_path.exists():
                raise BenchException(self.name, message=f"File not found {common_bench_config_path.name}.")

            save_dict_to_file(config, common_bench_config_path)
            self.logger.info(f"Common bench configuration set: {self.name}", extra_fields=extra)
        except Exception as e:
            extra["error"] = str(e)
            self.logger.exception(f"Failed to set common bench configuration: {self.name}", extra_fields=extra)
            raise

    def set_bench_site_config(self, config: dict):
        """
        Sets the values in the bench's site site_config.json file.

        Args:
            config (dict): A dictionary containing the key-value pairs
        """
        site_config_path = self.path / "workspace/frappe-bench/sites" / self.site_name / "site_config.json"
        if not site_config_path.exists():
            raise BenchException(self.name, message=f"File not found {site_config_path.name}.")
        save_dict_to_file(config, site_config_path)

    def create_bench_site_config(self, config: dict):
        """Create `sites/<site>/site_config.json` from the host, before anything connects.

        The external flow must write this file BEFORE `new-site` or any provisioning runs:
        TLS has no CLI flag and this is the only per-site config source Frappe reads. Frappe's
        `make_site_config` writes the file only when it does not exist and `make_conf` re-inits
        the site afterwards, so a file fm wrote first survives untouched and is what the rest of
        `new-site` reads. Unlike `set_bench_site_config` this creates the directory and the file.
        """
        site_dir = self.path / "workspace/frappe-bench/sites" / self.site_name
        site_dir.mkdir(parents=True, exist_ok=True)
        site_config_path = site_dir / "site_config.json"
        # save_dict_to_file merges, so it reads the file before writing and cannot create one.
        # Every other caller edits a file Frappe already wrote; this is the first write.
        if not site_config_path.exists():
            site_config_path.write_text("{}")
        save_dict_to_file(config, site_config_path)

    def get_common_bench_config(self):
        return self.info_display.get_common_config()

    def get_bench_site_config(self):
        return self.info_display.get_site_config()

    def generate_compose(self, inputs: dict) -> None:
        """
        Generates the compose file for the site based on the given inputs.

        Args:
            inputs (dict): A dictionary containing the inputs for generating the compose file.

        Returns:
            None
        """
        self.docker_ops.generate_compose(inputs)
        # Compose generation is the event where fm materializes network
        # topology into bench config (extra_hosts carries the proxy IP from
        # the same frontend network); refresh the fm-managed bench nginx confs
        # (real client IP restoration) in the same breath.
        self.ensure_fm_nginx_confs()

    def sync_bench_common_site_config(self):
        """
        Syncs `common_site_config.json` with this bench's redis wiring and container prefix.

        The database endpoint is not written here: it is per site and lives in
        `sites/<site>/site_config.json`, while redis is per bench and stays in the common file.
        """
        self.database.sync_common_site_config()

    def create_compose_dirs(self, copy_runtimes: bool = True) -> bool:
        return self.docker_ops.create_compose_dirs(copy_runtimes=copy_runtimes)

    def ensure_nginx_conf_seeded(self) -> None:
        """Re-seed the bench's nginx config from the image when the base config is missing.

        `create_compose_dirs` runs only at bench creation, so a bench created while the seeding
        was guarded on `conf/` existing (rather than on the `nginx.conf` marker file) is stuck
        with fm's two overlay files and none of the image's base config: nginx exits with
        `/etc/nginx/nginx.conf: No such file or directory` and the site serves 503 forever,
        because nothing a user runs re-seeds it. Do it here instead, on the way into a start,
        while the bench directory exists but no container has come up yet.

        The marker check keeps a healthy bench completely untouched, and the seeding itself only
        fills gaps, so a hand-tuned conf is never overwritten. Runtimes are deliberately not
        re-copied: `.uv`/`.fnm` are materialized at creation and image-runtime benches keep them
        inside the image on purpose, so `copy_runtimes=True` would mean a `docker run` and a
        multi-hundred-megabyte copy out of the frappe image on every start.

        Healing is best effort: a bench that cannot be repaired is still started, but the failure
        is surfaced as a warning rather than swallowed.
        """
        if (self.path / "configs" / "nginx" / "conf" / "nginx.conf").exists():
            return

        extra = {"operation": "nginx_conf_reseed", "bench_name": self.name}
        self.logger.debug(f"Re-seeding missing nginx conf for bench: {self.name}", extra_fields=extra)
        try:
            self.create_compose_dirs(copy_runtimes=False)
            self.logger.info(f"Re-seeded nginx conf for bench: {self.name}", extra_fields=extra)
        except Exception as e:
            extra["error"] = str(e)
            self.logger.warning(f"Failed to re-seed nginx conf for bench: {self.name}", extra_fields=extra)
            self.output.warning(f"Could not repair bench nginx config: {e!s}")

    def start(
        self,
        force: bool = False,
        reconfigure_workers: bool = False,
        include_default_workers=False,
        include_custom_workers=False,
        reconfigure_supervisor: bool = False,
        reconfigure_common_site_config: bool = False,
        sync_dev_packages: bool = False,
    ):
        """
        Starts the bench with various configuration options.
        """
        extra = {
            "operation": "bench_start",
            "bench_name": self.name,
            "force": force,
            "reconfigure_workers": reconfigure_workers,
        }
        self.logger.debug(f"Starting bench: {self.name}", extra_fields=extra)
        try:
            self.ensure_nginx_conf_seeded()
            # The fm-managed overlay (real-ip, auth) is written by generate_compose, so a
            # bench whose compose has not been regenerated since that landed never receives
            # it. Without real-ip.conf every request reaches the app carrying the global
            # proxy's own address, so frappe's request_ip, its per-IP rate limiting and the
            # Activity Log see one IP for the entire internet. Writing before start_bench is
            # deliberate: nginx must find the include when it boots. Best effort like the
            # seeding above, and surfaced rather than swallowed.
            try:
                self.ensure_fm_nginx_confs()
                # The proxy vhost entry is the other half, and it is the binding one: a bench with
                # no entry gets the proxy's 1M default and answers 413 however permissive its own
                # nginx conf is. Existing benches have no entry at all, so this is what heals them
                # without a migration. Reload only when something actually changed, because the
                # proxy is shared by every bench.
                if self.apply_upload_limit() and self.services.is_service_running("global-nginx-proxy"):
                    self.services.nginx_controller.reload()
            except Exception as e:
                self.logger.warning(f"Failed to refresh bench nginx overlay: {self.name}", extra_fields=extra)
                self.output.warning(f"Could not refresh bench nginx config: {e!s}")
            self.orchestrator.start_bench(
                force=force,
                reconfigure_workers=reconfigure_workers,
                include_default_workers=include_default_workers,
                include_custom_workers=include_custom_workers,
                reconfigure_supervisor=reconfigure_supervisor,
                reconfigure_common_site_config=reconfigure_common_site_config,
                sync_dev_packages=sync_dev_packages,
            )
            self.logger.info(f"Bench started successfully: {self.name}", extra_fields=extra)
        except Exception as e:
            extra["error"] = str(e)
            self.logger.exception(f"Failed to start bench: {self.name}", extra_fields=extra)
            raise

    def frappe_logs_till_start(self):
        """
        Retrieves and prints the logs of the 'frappe' service until site supervisor starts.

        Args:
            status_msg (str, optional): Custom status message to display. Defaults to None.
        """
        return self.docker_ops.frappe_logs_till_start()

    def stop(self):
        """
        Stop the site by stopping the containers.

        Returns:
            bool: True if the site is successfully stopped, False otherwise.
        """
        extra = {"operation": "bench_stop", "bench_name": self.name}
        self.logger.debug(f"Stopping bench: {self.name}", extra_fields=extra)
        try:
            self.docker_ops.stop(timeout=10)

            if self.workers.compose_file_manager.exists():
                self.output.change_head("Stopping bench workers services")
                self.workers.docker_client.compose.stop(services=[], timeout=10)
                self.output.print("Stopped bench workers services")

            # stop admin_tools if exists
            if self.admin_tools.compose_file_manager.exists():
                self.output.change_head("Stopping bench admin tools services")
                self.admin_tools.stop()
                self.output.print("Stopped bench admin tools services")

            self.logger.info(f"Bench stopped successfully: {self.name}", extra_fields=extra)
        except Exception as e:
            extra["error"] = str(e)
            self.logger.exception(f"Failed to stop bench: {self.name}", extra_fields=extra)
            raise

    def remove_containers_and_dirs(self):
        """
        Removes the site by stopping and removing the containers associated with it,
        and deleting the site directory.

        Returns:
            bool: True if the site is successfully removed, False otherwise.
        """
        # TODO handle low level errors like read only, write only, etc.
        if self.compose_file_manager.exists():
            self.output.change_head("Removing bench containers")
            self.docker_ops.remove_containers(remove_volumes=True, timeout=5)
            self.output.print("Removed bench containers")
        else:
            self.output.warning("Bench compose file not found. Skipping containers removal.")

        if self.workers.compose_file_manager.exists():
            self.output.change_head("Removing bench workers containers")
            output = self.workers.docker_client.compose.down(remove_orphans=True, volumes=True, timeout=5, stream=True)
            self.output.live_lines(
                cast("Iterator[tuple[str, bytes]]", output), padding=(0, 0, 0, 2), line_filters=DOCKER_LINE_NOISE
            )
            self.output.print("Removed bench workers containers")
        else:
            self.output.warning("Bench workers compose file not found. Skipping containers removal.")

        if self.admin_tools.compose_file_manager.exists():
            self.output.change_head("Removing bench admin tools containers")
            # down_service equivalent: stop + remove containers + volumes
            try:
                self.admin_tools.docker_client.compose.down(remove_orphans=True, volumes=True, timeout=5, stream=True)
            except Exception:
                pass  # Best effort cleanup
            self.output.print("Removed bench admin tools containers")
        else:
            self.output.warning("Bench admin tools compose file not found. Skipping containers removal.")

        self.output.change_head("Removing all bench files and directories")
        try:
            shutil.rmtree(self.path)
        except PermissionError:
            try:
                images = self.compose_file_manager.get_all_images()
                if "frappe" in images:
                    frappe_image = images["frappe"]
                    frappe_image = f"{frappe_image['name']}:{frappe_image['tag']}"
                    self.docker_client.run(
                        image=frappe_image,
                        entrypoint="/bin/sh",
                        command="-c 'chown -R frappe:frappe .'",
                        volume=[f"{self.path}/workspace:/workspace"],
                        stream=False,
                    )
                    shutil.rmtree(self.path)
            except Exception:
                raise BenchRemoveDirectoryError(self.name, self.path)

        self.output.print("Removed all bench files and directories")

    def is_bench_created(self, retry=60, interval=1) -> bool:
        curl_command = "curl -I --max-time {retry} --connect-timeout {retry} {headers} {url}"
        url = "http://localhost"
        headers = ""
        if self.bench_config.environment_type == FMBenchEnvType.prod:
            headers = f"-H 'Host: {self.primary_domain}'"

        check_command = curl_command.format(retry=retry, headers=headers, url=url)

        for _ in range(retry):
            try:
                # Execute curl command on frappe service
                result = self.docker_client.compose.exec(
                    service="frappe",
                    command=check_command,
                    stream=False,
                )
                for line in result.stdout:
                    if "HTTP/1.1 200 OK" in line:
                        return True
            except Exception:
                time.sleep(interval)
        return False

    def sync_workers_compose(
        self,
        force_recreate: bool = False,
        setup_supervisor: bool = True,
        include_default_workers: bool = True,
        include_custom_workers: bool = True,
        start: bool = True,
    ):
        extra = {
            "operation": "workers_sync_compose",
            "bench_name": self.name,
            "force_recreate": force_recreate,
            "setup_supervisor": setup_supervisor,
        }
        self.logger.debug(f"Syncing workers compose for bench: {self.name}", extra_fields=extra)
        try:
            self.worker_coordinator.sync_workers_compose(
                force_recreate=force_recreate,
                setup_supervisor=setup_supervisor,
                include_default_workers=include_default_workers,
                include_custom_workers=include_custom_workers,
                start=start,
            )
            self.logger.info(f"Workers compose synced for bench: {self.name}", extra_fields=extra)
        except Exception as e:
            extra["error"] = str(e)
            self.logger.exception(f"Failed to sync workers compose for bench: {self.name}", extra_fields=extra)
            raise

    def backup_restore_workers_supervisor(self, backup_manager: BackupManager):
        self.worker_coordinator.backup_restore_workers_supervisor(backup_manager)

    def backup_workers_supervisor_conf(self):
        return self.worker_coordinator.backup_workers_supervisor_conf()

    def regenerate_workers_supervisor_conf(self):
        self.worker_coordinator.regenerate_workers_supervisor_conf()

    def get_bench_apps(self):
        return self.info_display.get_bench_apps()

    # this can be plugable
    def get_db_connection_info(self):
        return self.database.get_connection_info()

    def create_certificate(self):
        extra = {"operation": "ssl_create_certificate", "bench_name": self.name}
        self.logger.debug(f"Creating SSL certificate: {self.name}", extra_fields=extra)
        try:
            self.ssl.create_individual_certificates()
            self.save_bench_config()
            self.logger.info(f"SSL certificate created successfully: {self.name}", extra_fields=extra)
        except Exception as e:
            extra["error"] = str(e)
            self.logger.exception(f"Failed to create SSL certificate: {self.name}", extra_fields=extra)
            raise

    def has_certificate(self):
        return self.ssl.has_certificate()

    def remove_certificate(self):
        """
        Remove ALL SSL certificates for this bench.

        This removes certificates for the primary domain and all alias domains,
        including their symlinks, vhost configs, and acme.sh configurations.
        Then clears the certificate list in bench_config.
        """
        extra = {"operation": "ssl_remove_certificate", "bench_name": self.name}
        self.logger.debug(f"Removing SSL certificate: {self.name}", extra_fields=extra)
        try:
            self.ssl.remove_all_certificates()
            # Clear all certificates from config
            self.bench_config.ssl_certificates = []
            self.save_bench_config()
            self.logger.info(f"SSL certificate removed successfully: {self.name}", extra_fields=extra)
        except Exception as e:
            extra["error"] = str(e)
            self.logger.exception(f"Failed to remove SSL certificate: {self.name}", extra_fields=extra)
            raise

    def update_certificate(self, certificate: SSLCertificate, raise_error: bool = True):
        extra = {"operation": "ssl_update_certificate", "bench_name": self.name, "raise_error": raise_error}
        self.logger.debug(f"Updating SSL certificate: {self.name}", extra_fields=extra)
        try:
            result = self.ssl.update_certificate(certificate, raise_error)
            if result:
                self.bench_config.set_primary_certificate(certificate)
            self.logger.info(f"SSL certificate updated: {self.name} (result: {result})", extra_fields=extra)
            return result
        except Exception as e:
            extra["error"] = str(e)
            self.logger.exception(f"Failed to update SSL certificate: {self.name}", extra_fields=extra)
            raise

    def renew_certificate(self):
        extra = {"operation": "ssl_renew_certificate", "bench_name": self.name}
        self.logger.debug(f"Renewing SSL certificate: {self.name}", extra_fields=extra)
        try:
            result = self.ssl.renew_certificate()
            self.logger.info(f"SSL certificate renewed: {self.name} (result: {result})", extra_fields=extra)
            return result
        except Exception as e:
            extra["error"] = str(e)
            self.logger.exception(f"Failed to renew SSL certificate: {self.name}", extra_fields=extra)
            raise

    def update_alias_domains(self, add_domains: list[str] | None = None, remove_domains: list[str] | None = None):
        """
        Update alias domains for the bench with atomic rollback support.

        Works independently of SSL status:
        - If SSL is active: regenerates certificate with updated domains
        - If SSL is inactive: updates config only

        Args:
            add_domains: List of domains to add as aliases
            remove_domains: List of domains to remove from aliases

        Raises:
            ValueError: If attempting to remove primary domain
            Exception: If certificate generation fails (config is rolled back)
        """
        self.orchestrator.update_alias_domains(add_domains, remove_domains)

    def info(self):
        """
        Retrieves and displays information about the bench.

        This method retrieves various information about the site, such as site URL, site root, database details,
        Frappe username and password, root database user and password, and more. It then formats and displays
        this information using the richprint library.
        """
        self.info_display.display_info()

    def _docker_ops_for_service(self, compose_service: str) -> BenchDockerOps:
        """Docker ops bound to the compose file that actually declares ``compose_service``.

        ``get_available_services`` advertises the union of the bench's three compose files,
        but ``self.docker_ops`` is bound to docker-compose.yml alone -- an admin-tools or
        worker service driven through it fails at the docker layer with `no such service`,
        even though it is running. Route it to the client that owns it instead.
        """
        for module, compose_name in (
            (self.admin_tools, "docker-compose.admin-tools.yml"),
            (self.workers, "docker-compose.workers.yml"),
        ):
            if not (self.path / compose_name).exists():
                continue
            if compose_service in module.compose_file_manager.get_services_list():
                return BenchDockerOps(
                    docker_client=module.docker_client,
                    compose_file_manager=module.compose_file_manager,
                    config=self.bench_config,
                    path=self.path,
                    output_handler=self.output,
                )
        return self.docker_ops

    def shell(
        self,
        compose_service: str,
        user: str | None,
        shell_path: str | None = None,
        use_run: bool = False,
        site: str | None = None,
    ):
        """
        Spawns a shell for the specified service and user.

        Args:
            service (str): The name of the service.
            user (str | None): The name of the user. If None, defaults to "frappe".
            shell_path (str | None): Path to shell executable (e.g., /bin/sh, /bin/bash).
            use_run (bool): Use 'docker compose run --rm' instead of 'docker compose exec'.
            site (str | None): Exported as FRAPPE_SITE so bare `bench` commands in the shell
                target this site instead of the bench-wide default.

        """
        return self._docker_ops_for_service(compose_service).shell(
            compose_service, user, shell_path=shell_path, use_run=use_run, site=site
        )

    def execute_command(
        self,
        compose_service: str,
        command: str,
        user: str | None = None,
        shell_path: str | None = None,
        use_run: bool = False,
        site: str | None = None,
    ) -> int:
        """
        Execute a single command in the specified service and return exit code.

        Args:
            compose_service: The name of the service
            command: The command to execute
            user: The name of the user (defaults to "frappe" for frappe service)
            shell_path: Path to shell executable (e.g., /bin/sh, /bin/bash)
            use_run: Use 'docker compose run --rm' instead of 'docker compose exec'
            site: Exported as FRAPPE_SITE so a bare `bench` command in `command` targets
                this site instead of the bench-wide default.

        Returns:
            Exit code of the executed command
        """
        return self._docker_ops_for_service(compose_service).execute_command(
            compose_service, command, user, shell_path=shell_path, use_run=use_run, site=site
        )

    def get_log_file_paths(self):
        return self.info_display.get_log_file_paths()

    def handle_frappe_server_file_logs(self, follow: bool):
        """Print (and optionally follow) the bench's host-side log files.

        Raw print() by design: this is a passthrough stream (pipe/grep-able).
        Non-follow prints each file sequentially; follow then polls all files
        in one loop -- draining every available line per cycle and sleeping
        only when idle (plain files are always select()-readable, so polling
        is the portable tail strategy; the drain-then-sleep shape keeps it
        from busy-looping).
        """
        log_file_paths = self.get_log_file_paths()
        if not log_file_paths:
            self.output.print("[fm.warn]No log files found.[/fm.warn]")
            return

        # Opened inside the try so a path that vanished between the existence check and
        # here closes the handles already opened instead of leaking them.
        files: list = []
        try:
            for path in log_file_paths:
                files.append(open(path))
            # Existing content first, file by file (no line-interleaving of
            # unrelated files, which zip_longest used to do).
            for handle in files:
                for line in handle:
                    print(line.rstrip("\n"))

            if not follow:
                return

            while True:
                idle = True
                for handle in files:
                    while line := handle.readline():
                        print(line.rstrip("\n"))
                        idle = False
                if idle:
                    time.sleep(0.5)
        except KeyboardInterrupt:
            pass
        finally:
            for handle in files:
                handle.close()

    def logs(self, follow: bool, service: str | None = None):
        """
        Display logs for the site or a specific service.

        Args:
            follow (bool): Whether to continuously follow the logs or not.
            service (str, optional): The name of the service to display logs for. If not provided, logs for the entire site will be displayed.
        """
        self.output.change_head("Showing logs")
        try:
            if not service:
                self.handle_frappe_server_file_logs(follow=follow)
            else:
                ops = self._docker_ops_for_service(service)
                if not ops._is_service_running(service):  # noqa: SLF001
                    # Raise, do not print-and-return: `fm logs BENCH --service nginx` printed this
                    # error and still exited 0, so a script could not tell empty logs from a
                    # container that was never up. `fm shell` already exits 1 for this exact
                    # condition, so the two disagreed.
                    raise BenchServiceNotRunning(self.name, service)
                ops.logs(services=[service], follow=follow)

        except KeyboardInterrupt:
            print("Detected CTRL+C. Exiting..")

    def attach_to_bench(self, user: str, extensions: list[str], workdir: str, debugger: bool = False) -> None:
        """
        Attaches to a running bench's container using Visual Studio Code Remote Containers extension.

        Args:
            user: Username to be used in the container
            extensions: List of VS Code extensions to install
            workdir: Working directory path inside container
            debugger: Whether to setup debugging configuration

        Raises:
            BenchNotRunning: If the bench container is not running
            BenchAttachTocontainerFailed: If attaching to container fails
        """
        return self.devtools.attach_to_bench(user, extensions, workdir, debugger)

    def remove_database_and_user(self):
        """
        This function is used to remove db and user of the site at self.name and path at self.path.
        """
        extra = {"operation": "db_remove", "bench_name": self.name}
        self.logger.debug(f"Removing database and user for bench: {self.name}", extra_fields=extra)
        try:
            self.database.remove_database_and_user()
            self.logger.info(f"Database and user removed for bench: {self.name}", extra_fields=extra)
        except Exception as e:
            extra["error"] = str(e)
            self.logger.exception(f"Failed to remove database and user for bench: {self.name}", extra_fields=extra)
            raise

    def _confirm_removal(self, default_choice: bool) -> bool:
        """Ask whether to remove this bench. `--yes` skips the caller, not this."""
        params: dict[str, Any] = {
            "prompt": f"🤔 Do you want to remove [bold][fm.ok]'{self.name}'[/bold][/fm.ok]",
            "choices": ["yes", "no"],
            "required_flag": "--yes or -y",
        }
        if default_choice:
            params["default"] = "no"
        return self.output.prompt_ask(**params) == "yes"

    def remove_bench(
        self,
        default_choice: bool = True,
        delete_db_from_global_db: bool | None = None,
        prompt: bool = True,
    ) -> bool:
        """Remove the bench: its certificate, then its schema, then its containers and directory.

        This is the ONE implementation of that sequence. `BenchService.delete_bench` carried a second
        copy for the `--yes` path that differed only in skipping the confirmation below, and the two
        had already drifted: the database question named the bench here and the site there. Under
        multi-site each copy would have grown a per-site loop and partial-failure gating, so they are
        collapsed before that happens.

        Args:
            default_choice: confirmation default; True defaults to 'no'
            delete_db_from_global_db: None prompts when the schema is fm's to drop
            prompt: False skips the confirmation entirely, which is what `--yes` means
        """
        extra = {"operation": "bench_remove", "bench_name": self.name, "default_choice": default_choice}
        self.logger.debug(f"Attempting to remove bench: {self.name}", extra_fields=extra)

        if prompt and not self._confirm_removal(default_choice):
            self.logger.debug(f"Bench removal cancelled by user: {self.name}", extra_fields=extra)
            return False

        from frappe_manager.output_manager import spinner

        try:
            with spinner(self.output, "Removing bench"):
                try:
                    self.remove_certificate()
                except Exception as e:
                    self.output.warning(str(e))

                try:
                    self._handle_database_deletion(delete_db_from_global_db)
                except Exception as e:
                    # Deliberately fatal: continuing to remove_containers_and_dirs() destroyed the
                    # only record of the schema that is still there. See orphaned_database_error.
                    raise orphaned_database_error(self, e) from e

                self.remove_containers_and_dirs()

            self.logger.info(f"Bench removed successfully: {self.name}", extra_fields=extra)
            return True
        except Exception as e:
            extra["error"] = str(e)
            self.logger.exception(f"Failed to remove bench: {self.name}", extra_fields=extra)
            raise

    def external_database_config(self) -> DatabaseConfig | None:
        """
        The `[database."<site>"]` entry for this bench's site, or None when there is none.

        Presence is the switch: an entry means the schema lives on a server fm does not own, absence
        means the fm-managed `global-db` container, exactly as before `[database]` existed. This is
        the single place that decision is made; `BenchService` calls it on the bench it is deleting.
        """
        return self.bench_config.get_database_config(self.site_name)

    def _handle_database_deletion(self, delete_db_from_global_db: bool | None):
        """
        Handle database deletion based on user preference and where the schema lives.

        Args:
            delete_db_from_global_db: User preference for database deletion.
                                     None = prompt if using global-db
                                     True = delete from global-db
                                     False = don't delete from global-db
        """
        extra = {"operation": "db_handle_deletion", "bench_name": self.name}
        self.logger.debug(f"Handling database deletion for bench: {self.name}", extra_fields=extra)
        try:
            external_db = self.external_database_config()

            if external_db is not None:
                # fm could drop this schema: the site's own grant carries DROP and its password is in
                # site_config.json. It does not, because the schema is not fm's to drop.
                self.output.print(
                    f"Database '[bold]{external_db.name}[/bold]' lives on '[bold]{external_db.host}[/bold]', "
                    "a server fm does not own. Skipping database deletion"
                )
                self.logger.info(
                    f"Skipping database deletion - external database {external_db.name} on {external_db.host}: "
                    f"{self.name}",
                    extra_fields=extra,
                )
                return

            should_delete = delete_db_from_global_db

            if should_delete is None:
                params = {
                    "prompt": f"🗄️  Do you want to remove the database '[bold]{self.name}[/bold]' from global-db?",
                    "choices": ["yes", "no"],
                    "default": "yes",
                    "required_flag": "--delete-db-from-global-db or --no-delete-db-from-global-db",
                }
                choice = self.output.prompt_ask(**params)
                should_delete = choice == "yes"

            if should_delete:
                self.remove_database_and_user()
            else:
                self.output.print("Skipping database deletion from global-db")
                self.logger.info(f"Database deletion skipped by user: {self.name}", extra_fields=extra)

            self.logger.info(f"Database deletion handled: {self.name}", extra_fields=extra)
        except Exception as e:
            extra["error"] = str(e)
            self.logger.exception(f"Failed to handle database deletion for bench: {self.name}", extra_fields=extra)
            raise

    def ensure_workers_running_if_available(self):
        extra = {"operation": "workers_ensure_running", "bench_name": self.name}
        self.logger.debug(f"Ensuring workers running if available for bench: {self.name}", extra_fields=extra)
        try:
            self.worker_coordinator.ensure_workers_running_if_available()
            self.logger.info(f"Workers status ensured for bench: {self.name}", extra_fields=extra)
        except Exception as e:
            extra["error"] = str(e)
            self.logger.exception(f"Failed to ensure workers for bench: {self.name}", extra_fields=extra)
            raise

    def ensure_admin_tools_running_if_available(self):
        if self.admin_tools.compose_file_manager.exists():
            if self.bench_config.admin_tools:
                admin_tools_running = False
                try:
                    services = self.admin_tools.compose_file_manager.get_services_list()
                    containers = self.admin_tools.compose_file_manager.get_container_names().values()
                    all_statuses = self.admin_tools.docker_client.compose.get_all_services_status()
                    running_statuses = {
                        status["Service"]: status["State"]
                        for status in all_statuses
                        if status.get("Name") in containers
                    }
                    admin_tools_running = all(running_statuses.get(service) == "running" for service in services)
                except Exception:
                    admin_tools_running = False

                if not admin_tools_running:
                    if self.running:
                        self.admin_tools.enable()
            else:
                atleast_one_service_running = False

                try:
                    services = self.admin_tools.compose_file_manager.get_services_list()
                    containers = self.admin_tools.compose_file_manager.get_container_names().values()
                    all_statuses = self.admin_tools.docker_client.compose.get_all_services_status()
                    running_services = {
                        status["Service"]: status["State"]
                        for status in all_statuses
                        if status.get("Name") in containers
                    }
                    for service in running_services:
                        if service == "running":
                            atleast_one_service_running = True
                except Exception:
                    atleast_one_service_running = False

                if atleast_one_service_running:
                    self.admin_tools.disable()

    def sync_admin_tools_compose(self):
        extra = {"operation": "admin_tools_sync_compose", "bench_name": self.name}
        self.logger.debug(f"Syncing admin tools compose for bench: {self.name}", extra_fields=extra)
        try:
            self.admin_tools.generate_compose()
            restart_required = self.admin_tools.enable(force_recreate_container=True)
            self.logger.info(f"Admin tools compose synced for bench: {self.name}", extra_fields=extra)
            return restart_required
        except Exception as e:
            extra["error"] = str(e)
            self.logger.exception(f"Failed to sync admin tools compose for bench: {self.name}", extra_fields=extra)
            raise

    def frappe_service_run_command(self, command: str):
        try:
            self.docker_client.compose.exec("frappe", command, user="frappe", stream=False)
        except DockerException as e:
            raise BenchException("frappe", f"Faild to run {command} in frappe service.")

    def get_apps_dev_requirements(self) -> list[str]:
        """Parse pip requirement string to package name and version"""
        return self.devtools.get_apps_dev_requirements()

    def remove_dev_packages(self):
        return self.devtools.remove_dev_packages()

    def install_dev_packages(self):
        return self.devtools.install_dev_packages()

    def is_supervisord_running(self, interval: int = 2, timeout: int = 30):
        return self.supervisor.is_supervisord_running(interval, timeout)

    def reset(self, admin_password: str | None = None):
        admin_pass = None

        if admin_password:
            admin_pass = admin_password
        else:
            if not admin_pass:
                site_config = self.get_bench_site_config()
                if "admin_password" in site_config:
                    admin_pass = site_config["admin_password"]
                    self.output.print("Using admin_password defined in site_config.json")

            if not admin_pass:
                common_site_config = self.get_common_bench_config()
                if "admin_password" in common_site_config:
                    admin_pass = common_site_config["admin_password"]
                    self.output.print("Using admin_password defined in common_site_config.json")

        if not admin_pass:
            admin_pass = self.output.prompt_ask(
                prompt=f"Please enter admin password for site {self.name}",
                required_flag="--admin-pass",
            )

        self.output.change_head(f"Resetting bench site {self.site_name}")

        self.site_manager.reset_bench_site(admin_pass)
        self.set_bench_site_config({"admin_password": admin_pass})

        self.output.print(f"Reset bench site {self.site_name}")

    def restart_supervisor_service(
        self,
        service: str,
        docker_client_obj: DockerClient | None = None,
        timeout: int = 30,
        interval: int = 1,
        force: bool = False,
    ):
        return self.supervisor.restart_supervisor_service(service, docker_client_obj, timeout, interval, force)

    def restart_web_containers_services(self, use_container_restart: bool = False, force: bool = False):
        """
        Restarts frappe server and socketio containers.

        Args:
            use_container_restart: If True, restart entire containers. If False, restart supervisor processes.
            force: If True, use aggressive restart (timeout=0 for container, stop+start for supervisor).
        """
        web_services = [
            SiteServicesEnum.frappe.value,
            SiteServicesEnum.socketio.value,
        ]

        if use_container_restart:
            self.docker_ops.restart_services(web_services, force=force)
        else:
            for service in web_services:
                self.output.change_head(f"Restarting web services - {service}")
                is_restarted = self.restart_supervisor_service(service, force=force)
                if is_restarted:
                    action = "Stopped and started" if force else "Restarted"
                    self.output.print(f"{action} supervisor processes - {service}")

    def restart_redis_services_containers(self):
        """Restarts redis containers, unless this bench points at an external redis."""

        if self.bench_config.redis:
            self.output.print("Bench uses an external redis. Skipping redis service restart")
            return

        redis_services = [
            SiteServicesEnum.redis_cache.value,
            SiteServicesEnum.redis_queue.value,
        ]
        self.output.change_head(f"Restarting redis services - {' '.join(redis_services)}")
        self.docker_ops.restart_services(redis_services)
        self.output.print(f"Restarted redis services - {' '.join(redis_services)}")

    def restart_nginx_service(self, force: bool = False):
        """
        Restarts nginx container.

        Args:
            force: If True, use timeout=0 for immediate kill. If False, use default graceful timeout.
        """
        nginx_service = [SiteServicesEnum.nginx.value]
        self.output.change_head("Restarting nginx service")
        self.docker_ops.restart_services(nginx_service, force=force)
        action = "Force restarted" if force else "Restarted"
        self.output.print(f"{action} nginx service")

    def restart_workers_containers_services(self, use_container_restart: bool = False, force: bool = False):
        """Restarts workers and schedule containers"""
        self.worker_coordinator.restart_workers_containers_services(
            use_container_restart=use_container_restart,
            force=force,
        )

    def apply_upload_limit(self) -> bool:
        """Push ``bench_config.upload_limit`` to the two places outside the nginx conf.

        Idempotent, and reads the limit off the config rather than taking it as an argument, so the
        create pipeline, ``fm start`` and ``fm update --upload-limit`` apply one value the same way.
        The bench's own nginx conf is NOT written here: it is an fm-managed conf, so
        ``ensure_fm_nginx_confs`` owns it and reloads once for whatever changed.

        Both steps are skipped when their target does not exist yet, which is what a bench with no
        sites in it (so no served domain) and a not-yet-provisioned services dir look like.

        Returns True when something on disk changed, so a caller can reload the global proxy only
        when it needs to. The proxy is shared by every bench, so reloading it on each ``fm start``
        would be a cost paid by benches that changed nothing.
        """
        upload_limit = self.bench_config.upload_limit
        changed = False

        site_config = self.path / "workspace" / "frappe-bench" / "sites" / self.site_name / "site_config.json"
        if site_config.is_file():
            wanted_bytes = self._parse_size_to_bytes(upload_limit)
            try:
                current = json.loads(site_config.read_text()).get("max_file_size")
            except (OSError, ValueError):
                current = None
            if current != wanted_bytes:
                self.set_bench_site_config({"max_file_size": wanted_bytes})
                changed = True

        # The global proxy caps the request before bench nginx ever sees it, so the bench conf alone
        # is not enough: whichever limit is lower wins, and a bench with no vhost entry at all gets
        # the proxy's own 1M default no matter what its own nginx allows.
        vhostd_dir = self.services.path / "nginx-proxy" / "vhostd"
        if vhostd_dir.exists():
            domains = self.domains
            before = {d: (vhostd_dir / d).read_text() if (vhostd_dir / d).is_file() else None for d in domains}
            UploadLimitManager(vhostd_dir).set_upload_limit_for_domains(domains, upload_limit.lower())
            for domain, previous in before.items():
                path = vhostd_dir / domain
                if path.is_file() and path.read_text() != previous:
                    changed = True

        return changed

    def update_upload_limit(self, upload_limit: str):
        """Change the upload size limit and apply it everywhere it is enforced.

        Args:
            upload_limit: Size string (e.g., "50M", "100M", "1G")

        Raises:
            BenchException: If format is invalid or operation fails
        """
        import re

        if not re.match(r"^\d+[MG]$", upload_limit, re.IGNORECASE):
            raise BenchException(
                self.name,
                message=f"Invalid upload limit format: '{upload_limit}'. Use format like '50M' or '1G'",
            )

        self.bench_config.upload_limit = upload_limit.upper()
        self.save_bench_config()

        # Writes conf/custom/upload-limit.conf from the config just saved, and reloads bench nginx
        # once if it changed. Compose is deliberately NOT regenerated: `upload_limit` reaches no
        # compose input and no template, so the old regeneration step here did nothing.
        self.ensure_fm_nginx_confs()
        self.apply_upload_limit()

        if self.services.is_service_running("global-nginx-proxy"):
            self.services.nginx_controller.reload()

        self.output.print(
            f"Upload size limit updated to {upload_limit} "
            f"(site_config: {self._parse_size_to_bytes(upload_limit)} bytes, nginx: {upload_limit.lower()})",
        )

    def _frontend_network_subnet(self) -> str | None:
        """The fm frontend network subnet, from the services compose file (the
        pinned source of truth), falling back to a live network inspect for
        older services composes without an ipam block."""
        try:
            networks = self.services.compose_file_manager.yml.get("networks", {})
            ipam = networks.get("global-frontend-network", {}).get("ipam", {})
            subnet = (ipam.get("config") or [{}])[0].get("subnet")
            if subnet:
                return str(subnet)
        except Exception:
            pass
        try:
            from frappe_manager.utils.network import detect_running_network

            info = detect_running_network()
            return info.get("subnet_cidr") if info else None
        except Exception:
            return None

    def ensure_fm_nginx_confs(self) -> None:
        """Materialize the fm-managed bench nginx confs, reloading once when
        anything changed. A no-op when nginx is not up yet; a reload nginx REJECTED
        raises, because the files on disk then describe a state the server is not in.

        Only runtime-dependent config lives here. The JSON access-log format is
        static and ships in the nginx image template instead.

        - real-ip: every request reaches bench nginx from the global proxy's
          address on the fm frontend network, so logs, frappe's request_ip and
          per-IP rate limiting otherwise see one IP for the whole internet.
          The frontend network is the only route in (bench nginx publishes no
          ports), so trusting its subnet is safe.
        - auth: one htpasswd file backs both basic auth surfaces. ``auth.web``
          renders a server-context include, which every location inherits, plus
          the realm map that backs ``allow_paths``; ``auth.tools`` is carried by
          the admin tools locations themselves, so a scope change is pushed into
          admin-tools.conf here too. The credentials are minted on the first
          pass that needs them.

        The three auth paths are fm-owned, so they are also removed again once
        no surface wants them, unless the file on disk was hand written.
        real-ip.conf is never removed: it is deliberately left in place when the
        subnet cannot be detected.
        """
        from frappe_manager.site_manager.modules.auth import (
            MAP_CONF_NAME,
            SERVER_CONF_NAME,
            build_auth_map_conf,
            build_server_auth_conf,
            container_htpasswd_path,
            generate_password,
            htpasswd_name,
            is_fm_auth_conf,
            write_htpasswd,
        )
        from frappe_manager.site_manager.modules.realip import build_bench_realip_conf

        conf_dir = self.path / "configs" / "nginx" / "conf"
        wanted: dict[Path, str] = {}
        subnet = self._frontend_network_subnet()
        if subnet:
            wanted[conf_dir / "custom" / "real-ip.conf"] = build_bench_realip_conf(subnet)

        # The bench's own client_max_body_size. Unconditional, because `upload_limit` always has a
        # value (default 50M) and nothing else ever wrote this file at create: a bench came up on
        # nginx's built-in 1M default while its config advertised 50M, so uploads over 1M were
        # refused with a 413 until someone happened to run `fm update --upload-limit`.
        wanted[conf_dir / "custom" / "upload-limit.conf"] = (
            f"client_max_body_size {self.bench_config.upload_limit.lower()};\n"
        )

        auth = self.bench_config.auth or AuthConfig()
        needs_auth = auth.web or (auth.tools and bool(self.bench_config.admin_tools))

        if needs_auth and auth.password is None:
            auth.password = generate_password()
            self.bench_config.auth = auth
            self.save_bench_config(print_message=False)

        htpasswd_path = conf_dir / "http_auth" / htpasswd_name(self.name)
        server_conf_path = conf_dir / "custom" / SERVER_CONF_NAME
        map_conf_path = conf_dir / "conf.d" / MAP_CONF_NAME

        if auth.web:
            wanted[server_conf_path] = build_server_auth_conf(
                container_htpasswd_path(self.name), auth.allow_ips, auth.allow_paths
            )
            if auth.allow_paths:
                wanted[map_conf_path] = build_auth_map_conf(auth.allow_paths, auth.allow_ips)

        changed = False

        if needs_auth and auth.password:
            changed |= write_htpasswd(htpasswd_path, auth.user, auth.password)
        elif htpasswd_path.exists():
            htpasswd_path.unlink()
            changed = True

        for path in (server_conf_path, map_conf_path):
            if path in wanted or not path.exists():
                continue
            if not is_fm_auth_conf(path.read_text()):
                continue
            path.unlink()
            changed = True

        for path, content in wanted.items():
            if path.exists() and path.read_text() == content:
                continue
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content)
            changed = True

        admin_tools_conf = conf_dir / "custom" / "admin-tools.conf"
        if self.bench_config.admin_tools and admin_tools_conf.exists():
            try:
                before = admin_tools_conf.read_text()
                self.admin_tools.save_nginx_location_config()
                changed = changed or admin_tools_conf.read_text() != before
            except Exception:
                pass

        if changed:
            # The confs and the htpasswd are already on disk, so a reload that did not happen
            # leaves nginx serving the PREVIOUS config: for `fm auth --protect` that means the
            # site keeps answering unauthenticated while the command reports it protected. The
            # two failure shapes differ and only one of them is a live problem.
            try:
                reloaded = self.bench_nginx_controller.reload()
            except Exception as e:
                # nginx is up and rejected the new config, so it is still serving the old one.
                raise BenchException(
                    self.name,
                    message=f"nginx rejected the updated configuration and is still serving the previous one: {e}",
                ) from e

            if not reloaded:
                # The nginx container is not running, so nothing is being served with the stale
                # config; the files are in place and apply when it next starts.
                self.logger.debug(
                    f"Bench nginx is not running; fm nginx confs apply on next start: {self.name}",
                    extra_fields={"operation": "nginx_confs_ensure", "bench_name": self.name},
                )

    def _parse_size_to_bytes(self, size_str: str) -> int:
        """
        Convert size string (e.g., '50M', '1G') to bytes for Frappe site_config.json.

        Args:
            size_str: Size string (e.g., "50M", "1G")

        Returns:
            Size in bytes (integer)

        Raises:
            BenchException: If format is invalid

        Examples:
            "50M" -> 52428800 (50 * 1024 * 1024)
            "1G"  -> 1073741824 (1 * 1024 * 1024 * 1024)
        """
        import re

        match = re.match(r"^(\d+)([MG])$", size_str, re.IGNORECASE)
        if not match:
            raise BenchException(
                self.name,
                message=f"Invalid size format: '{size_str}'. Expected format: <number><unit> (e.g., '50M', '1G')",
            )

        value = int(match.group(1))
        unit = match.group(2).upper()

        if unit == "M":
            return value * 1024 * 1024  # Convert MB to bytes
        if unit == "G":
            return value * 1024 * 1024 * 1024  # Convert GB to bytes

        # Should never reach here due to regex validation
        raise BenchException(self.name, message=f"Unsupported unit: {unit}")

    def get_available_services(self) -> list[str]:
        """
        Get all available services from all compose files.

        Dynamically discovers services from:
        - docker-compose.yml (main services: frappe, nginx, redis, etc.)
        - docker-compose.workers.yml (workers: schedule, default-worker, short-worker, long-worker, custom workers)
        - docker-compose.admin-tools.yml (admin tools: adminer, mailpit)

        Returns:
            List of available service names across all compose files
        """
        services = []

        # Get services from main compose file
        if self.compose_file_manager.compose_path.exists():
            services.extend(self.compose_file_manager.get_services_list())

        # Get services from workers compose file
        workers_compose_path = self.path / "docker-compose.workers.yml"
        if workers_compose_path.exists():
            services.extend(self.workers.compose_file_manager.get_services_list())

        # Get services from admin tools compose file
        admin_tools_compose_path = self.path / "docker-compose.admin-tools.yml"
        if admin_tools_compose_path.exists():
            services.extend(self.admin_tools.compose_file_manager.get_services_list())

        return services
