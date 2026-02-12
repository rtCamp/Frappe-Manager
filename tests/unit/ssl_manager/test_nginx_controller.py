"""
Tests for frappe_manager.ssl_manager.nginx_controller module.

This module tests the NginxController class which handles nginx process
control operations (reload and restart).
"""

from frappe_manager.ssl_manager.nginx_controller import NginxController


class TestNginxControllerInitialization:
    """Tests for NginxController initialization."""

    def test_init_stores_service_name_and_compose_project(self, mock_compose_file_manager, mock_docker_client):
        """Test that initialization stores the service name and compose file manager."""
        controller = NginxController("nginx-proxy", mock_compose_file_manager, mock_docker_client)

        assert controller.service_name == "nginx-proxy"
        assert controller.compose_file_manager == mock_compose_file_manager
        assert controller.docker_client == mock_docker_client

    def test_init_with_different_service_name(self, mock_compose_file_manager, mock_docker_client):
        """Test initialization with a different service name."""
        controller = NginxController("custom-nginx", mock_compose_file_manager, mock_docker_client)

        assert controller.service_name == "custom-nginx"
        assert controller.compose_file_manager == mock_compose_file_manager
        assert controller.docker_client == mock_docker_client


class TestNginxControllerReload:
    """Tests for NginxController.reload() method."""

    def test_reload_executes_nginx_command_when_running(self, mocker, mock_compose_file_manager, mock_docker_client):
        """Test that reload executes nginx -s reload for regular nginx when running."""
        mock_output = mocker.Mock()

        controller = NginxController(
            "nginx",
            mock_compose_file_manager,
            mock_docker_client,
            output_handler=mock_output,
        )
        controller.reload()

        mock_output.change_head.assert_called_once_with("Reloading nginx")
        mock_output.print.assert_called_once_with("Reloaded nginx")

        mock_docker_client.compose.exec.assert_called_once_with(
            service="nginx",
            command="nginx -s reload",
            stream=False,
        )

    def test_reload_uses_hup_for_global_nginx_proxy(self, mocker, mock_compose_file_manager, mock_docker_client):
        """Test that reload uses kill -HUP 1 for global-nginx-proxy to trigger docker-gen."""
        mock_output = mocker.Mock()

        controller = NginxController(
            "global-nginx-proxy",
            mock_compose_file_manager,
            mock_docker_client,
            output_handler=mock_output,
        )
        controller.reload()

        mock_output.change_head.assert_called_once_with("Reloading nginx")
        mock_output.print.assert_called_once_with("Reloaded nginx")

        mock_docker_client.compose.exec.assert_called_once_with(
            service="global-nginx-proxy",
            command="sh -c 'kill -HUP 1'",
            stream=False,
        )

    def test_reload_does_not_execute_when_not_running(self, mocker, mock_compose_file_manager, mock_docker_client):
        """Test that reload does not execute docker command when compose is not running."""
        mock_output = mocker.Mock()
        mock_docker_client.compose.is_service_running.return_value = False

        controller = NginxController(
            "nginx-proxy",
            mock_compose_file_manager,
            mock_docker_client,
            output_handler=mock_output,
        )
        controller.reload()

        mock_output.change_head.assert_called_once_with("Reloading nginx")
        mock_docker_client.compose.exec.assert_not_called()
        mock_output.print.assert_not_called()

    def test_reload_uses_correct_service_name(self, mocker, mock_compose_file_manager, mock_docker_client):
        """Test that reload uses the correct service name from initialization."""
        mock_output = mocker.Mock()

        controller = NginxController(
            "custom-nginx-service",
            mock_compose_file_manager,
            mock_docker_client,
            output_handler=mock_output,
        )
        controller.reload()

        mock_docker_client.compose.is_service_running.assert_called_once_with("custom-nginx-service")
        mock_docker_client.compose.exec.assert_called_once_with(
            service="custom-nginx-service",
            command="nginx -s reload",
            stream=False,
        )


class TestNginxControllerRestart:
    """Tests for NginxController.restart() method."""

    def test_restart_executes_docker_restart_when_running(self, mocker, mock_compose_file_manager, mock_docker_client):
        """Test that restart executes docker compose restart when compose is running."""
        mock_output = mocker.Mock()

        controller = NginxController(
            "nginx-proxy",
            mock_compose_file_manager,
            mock_docker_client,
            output_handler=mock_output,
        )
        controller.restart()

        # Verify output handler was called for status updates
        mock_output.change_head.assert_called_once_with("Restarting nginx")
        mock_output.print.assert_called_once_with("Restarting nginx")

        # Verify docker restart was called with correct parameters
        mock_docker_client.compose.restart.assert_called_once_with(services=["nginx-proxy"], stream=False)

    def test_restart_does_not_execute_when_not_running(self, mocker, mock_compose_file_manager, mock_docker_client):
        """Test that restart does not execute docker command when compose is not running."""
        mock_output = mocker.Mock()
        mock_docker_client.compose.is_service_running.return_value = False

        controller = NginxController(
            "nginx-proxy",
            mock_compose_file_manager,
            mock_docker_client,
            output_handler=mock_output,
        )
        controller.restart()

        mock_output.change_head.assert_called_once_with("Restarting nginx")
        mock_docker_client.compose.restart.assert_not_called()
        mock_output.print.assert_not_called()

    def test_restart_uses_correct_service_name(self, mocker, mock_compose_file_manager, mock_docker_client):
        """Test that restart uses the correct service name from initialization."""
        mock_output = mocker.Mock()

        controller = NginxController(
            "my-nginx",
            mock_compose_file_manager,
            mock_docker_client,
            output_handler=mock_output,
        )
        controller.restart()

        mock_docker_client.compose.is_service_running.assert_called_once_with("my-nginx")
        mock_docker_client.compose.restart.assert_called_once_with(services=["my-nginx"], stream=False)


class TestNginxControllerStreamParameter:
    """Tests verifying that stream=False is consistently used."""

    def test_reload_always_uses_stream_false(self, mocker, mock_compose_file_manager, mock_docker_client):
        """Test that reload always passes stream=False to docker exec."""
        mock_output = mocker.Mock()

        controller = NginxController(
            "nginx-proxy",
            mock_compose_file_manager,
            mock_docker_client,
            output_handler=mock_output,
        )
        controller.reload()

        # Verify stream=False is passed
        call_kwargs = mock_docker_client.compose.exec.call_args.kwargs
        assert "stream" in call_kwargs
        assert call_kwargs["stream"] is False

    def test_restart_always_uses_stream_false(self, mocker, mock_compose_file_manager, mock_docker_client):
        """Test that restart always passes stream=False to docker restart."""
        mock_output = mocker.Mock()

        controller = NginxController(
            "nginx-proxy",
            mock_compose_file_manager,
            mock_docker_client,
            output_handler=mock_output,
        )
        controller.restart()

        # Verify stream=False is passed
        call_kwargs = mock_docker_client.compose.restart.call_args.kwargs
        assert "stream" in call_kwargs
        assert call_kwargs["stream"] is False
