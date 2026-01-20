import os
import platform
import shutil
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

from jinja2 import Template

from frappe_manager import CLI_DIR, CLI_SERVICES_DIRECTORY
from frappe_manager.docker import ComposeFile, DockerClient, DockerException
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
    check_and_display_port_status,
    get_current_fm_version,
    get_template_path,
    get_unix_groups,
    random_password_generate,
)


class ServicesManager:
    def __init__(
        self,
        path=CLI_SERVICES_DIRECTORY,
        verbose: bool = False,
        invoked_subcommand: Optional[str] = None,
        output_handler: OutputHandler | None = None,
    ) -> None:
        self.path = path
        self.quiet = not verbose
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
                self.path.mkdir(parents=True, exist_ok=True)
                self.create(clean_install=True)

            except Exception as e:
                raise ServicesNotCreated(
                    f"Not able to create global services [blue]{', '.join(self.compose_file_manager.get_services_list())}[/blue].",
                )

            # Pull images
            output = self.docker_client.compose.pull(stream=False)

            self.output.print(
                f"Created global services [blue]{', '.join(self.compose_file_manager.get_services_list())}[/blue].",
            )

            if start:
                output = self.docker_client.compose.up(services=[], detach=True, pull="never", stream=self.quiet)
                if self.quiet:
                    self.output.live_lines(output, padding=(0, 0, 0, 2))

        if not self.compose_path.exists():
            raise ServicesComposeNotExist(
                f"Seems like global services has taken a down. No compose file found at {self.compose_path}.",
            )

        if start:
            if not self.invoked_subcommand == "service":
                services = self.compose_file_manager.get_services_list()
                containers = self.compose_file_manager.get_container_names().values()
                all_statuses = self.docker_client.compose.get_all_services_status()
                running_statuses = {
                    status["Service"]: status["State"] for status in all_statuses if status.get("Name") in containers
                }
                all_running = all(running_statuses.get(s) == "running" for s in services)

                if not all_running:
                    self.are_ports_free()
                    self.output.print(
                        f"Started non running global services [blue]{', '.join(self.compose_file_manager.get_services_list())}[/blue].",
                    )
                    output = self.docker_client.compose.up(services=[], detach=True, pull="never", stream=self.quiet)
                    if self.quiet:
                        self.output.live_lines(output, padding=(0, 0, 0, 2))

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
        self.docker_client = DockerClient(compose_file_path=self.compose_path)

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

        if backup:
            if self.path.exists():
                backup_path: Path = CLI_DIR / "backups"
                backup_path.mkdir(parents=True, exist_ok=True)
                current_time = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
                backup_dir_name = f"services_{current_time}"
                self.path.rename(backup_path / backup_dir_name)

        shutil.rmtree(self.path)

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
            image="mariadb:10.6",
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

            # Convert user format if present
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
        """Start services."""
        services = services or []
        output = self.docker_client.compose.up(
            services=services,
            detach=True,
            pull="never",
            force_recreate=force_recreate,
            stream=self.quiet,
        )
        if self.quiet:
            self.output.live_lines(output, padding=(0, 0, 0, 2))

    def stop_service(self, services: list[str] | None = None, timeout: int = 10):
        """Stop services."""
        services = services or []
        output = self.docker_client.compose.stop(services=services, timeout=timeout, stream=self.quiet)
        if self.quiet:
            self.output.live_lines(output, padding=(0, 0, 0, 2))

    def restart_service(self, services: list[str] | None = None):
        """Restart services."""
        services = services or []
        output = self.docker_client.compose.restart(services=services, stream=self.quiet)
        if self.quiet:
            self.output.live_lines(output, padding=(0, 0, 0, 2))

    def are_ports_free(self):
        ports = [80, 443]
        self.output.change_head(f"Verifying ports {', '.join(map(str, ports))} availability")
        docker_used_ports = []
        services = self.compose_file_manager.get_services_list()
        for service in services:
            ports_config = self.compose_file_manager.yml.get("services", {}).get(service, {}).get("ports", [])
            for port in ports_config:
                if isinstance(port, str) and ":" in port:
                    host_port = port.split(":")[0]
                    try:
                        docker_used_ports.append(int(host_port))
                    except ValueError:
                        pass
        check_and_display_port_status(ports, exclude=docker_used_ports)
        self.output.print(f"Global services will utilize ports {', '.join(map(str, ports))}")
