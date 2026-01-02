"""
Tests for frappe_manager.ssl_manager.nginx_controller module.

This module tests the NginxController class which handles nginx process
control operations (reload and restart).
"""

import pytest
from frappe_manager.ssl_manager.nginx_controller import NginxController


class TestNginxControllerInitialization:
    """Tests for NginxController initialization."""
    
    def test_init_stores_service_name_and_compose_project(self, mock_compose_project):
        """Test that initialization stores the service name and compose project."""
        controller = NginxController('nginx-proxy', mock_compose_project)
        
        assert controller.service_name == 'nginx-proxy'
        assert controller.compose_project == mock_compose_project
    
    def test_init_with_different_service_name(self, mock_compose_project):
        """Test initialization with a different service name."""
        controller = NginxController('custom-nginx', mock_compose_project)
        
        assert controller.service_name == 'custom-nginx'
        assert controller.compose_project == mock_compose_project


class TestNginxControllerReload:
    """Tests for NginxController.reload() method."""
    
    def test_reload_executes_nginx_command_when_running(self, mocker, mock_compose_project):
        """Test that reload executes nginx -s reload when compose is running."""
        mock_richprint = mocker.patch('frappe_manager.ssl_manager.nginx_controller.richprint')
        mock_compose_project.running = True
        mock_compose_project.docker.compose.exec.return_value = "nginx reloaded"
        
        controller = NginxController('nginx-proxy', mock_compose_project)
        controller.reload()
        
        # Verify richprint was called for status updates
        mock_richprint.change_head.assert_called_once_with("Reloading nginx")
        mock_richprint.print.assert_called_once_with("Reloaded nginx.")
        
        # Verify docker exec was called with correct parameters
        mock_compose_project.docker.compose.exec.assert_called_once_with(
            service='nginx-proxy',
            command='nginx -s reload',
            stream=False
        )
    
    def test_reload_does_not_execute_when_not_running(self, mocker, mock_compose_project):
        """Test that reload does not execute docker command when compose is not running."""
        mock_richprint = mocker.patch('frappe_manager.ssl_manager.nginx_controller.richprint')
        mock_compose_project.running = False
        
        controller = NginxController('nginx-proxy', mock_compose_project)
        controller.reload()
        
        # Verify richprint was called for status update
        mock_richprint.change_head.assert_called_once_with("Reloading nginx")
        
        # Verify docker exec was NOT called
        mock_compose_project.docker.compose.exec.assert_not_called()
        
        # Verify success message was NOT printed
        mock_richprint.print.assert_not_called()
    
    def test_reload_uses_correct_service_name(self, mocker, mock_compose_project):
        """Test that reload uses the correct service name from initialization."""
        mock_richprint = mocker.patch('frappe_manager.ssl_manager.nginx_controller.richprint')
        mock_compose_project.running = True
        
        controller = NginxController('custom-nginx-service', mock_compose_project)
        controller.reload()
        
        # Verify docker exec was called with the custom service name
        mock_compose_project.docker.compose.exec.assert_called_once_with(
            service='custom-nginx-service',
            command='nginx -s reload',
            stream=False
        )


class TestNginxControllerRestart:
    """Tests for NginxController.restart() method."""
    
    def test_restart_executes_docker_restart_when_running(self, mocker, mock_compose_project):
        """Test that restart executes docker compose restart when compose is running."""
        mock_richprint = mocker.patch('frappe_manager.ssl_manager.nginx_controller.richprint')
        mock_compose_project.running = True
        mock_compose_project.docker.compose.restart.return_value = "service restarted"
        
        controller = NginxController('nginx-proxy', mock_compose_project)
        controller.restart()
        
        # Verify richprint was called for status updates
        mock_richprint.change_head.assert_called_once_with("Restarting nginx")
        mock_richprint.print.assert_called_once_with("Restarting nginx.")
        
        # Verify docker restart was called with correct parameters
        mock_compose_project.docker.compose.restart.assert_called_once_with(
            services=['nginx-proxy'],
            stream=False
        )
    
    def test_restart_does_not_execute_when_not_running(self, mocker, mock_compose_project):
        """Test that restart does not execute docker command when compose is not running."""
        mock_richprint = mocker.patch('frappe_manager.ssl_manager.nginx_controller.richprint')
        mock_compose_project.running = False
        
        controller = NginxController('nginx-proxy', mock_compose_project)
        controller.restart()
        
        # Verify richprint was called for status update
        mock_richprint.change_head.assert_called_once_with("Restarting nginx")
        
        # Verify docker restart was NOT called
        mock_compose_project.docker.compose.restart.assert_not_called()
        
        # Verify success message was NOT printed
        mock_richprint.print.assert_not_called()
    
    def test_restart_uses_correct_service_name(self, mocker, mock_compose_project):
        """Test that restart uses the correct service name from initialization."""
        mock_richprint = mocker.patch('frappe_manager.ssl_manager.nginx_controller.richprint')
        mock_compose_project.running = True
        
        controller = NginxController('my-nginx', mock_compose_project)
        controller.restart()
        
        # Verify docker restart was called with the custom service name
        mock_compose_project.docker.compose.restart.assert_called_once_with(
            services=['my-nginx'],
            stream=False
        )


class TestNginxControllerStreamParameter:
    """Tests verifying that stream=False is consistently used."""
    
    def test_reload_always_uses_stream_false(self, mocker, mock_compose_project):
        """Test that reload always passes stream=False to docker exec."""
        mocker.patch('frappe_manager.ssl_manager.nginx_controller.richprint')
        mock_compose_project.running = True
        
        controller = NginxController('nginx-proxy', mock_compose_project)
        controller.reload()
        
        # Verify stream=False is passed
        call_kwargs = mock_compose_project.docker.compose.exec.call_args.kwargs
        assert 'stream' in call_kwargs
        assert call_kwargs['stream'] is False
    
    def test_restart_always_uses_stream_false(self, mocker, mock_compose_project):
        """Test that restart always passes stream=False to docker restart."""
        mocker.patch('frappe_manager.ssl_manager.nginx_controller.richprint')
        mock_compose_project.running = True
        
        controller = NginxController('nginx-proxy', mock_compose_project)
        controller.restart()
        
        # Verify stream=False is passed
        call_kwargs = mock_compose_project.docker.compose.restart.call_args.kwargs
        assert 'stream' in call_kwargs
        assert call_kwargs['stream'] is False
