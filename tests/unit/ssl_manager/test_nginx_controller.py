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

    def test_reload_signals_dockergen_then_nginx_for_global_proxy(
        self, mocker, mock_compose_file_manager, mock_docker_client
    ):
        """The global proxy must NEVER be reloaded via PID 1: forego treats
        HUP as shutdown and the whole container restarts, dropping every
        bench. docker-gen gets the HUP (re-render), then nginx reloads
        gracefully to cover vhost.d content-only edits."""
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

        calls = mock_docker_client.compose.exec.call_args_list
        assert len(calls) == 2
        assert calls[0].kwargs == {
            "service": "global-nginx-proxy",
            "command": "sh -c 'kill -HUP $(pidof docker-gen)'",
            "stream": False,
        }
        assert calls[1].kwargs == {
            "service": "global-nginx-proxy",
            "command": "nginx -s reload",
            "stream": False,
        }

    def test_reload_retries_when_nginx_reload_races_dockergen(
        self, mocker, mock_compose_file_manager, mock_docker_client
    ):
        """A follow-up nginx reload can catch docker-gen mid-write of
        default.conf and fail its config read; reload() must retry instead of
        raising (docker-gen's own notify covers the changed-file case)."""
        from frappe_manager.docker import DockerException
        from frappe_manager.docker.subprocess_output import SubprocessOutput

        mocker.patch("frappe_manager.ssl_manager.nginx_controller.time.sleep")
        mock_output = mocker.Mock()
        raced = DockerException(
            ["docker", "compose", "exec"],
            SubprocessOutput(stdout=[], stderr=["pread() returned only 2195 bytes"], combined=[], exit_code=1),
        )
        mock_docker_client.compose.exec.side_effect = [None, raced, None]

        controller = NginxController(
            "global-nginx-proxy",
            mock_compose_file_manager,
            mock_docker_client,
            output_handler=mock_output,
        )
        controller.reload()

        # HUP + failed reload + successful retry
        assert mock_docker_client.compose.exec.call_count == 3
        mock_output.warning.assert_not_called()
        mock_output.print.assert_called_once_with("Reloaded nginx")

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
