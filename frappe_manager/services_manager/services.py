import os
import platform
import shutil
from datetime import datetime
from pathlib import Path
from typing import Any

from jinja2 import Template

from frappe_manager import CLI_DIR, CLI_SERVICES_DIRECTORY, GLOBAL_DB_IMAGE
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
    get_current_fm_version,
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

    def entrypoint_checks(self, start=False):
        if not self.path.exists():
            try:
                self.output.print(
                    f"Creating global services [blue]{', '.join(self.compose_file_manager.get_services_list())}[/blue].",
                    emoji_code=":construction:",
                )
                self.create(clean_install=True)

            except Exception as e:
                self.output.error("Error during service creation", e)
                import traceback

                traceback.print_exc()
                raise ServicesNotCreated(
                    f"Not able to create global services [blue]{', '.join(self.compose_file_manager.get_services_list())}[/blue].",
                ) from e

            # Pull images
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

        if start:
            if not self.invoked_subcommand == "service":
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
            DatabaseServerServiceInfo.import_from_compose_file("global-db", self.compose_file_manager),
            self.compose_file_manager,
            self.docker_client,
            output_handler=self.output,
        )

    def init(self):
        # check if the global services exits if not then create
        # TODO this should be done by factory
        current_system = platform.system()

        template_name = "docker-compose.services.tmpl"
        if current_system == "Darwin":
            template_name = "docker-compose.services.osx.tmpl"

        self.compose_file_manager = ComposeFile(self.compose_path, template_name=template_name)
        self.docker_client = DockerClient(compose_file_path=self.compose_path, output=self.output)

        self.proxy_storage = ProxyStoragePaths("global-nginx-proxy", self.compose_file_manager)
        self.nginx_controller = NginxController("global-nginx-proxy", self.compose_file_manager, self.docker_client)

        # For backward compatibility
        # TODO: Remove this when all code is updated
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
        if self.fm_headers_path.parent.exists():
            template_path: Path = get_template_path("fm_headers.conf.tmpl")
            template = Template(template_path.read_text())
            output = template.render(current_version=f"v{get_current_fm_version()}")
            self.fm_headers_path.write_text(output)

    def create(self, backup: bool = False, clean_install: bool = True):
        envs = {
            "global-db": {
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
                "global-db": {
                    "uid": os.getuid(),
                    "gid": os.getgid(),
                },
            }

            if not current_system == "Darwin":
                user["global-nginx-proxy"] = {
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

        # create required directories
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

        # set secrets in compose
        self.generate_compose(inputs)

        # Ensure network configuration (subnet + proxy IP) is set. Only the
        # frontend network is auto-sized to dodge host subnet clashes; the
        # backend network keeps its fixed subnet from the template.
        fm_config = FMConfigManager.import_from_toml()
        if not fm_config.network.configured:
            # Reuse the network if it's already running (e.g. from a previous setup)
            running = detect_running_network("fm-global-frontend-network", docker=self.docker_client)
            if running:
                subnet_cidr = running["subnet_cidr"]
                # The proxy may not be attached yet; pick a free IP in the subnet
                # instead of persisting an empty address.
                proxy_ip = running["proxy_ip"] or pick_proxy_ip(subnet_cidr, "fm-global-frontend-network")
                fm_config.network.subnet_cidr = subnet_cidr
                fm_config.network.proxy_ip = proxy_ip
                fm_config.export_to_toml()
                self.output.print(f"Detected running network: {subnet_cidr}, proxy at {proxy_ip}")
            else:
                self.output.change_head("Configuring global frontend network")
                used_subnets = get_docker_network_subnets()
                cidr = find_available_subnet(used_subnets)
                net_config = compute_network_config(str(cidr), "fm-global-frontend-network")
                fm_config.network.subnet_cidr = net_config["subnet_cidr"]
                fm_config.network.proxy_ip = net_config["proxy_ip"]
                fm_config.export_to_toml()
                self.output.print(f"Assigned subnet {net_config['subnet_cidr']}, proxy IP {net_config['proxy_ip']}")

        # Set the subnet in the compose YAML
        if fm_config.network.subnet_cidr:
            try:
                self.compose_file_manager.yml["networks"]["global-frontend-network"]["ipam"]["config"][0][
                    "subnet"
                ] = fm_config.network.subnet_cidr
            except (KeyError, IndexError):
                pass

        # Pin the proxy's static IP without dropping any other networks it's on
        if fm_config.network.proxy_ip:
            try:
                proxy_service = self.compose_file_manager.yml["services"]["global-nginx-proxy"]
                nets = proxy_service.get("networks")
                if isinstance(nets, list):
                    nets = {name: {} for name in nets}
                elif not isinstance(nets, dict):
                    nets = {}
                entry = nets.get("global-frontend-network")
                if not isinstance(entry, dict):
                    entry = {}
                entry["ipv4_address"] = fm_config.network.proxy_ip
                nets["global-frontend-network"] = entry
                proxy_service["networks"] = nets
            except KeyError:
                pass

        if current_system == "Darwin":
            self.compose_file_manager.remove_container_user("global-nginx-proxy")
            self.compose_file_manager.remove_container_user("global-db")
        else:
            dirs_to_create.append("mariadb/data")

        # create dirs
        for folder in dirs_to_create:
            temp_dir = self.path / folder
            try:
                temp_dir.mkdir(parents=True, exist_ok=True)
            except Exception as e:
                raise ServicesNotCreated(f"Failed to create global services required dir {temp_dir.absolute()}.")

        # populate secrets for db
        db_password_path = self.path / "secrets" / "db_password.txt"
        db_root_password_path = self.path / "secrets" / "db_root_password.txt"

        db_password_path.write_text(random_password_generate(password_length=16, symbols=True))
        db_root_password_path.write_text(random_password_generate(password_length=24, symbols=True))

        # populate mariadb config
        mariadb_conf = self.path / "mariadb/conf"
        mariadb_conf = str(mariadb_conf.absolute())
        host_run_cp(
            image=GLOBAL_DB_IMAGE,
            source="/etc/mysql/.",
            destination=mariadb_conf,
            docker=self.docker_client,
        )

        self.set_frappe_headers_conf()

        self.compose_file_manager.set_secret_file_path("db_password", str(db_password_path.absolute()))
        self.compose_file_manager.set_secret_file_path("db_root_password", str(db_root_password_path.absolute()))
        self.compose_file_manager.write_to_file()

        if clean_install:
            # remove previous contaniners and volumes
            self.docker_client.compose.down(remove_orphans=True, timeout=10, volumes=True, stream=False)

    def exists(self):
        return (self.path / "docker-compose.yml").exists()

    def generate_compose(self, inputs: dict):
        # TODO do something about this function
        try:
            # Extract inputs
            environments = inputs.get("environment")
            labels = inputs.get("labels")
            users = None

            if "user" in inputs:
                users = {}
                for container_name, user_data in inputs["user"].items():
                    users[container_name] = (user_data["uid"], user_data["gid"])

            # Use fluent interface to set all configurations atomically
            cf = self.compose_file_manager
            if environments:
                cf.with_envs(environments)
            if labels:
                cf.with_labels(labels)
            if users:
                cf.with_users(users)

            # Commit changes if any were made
            if environments or labels or users:
                cf.commit()

        # TODO do something about this exception
        except Exception as e:
            raise ServicesNotCreated("Not able to generate global services compose file.")

    def shell(self, container: str, user: str | None = None):
        self.output.stop()
        shell_path = "/bin/bash"
        try:
            if user:
                self.docker_client.compose.exec(container, user=user, command=shell_path, capture_output=False)
            else:
                self.docker_client.compose.exec(container, command=shell_path, capture_output=False)
        except DockerException as e:
            self.output.warning(f"Shell exited with error code: {e.output.exit_code}")

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
