import os
import platform
import shutil
from datetime import datetime
from pathlib import Path
from typing import Any

import typer
from jinja2 import Template

from frappe_manager import (
    CLI_DIR,
    CLI_SERVICES_DIRECTORY,
    MARIADB_IMAGE,
    MIGRATION_COMMANDS,
    OBSERVE_ONLY_COMMANDS,
    STACK_AUTOSTART_EXEMPT_PREFIXES,
)
from frappe_manager.docker import ComposeFile, DockerClient, DockerException
from frappe_manager.metadata_manager import FMConfigManager
from frappe_manager.output_manager import OutputHandler
from frappe_manager.output_manager.rich_output import RichOutputHandler
from frappe_manager.services_manager.database_service_manager import (
    DatabaseServerServiceInfo,
    DatabaseServiceManager,
    MariaDBManager,
)
from frappe_manager.services_manager.services_exceptions import (
    ServicesComposeNotExist,
    ServicesException,
    ServicesNotCreated,
)
from frappe_manager.ssl_manager.nginx_controller import NginxController
from frappe_manager.ssl_manager.proxy_storage import ProxyStoragePaths
from frappe_manager.utils.docker import host_run_cp
from frappe_manager.utils.helpers import (
    get_template_path,
    get_unix_groups,
    random_password_generate,
)
from frappe_manager.utils.network import (
    compute_network_config,
    detect_running_network,
    find_available_subnet,
    get_docker_network_subnets,
    pick_proxy_ip,
)

# fm's own database servers, by compose service name. Each is switched off while no site uses it.
DATABASE_SERVICES = ("mariadb", "postgres")


