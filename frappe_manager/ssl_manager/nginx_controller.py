"""
Controls nginx process operations (reload/restart).

This module separates nginx control operations from configuration reading,
following the Single Responsibility Principle and improving testability.
"""

import time

from frappe_manager.docker import ComposeFile, DockerClient, DockerException
from frappe_manager.output_manager import OutputHandler
from frappe_manager.output_manager.rich_output import RichOutputHandler


class NginxController:
    """
    Controls nginx process operations.

    This class is responsible only for controlling the nginx process
    (reload, restart). It does not handle configuration or path management.

    Attributes:
        service_name: Name of the nginx service in docker-compose
        compose_file_manager: The compose file manager
        docker_client: The docker client for operations
    """

    def __init__(
        self,
        service_name: str,
        compose_file_manager: ComposeFile,
        docker_client: DockerClient,
        output_handler: OutputHandler | None = None,
    ):
        """
        Initialize the nginx controller.

        Args:
            service_name: Name of the nginx service (e.g., 'nginx', 'nginx-proxy')
            compose_file_manager: The compose file manager
            docker_client: The docker client for operations
            output_handler: Optional output handler for display operations
        """
        self.service_name = service_name
        self.compose_file_manager = compose_file_manager
        self.docker_client = docker_client
        self.output = output_handler or RichOutputHandler()

    def reload(self) -> bool:
        """
        Reload nginx configuration without stopping the service.

        For jwilder/nginx-proxy, PID 1 is forego, which treats SIGHUP as
        shutdown: signaling it restarts the whole container and drops every
        bench it fronts for seconds. Instead, signal docker-gen directly (it
        re-renders default.conf and notifies nginx when the render changed)
        and follow with a graceful nginx reload to also cover vhost.d
        content-only edits, which leave default.conf byte-identical.
        Regular nginx uses the standard reload signal.

        Returns True when nginx was actually reloaded, False when the service is not
        running or every reload attempt failed, so callers that report success to the
        operator can tell the difference instead of claiming a reload that never happened.
        """
        self.output.change_head("Reloading nginx")

        if self.docker_client.compose.is_service_running(self.service_name):
            reloaded = True
            if self.service_name == "global-nginx-proxy":
                self.docker_client.compose.exec(
                    service=self.service_name,
                    command="sh -c 'kill -HUP $(pidof docker-gen)'",
                    stream=False,
                )
                # Follow-up graceful reload for vhost.d content-only edits
                # (those leave default.conf byte-identical, so docker-gen sends
                # no notify). When default.conf DID change, docker-gen may be
                # mid-write here and the reload fails its config read; that is
                # harmless (nginx keeps the old config and docker-gen's own
                # notify reloads it), so retry briefly and then let it go.
                for attempt in range(3):
                    try:
                        self.docker_client.compose.exec(
                            service=self.service_name, command="nginx -s reload", stream=False
                        )
                        break
                    except DockerException:
                        if attempt == 2:
                            reloaded = False
                            self.output.warning(
                                "nginx reload raced the docker-gen render; docker-gen's own notify completes the reload"
                            )
                        else:
                            time.sleep(0.5)
            else:
                self.docker_client.compose.exec(service=self.service_name, command="nginx -s reload", stream=False)
            if reloaded:
                self.output.print("Reloaded nginx")
            return reloaded
        return False

    def restart(self):
        """
        Restart the nginx service.

        This completely stops and starts the nginx container, which will
        interrupt active connections but ensures a clean restart.
        """
        self.output.change_head("Restarting nginx")

        if self.docker_client.compose.is_service_running(self.service_name):
            output = self.docker_client.compose.restart(services=[self.service_name], stream=False)
            self.output.print("Restarting nginx")
