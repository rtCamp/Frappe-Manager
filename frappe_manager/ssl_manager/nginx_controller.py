"""
Controls nginx process operations (reload/restart).

This module separates nginx control operations from configuration reading,
following the Single Responsibility Principle and improving testability.
"""

from frappe_manager.compose_project.compose_project import ComposeProject
from frappe_manager.display_manager.DisplayManager import richprint


class NginxController:
    """
    Controls nginx process operations.
    
    This class is responsible only for controlling the nginx process
    (reload, restart). It does not handle configuration or path management.
    
    Attributes:
        service_name: Name of the nginx service in docker-compose
        compose_project: The compose project containing the service
    """
    
    def __init__(
        self,
        service_name: str,
        compose_project: ComposeProject,
    ):
        """
        Initialize the nginx controller.
        
        Args:
            service_name: Name of the nginx service (e.g., 'nginx', 'nginx-proxy')
            compose_project: The compose project containing the nginx service
        """
        self.service_name = service_name
        self.compose_project = compose_project
    
    def reload(self):
        """
        Reload nginx configuration without stopping the service.
        
        This sends a SIGHUP signal to nginx, causing it to reload its
        configuration files without interrupting active connections.
        """
        richprint.change_head("Reloading nginx")
        
        if self.compose_project.running:
            output = self.compose_project.docker.compose.exec(
                service=self.service_name, command='nginx -s reload', stream=False
            )
            richprint.print("Reloaded nginx.")
    
    def restart(self):
        """
        Restart the nginx service.
        
        This completely stops and starts the nginx container, which will
        interrupt active connections but ensures a clean restart.
        """
        richprint.change_head("Restarting nginx")
        
        if self.compose_project.running:
            output = self.compose_project.docker.compose.restart(services=[self.service_name], stream=False)
            richprint.print("Restarting nginx.")
