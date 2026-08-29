"""
BenchDockerOps - Docker and Compose Operations Module

This module handles all Docker and docker-compose operations for a bench.
Extracted from the monolithic Bench class for better separation of concerns.
"""

import os
import shlex
import shutil
import sys
import tempfile
from collections.abc import Iterable, Iterator
from pathlib import Path
from typing import Any, Literal, cast

from frappe_manager import CLI_DEFAULT_DELIMETER, CLI_SERVICES_DIRECTORY
from frappe_manager.docker import DOCKER_LINE_NOISE, DockerClient, DockerException
from frappe_manager.docker.compose_file import ComposeFile
from frappe_manager.docker.subprocess_output import SubprocessOutput
from frappe_manager.logger import get_logger
from frappe_manager.output_manager import OutputHandler
from frappe_manager.output_manager.rich_output import RichOutputHandler
from frappe_manager.site_manager import NON_BASH_SUPPORTED_SERVICES
from frappe_manager.site_manager.bench_config import BenchConfig, BenchRuntime
from frappe_manager.utils.docker import host_run_cp
from frappe_manager.utils.helpers import get_container_name_prefix, get_current_fm_version
from frappe_manager.utils.network import get_proxy_ip_on_frontend


class BenchDockerOps:
    """Handles all Docker and compose operations for a bench."""

    def __init__(
        self,
        docker_client: DockerClient,
        compose_file_manager: ComposeFile,
        config: BenchConfig,
        path: Path,
        output_handler: OutputHandler | None = None,
    ):
        """
        Initialize BenchDockerOps.

        Args:
            docker_client: Docker client for operations
            compose_file_manager: Compose file manager
            config: Bench configuration
            path: Path to bench directory
            output_handler: Handler for output operations
        """
        self.logger = get_logger(component="docker")
        self.docker_client = docker_client
        self.compose_file_manager = compose_file_manager
        self.config = config
        self.path = path
        self.output = output_handler or RichOutputHandler()

    def _is_service_running(self, service: str) -> bool:
        """Check if a specific service is running."""
        try:
            all_statuses = self.docker_client.compose.get_all_services_status()
            return any(status["Service"] == service and status["State"] == "running" for status in all_statuses)
        except DockerException:
            return False

    def is_running(self) -> bool:
        """Check if all bench services are running.

        Services suppressed via the ``disabled`` compose profile are excluded: fm
        deliberately never starts them (a bench on an external redis), so counting
        them would report a healthy bench as broken.
        """
        try:
            services = self.compose_file_manager.get_services_list(exclude_disabled=True)
            containers = self.compose_file_manager.get_container_names().values()
            all_statuses = self.docker_client.compose.get_all_services_status()
            running_statuses = {
                status["Service"]: status["State"] for status in all_statuses if status.get("Name") in containers
            }
            return all(running_statuses.get(s) == "running" for s in services)
        except DockerException:
            return False

    def get_services_running_status(self) -> dict:
        """Get the running status of all services fm actually starts.

        Suppressed services are left out rather than reported as not running: a
        leftover container from before the service was disabled must not turn up
        in the status of a bench that is doing exactly what it was configured to.
        """
        try:
            services = set(self.compose_file_manager.get_services_list(exclude_disabled=True))
            containers = self.compose_file_manager.get_container_names().values()
            all_statuses = self.docker_client.compose.get_all_services_status()
            return {
                status["Service"]: status["State"]
                for status in all_statuses
                if status.get("Name") in containers and status["Service"] in services
            }
        except DockerException:
            return {}

    def generate_compose(self, inputs: dict) -> None:
        """
        Generate the compose file for the bench based on the given inputs.

        Args:
            inputs: Dictionary containing environment, labels, users, etc.
        """
        # Extract inputs
        environments = inputs.get("environment")
        labels = inputs.get("labels")
        users = None

        if "user" in inputs:
            users = {}
            for container_name, user_data in inputs["user"].items():
                users[container_name] = (user_data["uid"], user_data["gid"])

        network_aliases = [self.config.name]
        if self.config.alias_domains:
            network_aliases.extend(self.config.alias_domains)

        # No domain aliases on bench nginx — internal DNS resolution for all domains
        # is handled via extra_hosts (pointing to the global proxy).
        # The proxy discovers domains via VIRTUAL_HOST env var, not network aliases.

        # Add extra_hosts for the primary domain and alias domains pointing to the
        # global nginx proxy. The proxy IP is read live from Docker so it's always
        # correct even if the proxy was recreated after a restart.
        proxy_ip = get_proxy_ip_on_frontend()

        if proxy_ip:
            all_domains = [self.config.name]
            if self.config.alias_domains:
                all_domains.extend(self.config.alias_domains)
            extra_hosts = [f"{domain}:{proxy_ip}" for domain in all_domains]
            for service in ["frappe", "socketio", "schedule"]:
                self.compose_file_manager.set_extrahosts(service, extra_hosts)

        # For dev SSL (self-signed certs), mount the CA cert into containers
        # that make outbound HTTPS requests, so they trust the dev cert.
        # Production Let's Encrypt certs are trusted by default and don't need this.
        from frappe_manager.docker import DockerVolumeMount, DockerVolumeType
        from frappe_manager.ssl_manager import SUPPORTED_SSL_TYPES

        has_dev_ssl = any(cert.ssl_type == SUPPORTED_SSL_TYPES.dev for cert in self.config.ssl_certificates)
        if has_dev_ssl:
            ca_cert_host = CLI_SERVICES_DIRECTORY / "nginx-proxy" / "ssl" / "dev" / "ca" / "rootCA.pem"
            if ca_cert_host.exists():
                container_ca_path = "/etc/ssl/certs/fm-dev-ca.pem"
                ca_services = ["frappe", "socketio", "schedule"]
                for svc in ca_services:
                    # Mount the CA cert
                    vols = self.compose_file_manager.get_service_volumes(svc)
                    vols.append(
                        DockerVolumeMount(
                            host=str(ca_cert_host),
                            container=container_ca_path,
                            type=DockerVolumeType.bind,
                            compose_path=self.compose_file_manager.compose_path,
                        )
                    )
                    self.compose_file_manager.set_service_volumes(svc, vols)
                    # Set runtime-specific env var for CA trust
                    envs = self.compose_file_manager.get_envs(svc) or {}
                    # Node.js apps (socketio) or any process that uses NODE_EXTRA_CA_CERTS
                    envs["NODE_EXTRA_CA_CERTS"] = container_ca_path
                    # Python requests library honors this env var
                    envs["REQUESTS_CA_BUNDLE"] = container_ca_path
                    self.compose_file_manager.set_envs(svc, envs, append=True)

        # Use configure_bench method to set all configurations atomically
        self.compose_file_manager.configure_bench(
            prefix=get_container_name_prefix(network_aliases[0]),
            version=get_current_fm_version(),
            envs=environments,
            labels=labels,
            users=users,
            network_name="site-network",
            auto_save=False,
        )

        restart_policy = inputs.get("restart_policy", "no")
        self.compose_file_manager.set_all_services_restart(restart_policy)

        # Mode shape (image + code-service volumes) is a pure projection of
        # bench_config via compose_shape -- the same specs deploy re-pins use,
        # so every writer produces the identical shape (create/update/deploy).
        from frappe_manager.site_manager.modules.compose_shape import apply_specs, bench_service_specs

        apply_specs(self.compose_file_manager, bench_service_specs(self.config), self.config.name)
        self.compose_file_manager.write_to_file()

    def render_image_compose(self, deploy_tag: str, rolling: bool = False) -> str:
        """Re-pin the bench compose to ``deploy_tag`` (deploy/switch/rollback).

        Thin delegator over the compose_shape projection -- the same specs
        ``generate_compose`` uses, with ``deploy_tag`` as the candidate tag (so
        deploy shapes the NEW tag without mutating deploy_state mid-pipeline).
        ``rolling=True`` sheds container_name on the scaled web services so
        ``compose up --scale`` is accepted; the canonical render restores them.
        Returns the paired nginx assets tag. Idempotent.
        """
        from frappe_manager.site_manager.bench_config import BenchRuntime
        from frappe_manager.site_manager.modules.bake import BakeManager
        from frappe_manager.site_manager.modules.compose_shape import (
            RenderContext,
            apply_specs,
            bench_service_specs,
        )

        if self.config.runtime != BenchRuntime.image:
            raise ValueError("render_image_compose is only valid for image runtime")

        specs = bench_service_specs(self.config, RenderContext(deploy_tag=deploy_tag, rolling=rolling))
        apply_specs(self.compose_file_manager, specs, self.config.name)

        # Rolling swap: shed container_name on the scaled web
        # services so `compose up --scale <svc>=2` is accepted; the canonical
        # (non-rolling) render restores them so get_container_names() keeps
        # working between deploys. The fm-sockets mount stays: the entrypoint
        # rewrites supervisord to /fm-sockets/<svc>.sock and clears stale
        # sockets, so the new replica takes over the canonical socket
        # (last-writer-wins) during the overlap.
        services = self.compose_file_manager.get_services_list()
        prefix = get_container_name_prefix(self.config.name)
        for spec in specs:
            if not spec.rolling or spec.name not in services:
                continue
            if rolling:
                self.compose_file_manager.remove_container_name(spec.name)
            else:
                self.compose_file_manager.set_container_name(spec.name, f"{prefix}{CLI_DEFAULT_DELIMETER}{spec.name}")

        self.compose_file_manager.write_to_file()
        self.output.print(f"Rendered image-mode compose pinned to {deploy_tag}")
        return BakeManager.nginx_image_tag(deploy_tag)

    def _seed_nginx_conf(self, conf_dir: Path, nginx_image: str) -> None:
        """Lay the nginx image's `/etc/nginx` onto the host without clobbering fm's overlays.

        `docker cp <dir> <existing dir>` nests instead of merging: copying `/etc/nginx` straight
        onto a `conf/` that already holds `custom/real-ip.conf` produces `conf/nginx/nginx.conf`,
        which nginx never reads. So the image content lands in a scratch directory first and is
        merged in file by file, and anything already on the host wins. That keeps fm's own
        `custom/` and `http_auth/` overlays intact and makes the whole step idempotent.
        """
        conf_dir.mkdir(parents=True, exist_ok=True)

        with tempfile.TemporaryDirectory(dir=conf_dir.parent) as scratch:
            staged = Path(scratch) / "conf"
            host_run_cp(
                nginx_image,
                source="/etc/nginx",
                destination=str(staged),
                docker=self.docker_client,
            )
            for item in sorted(staged.rglob("*")):
                target = conf_dir / item.relative_to(staged)
                # `modules` in the nginx image is a symlink to /usr/lib/nginx/modules, which does
                # not exist inside the staged copy. Recreate the link itself rather than following
                # it: `is_dir()` and `copy2()` both resolve the target and blow up on a dangling
                # one. `exists()` is also False for a broken link, hence the explicit is_symlink.
                if item.is_symlink():
                    if not target.is_symlink() and not target.exists():
                        target.parent.mkdir(parents=True, exist_ok=True)
                        os.symlink(os.readlink(item), target)
                elif item.is_dir():
                    target.mkdir(parents=True, exist_ok=True)
                elif not target.exists():
                    target.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(item, target)

    def create_compose_dirs(self, copy_runtimes: bool = True) -> bool:
        """
        Create the necessary directories for the Compose setup.

        Returns:
            True if directories are created successfully
        """
        self.output.change_head("Creating required directories")

        workspace_path = self.path / "workspace"
        workspace_path.mkdir(parents=True, exist_ok=True)

        frappe_bench_dir = workspace_path / "frappe-bench"
        frappe_bench_dir.mkdir(parents=True, exist_ok=True)

        (frappe_bench_dir / "sites").mkdir(parents=True, exist_ok=True)
        (frappe_bench_dir / "logs").mkdir(parents=True, exist_ok=True)
        (frappe_bench_dir / "config").mkdir(parents=True, exist_ok=True)
        (frappe_bench_dir / "config" / "pids").mkdir(parents=True, exist_ok=True)
        # Image runtime ships app code in the image; the host has no bind-mounted apps/.
        if self.config.runtime != BenchRuntime.image:
            (frappe_bench_dir / "apps").mkdir(parents=True, exist_ok=True)

        apps_txt = frappe_bench_dir / "sites" / "apps.txt"
        if not apps_txt.exists():
            apps_txt.write_text("frappe\n")

        common_site_config = frappe_bench_dir / "sites" / "common_site_config.json"
        if not common_site_config.exists():
            common_site_config.write_text("{}")

        configs_path = self.path / "configs"
        configs_path.mkdir(parents=True, exist_ok=True)

        # create nginx dirs
        nginx_dir = configs_path / "nginx"
        nginx_dir.mkdir(parents=True, exist_ok=True)

        # The bind mount replaces /etc/nginx wholesale, so the image's base config has to be on
        # the host or nginx has nothing to read and dies with `nginx.conf` not found. This used to
        # be guarded on `conf/` not existing, which broke the moment anything else wrote into that
        # directory first: `ensure_fm_nginx_confs` lays down `conf/custom/real-ip.conf` during
        # `generate_compose`, which runs BEFORE this, so the directory existed, the copy was
        # skipped, and every new bench came up with a dead web server. Guard on the marker file
        # rather than the directory, so the seeding is independent of who got there first, and a
        # bench already broken this way repairs itself on the next run.
        nginx_image = self.compose_file_manager.yml["services"]["nginx"]["image"]
        nginx_conf_dir = nginx_dir / "conf"

        if not (nginx_conf_dir / "nginx.conf").exists():
            self._seed_nginx_conf(nginx_conf_dir, nginx_image)

        nginx_subdirs = ["logs", "cache", "run", "html"]

        for directory in nginx_subdirs:
            new_dir = nginx_dir / directory
            new_dir.mkdir(parents=True, exist_ok=True)

        # Copy prebaked Python and Node from Docker image to host workspace.
        # Skipped in image mode: .uv/.fnm live in the app image (data-only binds).
        if copy_runtimes:
            frappe_image = self.compose_file_manager.yml["services"]["frappe"]["image"]

            # Copy prebaked UV Python installations
            uv_dir = workspace_path / "frappe-bench" / ".uv"
            if not uv_dir.exists():
                uv_dir_abs = str(uv_dir.absolute())
                host_run_cp(
                    frappe_image,
                    source="/workspace/frappe-bench/.uv",
                    destination=uv_dir_abs,
                    docker=self.docker_client,
                )

            # Copy prebaked FNM Node installations
            fnm_dir = workspace_path / "frappe-bench" / ".fnm"
            if not fnm_dir.exists():
                fnm_dir_abs = str(fnm_dir.absolute())
                host_run_cp(
                    frappe_image,
                    source="/workspace/frappe-bench/.fnm",
                    destination=fnm_dir_abs,
                    docker=self.docker_client,
                )

        self.output.print("Created all required directories")

        return True

    def start(
        self,
        services: list | None = None,
        force_recreate: bool = False,
        pull: Literal["missing", "never", "always"] = "never",
    ) -> None:
        """
        Start bench services.

        Args:
            services: List of specific services to start (None for all)
            force_recreate: Force recreate containers
            pull: Pull policy (never, always, missing)
        """
        self.output.change_head("Starting bench services")

        self.docker_client.compose.up(services=services or [], detach=True, pull=pull, force_recreate=force_recreate)

        self.output.print("Started bench services")

    def stop(self, timeout: int = 10) -> None:
        """
        Stop bench services.

        Args:
            timeout: Timeout in seconds for stopping containers
        """
        self.output.change_head("Stopping bench services")
        self.docker_client.compose.stop(services=[], timeout=timeout)
        self.output.print("Stopped bench services")

    def remove_containers(self, remove_volumes: bool = True, timeout: int = 5) -> None:
        """
        Remove bench containers.

        Args:
            remove_volumes: Whether to remove volumes
            timeout: Timeout for removal
        """
        if self.compose_file_manager.exists():
            self.output.change_head("Removing bench containers")
            output = self.docker_client.compose.down(
                remove_orphans=True,
                volumes=remove_volumes,
                timeout=timeout,
                stream=True,
            )
            self.output.live_lines(
                cast("Iterator[tuple[str, bytes]]", output), padding=(0, 0, 0, 2), line_filters=DOCKER_LINE_NOISE
            )
            self.output.print("Removed bench containers")
        else:
            self.output.warning("Bench compose file not found. Skipping containers removal.")

    def shell(
        self,
        compose_service: str,
        user: str | None = None,
        shell_path: str | None = None,
        use_run: bool = False,
        site: str | None = None,
    ) -> None:
        """
        Spawn a shell for the specified service.

        Args:
            compose_service: The name of the service
            user: The name of the user (defaults to "frappe" for frappe service)
            shell_path: Path to shell executable (overrides auto-detection)
            use_run: Use 'docker compose run --rm' instead of 'docker compose exec'
            site: Exported into the container as FRAPPE_SITE, which sits above
                common_site_config's default_site in Frappe's own resolution, so bare
                `bench` commands in the shell target this site.
        """
        self.output.change_head("Spawning shell")

        if compose_service == "frappe" and not user:
            user = "frappe"

        if not use_run and not self._is_service_running(compose_service):
            self.output.display_error(f"Cannot spawn shell. Compose service '{compose_service}' not running!")
            return

        self.output.stop()

        if not shell_path:
            shell_path = "/bin/bash" if compose_service not in NON_BASH_SUPPORTED_SERVICES else "sh"

        if use_run:
            run_cmd = self.docker_client.compose.docker_compose_cmd + [
                "run",
                "--rm",
                "--entrypoint",
                "/exec-entrypoint.sh",
            ]
            if site:
                run_cmd += ["--env", f"FRAPPE_SITE={site}"]
            # Use lightweight exec-entrypoint.sh that only handles UID/GID mismatch.
            # It never cds and the image's WORKDIR is /workspace, one level above
            # the bench, so the frappe service needs the same --workdir exec gets.
            if compose_service == "frappe":
                run_cmd += ["--workdir", "/workspace/frappe-bench"]
            run_cmd += [compose_service, shell_path]

            import os

            os.execvp(run_cmd[0], run_cmd)
        else:
            exec_cmd = self.docker_client.compose.docker_compose_cmd + ["exec"]

            if site:
                exec_cmd += ["--env", f"FRAPPE_SITE={site}"]

            if user:
                exec_cmd += ["--user", user]

            if compose_service == "frappe":
                exec_cmd += ["--workdir", "/workspace/frappe-bench"]

            exec_cmd += [compose_service, shell_path]

            import os

            os.execvp(exec_cmd[0], exec_cmd)

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
            shell_path: Path to shell executable (overrides auto-detection)
            use_run: Use 'docker compose run --rm' instead of 'docker compose exec'
            site: Exported into the container as FRAPPE_SITE, which sits above
                common_site_config's default_site in Frappe's own resolution, so a bare
                `bench` command in `command` targets this site.

        Returns:
            Exit code of the executed command
        """
        if compose_service == "frappe" and not user:
            user = "frappe"

        if not use_run and not self._is_service_running(compose_service):
            self.output.display_error(f"Cannot execute command. Compose service '{compose_service}' not running!")
            return 1

        if not shell_path:
            shell_path = "/bin/bash" if compose_service not in NON_BASH_SUPPORTED_SERVICES else "sh"

        if use_run:
            run_args: dict[str, Any] = {
                "service": compose_service,
                "command": f"{shell_path} -c {shlex.quote(command)}",
                "rm": True,
                "use_shlex_split": True,
                "stream": False,
                "entrypoint": "/exec-entrypoint.sh",
                # Use lightweight exec-entrypoint.sh that only handles UID/GID mismatch
            }
            # exec-entrypoint.sh never cds and the image's WORKDIR is /workspace, one
            # level above the bench, so the frappe service needs the same --workdir
            # the exec branch below passes -- otherwise `bench ...` runs from the wrong
            # directory and fails.
            if compose_service == "frappe":
                run_args["workdir"] = "/workspace/frappe-bench"

            if site:
                run_args["env"] = [f"FRAPPE_SITE={site}"]

            try:
                result = cast("SubprocessOutput", self.docker_client.compose.run(**run_args))

                if result.stdout:
                    for line in result.stdout:
                        print(line)
                if result.stderr:
                    for line in result.stderr:
                        print(line, file=sys.stderr)

                return result.exit_code
            except DockerException as e:
                if e.output.stdout:
                    for line in e.output.stdout:
                        print(line)
                if e.output.stderr:
                    for line in e.output.stderr:
                        print(line, file=sys.stderr)
                return e.output.exit_code
        else:
            exec_args: dict[str, Any] = {
                "service": compose_service,
                "command": f"{shell_path} -c {shlex.quote(command)}",
                "stream": False,
                "capture_output": True,
                "use_shlex_split": True,
            }

            if compose_service == "frappe":
                exec_args["workdir"] = "/workspace/frappe-bench"

            if user:
                exec_args["user"] = user

            if site:
                exec_args["env"] = [f"FRAPPE_SITE={site}"]

            try:
                result = self.docker_client.compose.exec(**exec_args)

                if result.stdout:
                    for line in result.stdout:
                        print(line)
                if result.stderr:
                    for line in result.stderr:
                        print(line, file=sys.stderr)

                return result.exit_code
            except DockerException as e:
                if e.output.stdout:
                    for line in e.output.stdout:
                        print(line)
                if e.output.stderr:
                    for line in e.output.stderr:
                        print(line, file=sys.stderr)
                return e.output.exit_code

    def logs(self, services: list | None = None, follow: bool = False) -> None:
        """
        Display logs for services.

        Args:
            services: List of services to show logs for (None for all)
            follow: Whether to follow logs continuously
        """
        self.output.change_head("Showing logs")

        services_list = services or []
        if services_list and not self._is_service_running(services_list[0]):
            # Raise rather than print-and-return: this exited 0 after telling the user it could show
            # nothing, so a caller could not distinguish empty logs from a container that was down.
            from frappe_manager.site_manager.exceptions import BenchServiceNotRunning

            bench_name = self.config.container_name_prefix.replace("-", ".")
            raise BenchServiceNotRunning(bench_name, services_list[0])

        output = self.docker_client.compose.logs(services=services_list, follow=follow, stream=True)
        self.output.live_lines(
            cast("Iterator[tuple[str, bytes]]", output), padding=(0, 0, 0, 2), line_filters=DOCKER_LINE_NOISE
        )

    def frappe_logs_till_start(self) -> None:
        """
        Retrieve and print the logs of the 'frappe' service until supervisor starts.
        """
        output = cast(
            "Iterable[tuple[str, bytes]]",
            self.docker_client.compose.logs(
                services=["frappe"],
                no_log_prefix=True,
                no_color=True,
                follow=True,
                stream=True,
            ),
        )

        self.output.live_lines(
            cast("Iterator[tuple[str, bytes]]", output),
            padding=(0, 0, 0, 2),
            stop_string="INFO supervisord started with pid",
            line_filters=DOCKER_LINE_NOISE,
        )

    def restart_services(self, services: list, force: bool = False) -> None:
        """
        Restart specific services.

        Services suppressed via the ``disabled`` compose profile are dropped rather
        than restarted: docker compose cannot address a service outside the active
        profiles, and fm never started it anyway (a bench on an external redis).

        Args:
            services: List of service names to restart
            force: If True, use timeout=0 for immediate kill. If False, use default graceful timeout.
        """
        enabled = [s for s in services if not self.compose_file_manager.is_service_profile_disabled(s)]
        if not enabled:
            return
        timeout = 0 if force else 100
        self.output.change_head(f"Restarting services - {' '.join(enabled)}")
        self.docker_client.compose.restart(services=enabled, timeout=timeout)
        action = "Force restarted" if force else "Restarted"
        self.output.print(f"{action} services - {' '.join(enabled)}")

    def exec_command(self, service: str, command: str, user: str | None = None, stream: bool = False):
        """
        Execute a command in a service container.

        Args:
            service: Service name
            command: Command to execute
            user: User to run as
            stream: Whether to stream output

        Returns:
            Command output
        """
        exec_args = {"service": service, "command": command, "stream": stream}

        if user:
            exec_args["user"] = user

        return self.docker_client.compose.exec(**exec_args)

    def check_required_docker_images_available(self) -> None:
        """
        Check if all required Docker images are available locally.

        This method verifies that all images needed for the bench are
        present on the system before attempting to start containers.

        Raises:
            BenchOperationRequiredDockerImagesNotAvailable: If any required images are missing

        Example:
            >>> docker_ops.check_required_docker_images_available()
        """
        from frappe_manager.site_manager.exceptions import BenchOperationRequiredDockerImagesNotAvailable
        from frappe_manager.utils.site import get_all_docker_images

        self.output.change_head("Checking required docker images availability")
        fm_images = get_all_docker_images()
        system_available_images = self.docker_client.images()

        not_available_images = []

        for key, value in fm_images.items():
            name = value["name"]
            tag = value["tag"]

            found = False

            for item in system_available_images:
                if item.get("Repository") == name and item.get("Tag") == tag:
                    found = True
                    break

            if not found:
                image = f"{name}:{tag}"
                not_available_images.append(image)

        not_available_images = list(dict.fromkeys(not_available_images))

        if not_available_images:
            for image in not_available_images:
                self.output.display_error(f"Docker image '{image}' is not available locally")

            bench_name = self.config.container_name_prefix.replace("-", ".")
            raise BenchOperationRequiredDockerImagesNotAvailable(bench_name, "fm self update-images")