class ServicesManager:
    def __init__(
        self,
        path=CLI_SERVICES_DIRECTORY,
        verbose: bool = False,
        invoked_subcommand: str | None = None,
        output_handler: OutputHandler | None = None,
    ) -> None:
        self.path = path
        self.compose_path = self.path / "docker-compose.yml"
        self.invoked_subcommand = invoked_subcommand
        self.output = output_handler or RichOutputHandler()

    def engines_in_use(self) -> set[str]:
        """Which of fm's OWN database servers some site on this host lives on.

        The switch is per SITE and explicit: `[sites."<site>".database]` records which engine and
        whose server (`bench_config.py:1397`), so the host-wide answer is a set read off disk,
        without starting anything.

        Deliberately pessimistic. Every uncertainty answers "mariadb", because that is the engine
        every bench predating this had:

        - a bench whose config cannot be read or parsed. It may hold a site on fm's mariadb, and
          stopping the server under a live site is far worse than running one nobody uses;
        - a bench that records no sites at all, which is what an unmigrated (pre-`[sites]`) config
          looks like from here.

        Answering on what is RECORDED rather than on what is running is the whole point: a stopped
        bench still owns its schema, and a server disabled because nothing happened to be up is a
        bench that cannot start again.
        """
        from frappe_manager import CLI_BENCH_CONFIG_FILE_NAME, CLI_BENCHES_DIRECTORY
        from frappe_manager.site_manager.bench_config import BenchConfig, DatabaseEngine

        engines: set[str] = set()
        if not CLI_BENCHES_DIRECTORY.is_dir():
            return engines

        for bench_dir in CLI_BENCHES_DIRECTORY.iterdir():
            if not (bench_dir / "docker-compose.yml").is_file():
                continue
            config_path = bench_dir / CLI_BENCH_CONFIG_FILE_NAME
            try:
                config = BenchConfig.import_from_toml(config_path)
            except Exception:
                engines.add(DatabaseEngine.mariadb.value)
                continue
            sites = config.sites or {}
            if not sites:
                engines.add(DatabaseEngine.mariadb.value)
                continue
            engines.update(
                site.database.type.value for site in sites.values() if not site.database.external
            )
        return engines

    def reconcile_database_services(self, engines: set[str] | None = None) -> None:
        """Put each of fm's database services in or out of the `disabled` compose profile, to match
        whether any site on this host actually lives on it, and start or remove its container.

        A host whose every site is on an external database ran a MariaDB nobody connected to: a
        server, its datadir and its memory, maintained for nothing. The same mechanism already
        suppresses a bench's own redis containers when that bench points at a redis fm does not
        own (`compose_shape.py:270`); this is that rule one level up, on the shared stack, and with
        a second engine it is what keeps a postgres-only host from running a MariaDB as well.

        Two things the profile does NOT do, both learned from the redis side (`update.py:463`):
        it does not stop a service already running, and `compose up` silently IGNORES a service
        whose profile is inactive rather than failing. So the container is removed by name here,
        and started here too rather than left to the autostart, which cannot address it while the
        profile is off.

        `engines` is for a caller that already knows the answer and is about to change it -- `fm
        create` enables the server before `bench new-site` reaches for it, at which point the site
        that needs it is not on disk yet and the scan would answer with the old set.
        """
        if engines is None:
            engines = self.engines_in_use()

        for service in DATABASE_SERVICES:
            needed = service in engines
            # Before the no-op shortcut below: a host whose postgres is ALREADY enabled changes no
            # profile, so a check that ran only on the transition would never fire on the one host
            # that needs it -- the one whose datadir predates an image fm has since bumped.
            if needed and service == "postgres":
                self.check_postgres_datadir_major()

            if self.compose_file_manager.is_service_profile_disabled(service) == (not needed):
                continue

            self.compose_file_manager.set_service_disabled(service, disabled=not needed)
            self.compose_file_manager.write_to_file()

            if needed:
                self.output.change_head(f"Starting fm's {service}: a site on this host uses it")
                self.docker_client.compose.up(services=[service], detach=True, pull="missing")
            else:
                self.output.print(f"Stopping fm's {service}: no site on this host uses it.")
                self.docker_client.compose.rm(services=[service], stop=True, force=True)

    def check_postgres_datadir_major(self) -> None:
        """Refuse to start fm's postgres against a datadir written by a different major.

        Postgres has no equivalent of MariaDB's MARIADB_AUTO_UPGRADE. Started against a datadir
        from another major it exits immediately with "database files are incompatible with
        server", and compose's `restart: always` turns that into a crash loop -- a container that
        keeps coming back and never serves, with the real sentence buried in `docker logs`.

        `PG_VERSION` in the datadir is the server's own record of which major wrote it, so the
        answer needs no running container and costs one file read.

        Named as a REFUSAL rather than an upgrade, because there is no safe automatic move: going
        up a major means a dump taken by the old server and a restore into a new datadir, and
        doing that silently on start is the one thing an operator must not discover afterwards.
        """
        marker = self.path / "postgres" / "data" / "pgdata" / "PG_VERSION"
        if not marker.is_file():
            return

        try:
            on_disk = marker.read_text().strip()
        except OSError:
            return

        service = self.compose_file_manager.yml.get("services", {}).get("postgres", {})
        image = str(service.get("image", ""))
        wanted = image.rpartition(":")[2].split(".")[0]
        if not on_disk or not wanted or on_disk == wanted:
            return

        self.output.exit(
            f"fm's postgres data was written by PostgreSQL {on_disk}, and fm now runs {wanted}. "
            f"Postgres does not upgrade a datadir in place, so starting it would fail with "
            f"'database files are incompatible with server'. Dump every database with the {on_disk} "
            f"image, move {marker.parent} aside, and restore into the new one."
        )

    def database_server_info_for(self, engine) -> DatabaseServerServiceInfo:
        """Endpoint and root credentials for one of fm's OWN database servers.

        `self.database_manager` is and stays the mariadb one, because everything that predates a
        second engine reaches for it by that name. Anything that knows WHICH engine it wants asks
        here instead, and gets a server whose host is that engine's compose service and whose
        password is that engine's own secret -- so a credential can only ever travel to the server
        it was minted for.
        """
        from frappe_manager.site_manager.bench_config import DatabaseEngine

        if engine is DatabaseEngine.postgres:
            return DatabaseServerServiceInfo(
                host="postgres",
                # The superuser the official image creates, and the login Frappe's postgres
                # setup_db falls back to. Not `root`.
                user="postgres",
                port=5432,
                password=(self.path / "secrets" / "postgres_root_password.txt").read_text().strip(),
            )
        return self.database_manager.database_server_info

    def switched_off_reason(self, service: str) -> str | None:
        """Why `service` will not respond to a start or a restart, or None when it will.

        docker compose cannot address a service whose profile is inactive: `compose up` IGNORES it
        and `compose restart` fails with "no such service". Either way the command would report
        work it did not do, so every command that drives a named service asks here first.
        """
        if not self.compose_file_manager.is_service_profile_disabled(service):
            return None
        return (
            f"{service} is switched off because every site on this host uses an external database. "
            f"It starts by itself when a bench that needs it is created."
        )

    def entrypoint_checks(self, start=False):
        if not self.path.exists():
            try:
                self.output.print(
                    f"Creating global services [blue]{', '.join(self.compose_file_manager.get_services_list())}[/blue].",
                    emoji_code=":construction:",
                )
                self.create(clean_install=True)

            except Exception as e:
                # display_error does not raise, so the ServicesNotCreated wrapper below actually
                # propagates and the caller's `except ServicesNotCreated: remove_itself()` cleanup
                # gets to remove the half-built services directory.
                self.output.display_error("Error during service creation")
                raise ServicesNotCreated(
                    f"Not able to create global services [blue]{', '.join(self.compose_file_manager.get_services_list())}[/blue].",
                ) from e

            output = self.docker_client.compose.pull(stream=False)

            self.output.print(
                f"Created global services [blue]{', '.join(self.compose_file_manager.get_services_list())}[/blue].",
            )

            if start:
                self.docker_client.compose.up(services=[], detach=True, pull="never")

        if not self.compose_path.exists():
            raise ServicesComposeNotExist(
                f"Seems like global services has taken a down. No compose file found at {self.compose_path}.",
            )

        # A pre-v1.0.0 install still has `global-db`/`global-nginx-proxy` in its services
        # compose. Commands whitelisted past the migration gate (`fm list`, `fm compose`, ...)
        # reach this init anyway, and against the old names it dies deep inside MariaDBManager
        # with a missing-password error that says nothing about the actual problem. Detect the
        # old shape and refuse with the fix. Not legacy support: nothing here can run on it.
        # The migration commands and the `self` family stay usable because they ARE the way
        # out (this check runs before their command bodies get to do the cutover); for them
        # the database manager below stays unwired, since it cannot be built against the old
        # names either. `invoked_subcommand` carries the FULL command path ("services
        # migrate"), so the escape hatch does not open for `fm services start`.
        command = self.invoked_subcommand or ""
        old_names = self.compose_file_manager.get_services_list()
        if "global-db" in old_names and "mariadb" not in old_names:
            if command not in MIGRATION_COMMANDS and command.split(" ")[0] != "self":
                self.output.exit(
                    "The global services predate the v1.0.0 rename (global-db -> mariadb). "
                    "Run 'fm services migrate' to cut this install over."
                )
            return

        if start:
            # The root callback passes the FULL command path ("services migrate"); for the
            # sub-Typers the group name comes first. The services/self families act ON the
            # global stack (stop/start/shell/self stop) and must be able to run against a
            # deliberately stopped stack instead of silently starting it first. `compose` is a
            # diagnostic passthrough to docker compose: `fm compose BENCH ps` against a stopped
            # stack must report it stopped, not boot it. Observers (fm list, fm info, ...) hold
            # no host lock so they can run DURING a migration -- an auto-start here would boot
            # the half-migrated stack; they report the stack as they find it instead.
            if command.split(" ")[0] not in STACK_AUTOSTART_EXEMPT_PREFIXES and command not in OBSERVE_ONLY_COMMANDS:
                services = self.compose_file_manager.get_services_list(exclude_disabled=True)
                containers = self.compose_file_manager.get_container_names().values()
                all_statuses = self.docker_client.compose.get_all_services_status()
                running_statuses = {
                    status["Service"]: status["State"] for status in all_statuses if status.get("Name") in containers
                }
                all_running = all(running_statuses.get(s) == "running" for s in services)

                if not all_running:
                    self.output.print(
                        f"Started non running global services [blue]{', '.join(services)}[/blue].",
                    )
                    self.docker_client.compose.up(services=[], detach=True, pull="missing")

        self.database_manager: DatabaseServiceManager = MariaDBManager(
            DatabaseServerServiceInfo.import_from_compose_file("mariadb", self.compose_file_manager),
            self.compose_file_manager,
            self.docker_client,
            output_handler=self.output,
        )

        # After `database_manager` is wired, because reconciling may have to wait for the server to
        # accept connections. Skipped for the same commands the autostart above skips -- an observer
        # reports the stack as it finds it, and the services/self families act ON the stack -- plus:
        #
        # - the migration commands, which own the stack's lifecycle for the length of their run and
        #   dump every database through it;
        # - `create`, whose bench is not on disk yet, so the scan would answer No and stop a server
        #   the command is about to need. `fm create` enables it itself, at the point it knows.
        reconcile_skip = set(MIGRATION_COMMANDS) | {"create"} | set(OBSERVE_ONLY_COMMANDS)
        if command.split(" ")[0] not in STACK_AUTOSTART_EXEMPT_PREFIXES and command not in reconcile_skip:
            self.reconcile_database_services()

    def init(self):
        current_system = platform.system()

        template_name = "docker-compose.services.tmpl"
        if current_system == "Darwin":
            template_name = "docker-compose.services.osx.tmpl"

        self.compose_file_manager = ComposeFile(self.compose_path, template_name=template_name)
        self.docker_client = DockerClient(compose_file_path=self.compose_path, output=self.output)

        # Transition wiring, not legacy support: on a pre-v1.0.0 compose the proxy service is
        # still `global-nginx-proxy`, and ProxyStoragePaths resolves its volumes eagerly -- so
        # `fm migrate` (the only command allowed to run against that compose, see
        # entrypoint_checks) could never construct this manager to perform the cutover. The
        # storage DIRECTORIES are identical under either name.
        proxy_service = "nginx-proxy"
        if self.compose_path.exists():
            services_list = self.compose_file_manager.get_services_list()
            if "nginx-proxy" not in services_list and "global-nginx-proxy" in services_list:
                proxy_service = "global-nginx-proxy"
        self.proxy_storage = ProxyStoragePaths(proxy_service, self.compose_file_manager)
        self.nginx_controller = NginxController(proxy_service, self.compose_file_manager, self.docker_client)

        self.proxy_manager = type(
            "ProxyManager",
            (),
            {
                "dirs": self.proxy_storage.dirs,
                "restart": self.nginx_controller.restart,
                "reload": self.nginx_controller.reload,
            },
        )()

        self.fm_headers_path: Path = self.proxy_storage.dirs.confd.host / "fm_headers.conf"
        self.set_frappe_headers_conf()

    def set_frappe_headers_conf(self):
        """Refresh nginx-proxy's fm_headers.conf, but only when its content actually changes.

        `init()` runs on every fm invocation, so an unconditional write touched a file inside a
        running proxy's config directory for read-only commands like `fm list` and `fm info` --
        enough to fail on a read-only mount or under a second user, and to make the file's mtime
        lie about when the proxy config last changed. The comparison is also what upgrades an
        existing install: the file is rewritten whenever fm ships different content for it.
        """
        if not self.fm_headers_path.parent.exists():
            return

        template_path: Path = get_template_path("fm_headers.conf.tmpl")
        desired = Template(template_path.read_text()).render()

        if self.fm_headers_path.exists() and self.fm_headers_path.read_text() == desired:
            return

        self.fm_headers_path.write_text(desired)

    def reconcile_standalone_vhosts(self) -> list[str]:
        """Rebuild the standalone (non-bench) vhosts from external_domains.toml. Returns what changed.

        Runs before the proxy comes up, so a conf.d that lost these files -- recreated container,
        restored services directory -- serves the external domains again instead of 503, and their
        HTTP-01 renewals keep working. Nothing else rebuilds them: fm_headers.conf is rewritten
        above and default.conf belongs to docker-gen.
        """
        from frappe_manager.ssl_manager.external_domain_manager import ExternalDomainConfigManager
        from frappe_manager.ssl_manager.standalone_nginx_config_manager import (
            StandaloneNginxConfigManager,
            reconcile_standalone_configs,
        )

        dirs = self.proxy_storage.dirs
        if not dirs.confd.host.exists():
            return []

        external_manager = ExternalDomainConfigManager(self.path / "nginx-proxy" / "external_domains.toml")
        domains = external_manager.list_domains()
        if not domains:
            return []

        certs_dir = Path(dirs.certs.host)
        manager = StandaloneNginxConfigManager(
            conf_dir=dirs.confd.host,
            webroot_dir_container=dirs.html.container,
            certs_dir_container=dirs.certs.container,
        )

        def certificate_linked(domain: str) -> bool:
            # `lexists`, NOT `exists`: certs/<domain>.crt is a symlink whose target is the
            # CONTAINER path (/usr/share/nginx/ssl/...), so it never resolves on the host and
            # `exists()` answers False for every certificate fm has ever issued -- which would
            # downgrade a working HTTPS vhost to the challenge-only one on the next start.
            return os.path.lexists(certs_dir / f"{domain}.crt")

        return reconcile_standalone_configs(manager, {entry.domain: certificate_linked(entry.domain) for entry in domains})

    def create(self, backup: bool = False, clean_install: bool = True):
        envs = {
            "mariadb": {
                "MYSQL_ROOT_PASSWORD_FILE": "/run/secrets/db_root_password",
                "MYSQL_DATABASE": "root",
                "MYSQL_USER": "admin",
                "MYSQL_PASSWORD_FILE": "/run/secrets/db_password",
            },
        }
        current_system = platform.system()
        inputs: dict[str, Any] = {"environment": envs}
        try:
            user = {
                "mariadb": {
                    "uid": os.getuid(),
                    "gid": os.getgid(),
                },
                "postgres": {
                    "uid": os.getuid(),
                    "gid": os.getgid(),
                },
            }

            if not current_system == "Darwin":
                user["nginx-proxy"] = {
                    "uid": os.getuid(),
                    "gid": get_unix_groups()["docker"],
                }

            inputs["user"] = user
        except KeyError:
            raise ServicesException(
                "docker group not found in system. Please add docker group to the system and current user to the docker group.",
            )

        if backup and self.path.exists():
            backup_path: Path = CLI_DIR / "backups"
            backup_path.mkdir(parents=True, exist_ok=True)
            current_time = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
            backup_dir_name = f"services_{current_time}"
            self.path.rename(backup_path / backup_dir_name)

        if self.path.exists():
            shutil.rmtree(self.path)

        self.path.mkdir(parents=True, exist_ok=True)

        dirs_to_create = [
            "mariadb/conf",
            "mariadb/logs",
            "nginx-proxy/dhparam",
            "nginx-proxy/certs",
            "nginx-proxy/confd",
            "nginx-proxy/htpasswd",
            "nginx-proxy/vhostd",
            "nginx-proxy/html",
            "nginx-proxy/logs",
            "nginx-proxy/run",
            "nginx-proxy/ssl",
            "nginx-proxy/cache",
            "secrets",
        ]

        self.generate_compose(inputs)

        # Ensure network configuration (subnet + proxy IP) is set. Only the
        # frontend network is auto-sized to dodge host subnet clashes; the
        # backend network keeps its fixed subnet from the template.
        fm_config = FMConfigManager.import_from_toml()
        if not fm_config.network.configured:
            running = detect_running_network("fm-frontend-network", docker=self.docker_client)
            if running:
                subnet_cidr = running["subnet_cidr"]
                # The proxy may not be attached yet; pick a free IP in the subnet
                # instead of persisting an empty address.
                proxy_ip = running["proxy_ip"] or pick_proxy_ip(subnet_cidr, "fm-frontend-network")
                fm_config.network.subnet_cidr = subnet_cidr
                fm_config.network.proxy_ip = proxy_ip
                fm_config.export_to_toml()
                self.output.print(f"Detected running network: {subnet_cidr}, proxy at {proxy_ip}")
            else:
                self.output.change_head("Configuring global frontend network")
                used_subnets = get_docker_network_subnets()
                cidr = find_available_subnet(used_subnets)
                net_config = compute_network_config(str(cidr), "fm-frontend-network")
                fm_config.network.subnet_cidr = net_config["subnet_cidr"]
                fm_config.network.proxy_ip = net_config["proxy_ip"]
                fm_config.export_to_toml()
                self.output.print(f"Assigned subnet {net_config['subnet_cidr']}, proxy IP {net_config['proxy_ip']}")

        if fm_config.network.subnet_cidr:
            try:
                self.compose_file_manager.yml["networks"]["frontend-network"]["ipam"]["config"][0]["subnet"] = (
                    fm_config.network.subnet_cidr
                )
            except (KeyError, IndexError):
                pass

        # Pin the proxy's static IP without dropping any other networks it's on
        if fm_config.network.proxy_ip:
            try:
                proxy_service = self.compose_file_manager.yml["services"]["nginx-proxy"]
                nets = proxy_service.get("networks")
                if isinstance(nets, list):
                    nets = {name: {} for name in nets}
                elif not isinstance(nets, dict):
                    nets = {}
                entry = nets.get("frontend-network")
                if not isinstance(entry, dict):
                    entry = {}
                entry["ipv4_address"] = fm_config.network.proxy_ip
                nets["frontend-network"] = entry
                proxy_service["networks"] = nets
            except KeyError:
                pass

        if current_system == "Darwin":
            self.compose_file_manager.remove_container_user("nginx-proxy")
            self.compose_file_manager.remove_container_user("mariadb")
            self.compose_file_manager.remove_container_user("postgres")
        else:
            dirs_to_create.append("mariadb/data")
            dirs_to_create.append("postgres/data")

        for folder in dirs_to_create:
            temp_dir = self.path / folder
            try:
                temp_dir.mkdir(parents=True, exist_ok=True)
            except Exception as e:
                raise ServicesNotCreated(
                    f"Failed to create global services required dir {temp_dir.absolute()}: {e}"
                ) from e

        db_password_path = self.path / "secrets" / "db_password.txt"
        db_root_password_path = self.path / "secrets" / "db_root_password.txt"
        postgres_root_password_path = self.path / "secrets" / "postgres_root_password.txt"

        db_password_path.write_text(random_password_generate(password_length=16, symbols=True))
        db_root_password_path.write_text(random_password_generate(password_length=24, symbols=True))
        # Minted even when no site uses postgres yet: compose has to resolve every declared secret
        # to parse the file at all, profile-disabled service or not, and a host that later creates
        # a postgres site must not need its services rebuilt to get one. No symbols: the value
        # reaches psql through PGPASSWORD and a connection URI in other tools, where several
        # punctuation characters need escaping that nothing here would do.
        postgres_root_password_path.write_text(random_password_generate(password_length=24, symbols=False))

        mariadb_conf = self.path / "mariadb/conf"
        mariadb_conf = str(mariadb_conf.absolute())
        host_run_cp(
            image=MARIADB_IMAGE,
            source="/etc/mysql/.",
            destination=mariadb_conf,
            docker=self.docker_client,
        )

        self.set_frappe_headers_conf()

        self.compose_file_manager.set_secret_file_path("db_password", str(db_password_path.absolute()))
        self.compose_file_manager.set_secret_file_path("db_root_password", str(db_root_password_path.absolute()))
        self.compose_file_manager.set_secret_file_path(
            "postgres_root_password", str(postgres_root_password_path.absolute())
        )
        self.compose_file_manager.write_to_file()

        if clean_install:
            self.docker_client.compose.down(remove_orphans=True, timeout=10, volumes=True, stream=False)

    def exists(self):
        return (self.path / "docker-compose.yml").exists()

    def generate_compose(self, inputs: dict):
        try:
            environments = inputs.get("environment")
            labels = inputs.get("labels")
            users = None

            if "user" in inputs:
                users = {}
                for container_name, user_data in inputs["user"].items():
                    users[container_name] = (user_data["uid"], user_data["gid"])

            cf = self.compose_file_manager
            if environments:
                cf.with_envs(environments)
            if labels:
                cf.with_labels(labels)
            if users:
                cf.with_users(users)

            if environments or labels or users:
                cf.commit()

        except Exception as e:
            raise ServicesNotCreated(f"Not able to generate global services compose file: {e}") from e

    def shell(self, container: str, user: str | None = None):
        self.output.stop()
        shell_path = "/bin/bash"
        try:
            if user:
                self.docker_client.compose.exec(container, user=user, command=shell_path, capture_output=False)
            else:
                self.docker_client.compose.exec(container, command=shell_path, capture_output=False)
        except DockerException as e:
            # `fm shell` for a bench execs into the container and propagates its status; the global
            # shell must agree, otherwise a script cannot tell a failed command from a clean exit.
            self.output.warning(f"Shell exited with error code: {e.output.exit_code}")
            raise typer.Exit(e.output.exit_code) from e

    def remove_itself(self):
        shutil.rmtree(self.path)

    def is_service_running(self, service: str) -> bool:
        """Check if a service is running."""
        all_statuses = self.docker_client.compose.get_all_services_status()
        containers = self.compose_file_manager.get_container_names()
        service_container = containers.get(service)

        for status in all_statuses:
            if status.get("Name") == service_container:
                return status.get("State") == "running"
        return False

    def start_service(self, services: list[str] | None = None, force_recreate: bool = False):
        services = services or []
        # Before the proxy reads conf.d, not after: a vhost written afterwards needs an extra
        # reload, and a missing one would already have served 503 in the meantime.
        self.reconcile_standalone_vhosts()
        self.docker_client.compose.up(
            services=services,
            detach=True,
            pull="never",
            force_recreate=force_recreate,
        )

    def stop_service(self, services: list[str] | None = None, timeout: int = 10):
        services = services or []
        self.docker_client.compose.stop(services=services, timeout=timeout)

    def restart_service(self, services: list[str] | None = None):
        services = services or []
        self.docker_client.compose.restart(services=services)
