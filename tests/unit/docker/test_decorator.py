"""
Unit tests for the @docker_command decorator.

These tests verify that the decorator correctly:
- Handles positional and keyword parameters
- Converts parameters to docker CLI options
- Manages the 'stream' parameter
- Executes commands correctly
- Picks the AUTO-STREAM branch only when the caller left `stream` unset: with an output
  handler and no explicit `stream=`, the raw (source, line) iterator must be tee'd so the
  lines are displayed live AND still returned as a materialized SubprocessOutput. An
  explicit `stream=True` is the caller taking ownership of the iterator, so the decorator
  must hand it back untouched and never display it itself.
"""

from typing import ClassVar
from unittest.mock import patch

import pytest

from frappe_manager.docker.docker_compose import DockerComposeWrapper, docker_command
from frappe_manager.docker.subprocess_output import SubprocessOutput


class TestDockerCommandDecorator:
    """Test the @docker_command decorator functionality"""

    def test_decorator_basic_usage(self):
        """Test that decorator can be applied to a method"""

        @docker_command("test")
        def test_method(self):
            pass

        assert callable(test_method)
        assert test_method.__name__ == "test_method"

    def test_decorator_with_positional_params(self):
        """Test decorator handles positional parameters correctly"""

        class MockWrapper:
            docker_compose_cmd = ["docker", "compose", "-f", "test.yml"]

        @docker_command("up", positional_params=["services"])
        def up(self, services: list[str] = [], detach: bool = True, stream: bool = False):
            pass

        # Mock the run_command_with_exit_code function
        with patch("frappe_manager.docker.docker_compose.run_command_with_exit_code") as mock_run:
            mock_run.return_value = iter([])

            wrapper = MockWrapper()
            up(wrapper, services=["web", "db"], detach=True)

            # Verify the command was built correctly
            called_cmd = mock_run.call_args[0][0]
            assert called_cmd[0:4] == ["docker", "compose", "-f", "test.yml"]
            assert "up" in called_cmd
            assert "web" in called_cmd
            assert "db" in called_cmd
            assert "--detach" in called_cmd

    def test_decorator_with_keyword_params(self):
        """Test decorator converts keyword parameters to options"""

        class MockWrapper:
            docker_compose_cmd = ["docker", "compose", "-f", "test.yml"]

        @docker_command("down")
        def down(self, timeout: int = 100, volumes: bool = False, stream: bool = False):
            pass

        with patch("frappe_manager.docker.docker_compose.run_command_with_exit_code") as mock_run:
            mock_run.return_value = iter([])

            wrapper = MockWrapper()
            down(wrapper, timeout=30, volumes=True)

            called_cmd = mock_run.call_args[0][0]
            assert "down" in called_cmd
            assert "--timeout" in called_cmd
            assert 30 in called_cmd  # Integer value, not string
            assert "--volumes" in called_cmd

    def test_decorator_handles_stream_parameter(self):
        """Test that stream parameter is passed correctly"""

        class MockWrapper:
            docker_compose_cmd = ["docker", "compose", "-f", "test.yml"]
            output = None  # Add output attribute for decorator

        @docker_command("logs")
        def logs(self, follow: bool = False, stream: bool = False):
            pass

        with patch("frappe_manager.docker.docker_compose.run_command_with_exit_code") as mock_run:
            mock_run.return_value = iter([])

            wrapper = MockWrapper()
            logs(wrapper, follow=True, stream=True)

            # Check that stream was passed to run_command_with_exit_code
            assert mock_run.call_args[1]["stream"] is True

            # Check that stream is NOT in the command
            called_cmd = mock_run.call_args[0][0]
            assert "--stream" not in called_cmd

    def test_decorator_with_exclude_params(self):
        """Test that exclude_params prevents parameters from being added to command"""

        class MockWrapper:
            docker_compose_cmd = ["docker", "compose", "-f", "test.yml"]

        @docker_command("exec", exclude_params=["use_shlex_split"], positional_params=["service"])
        def exec(self, service: str, command: str, use_shlex_split: bool = True, stream: bool = False):
            pass

        with patch("frappe_manager.docker.docker_compose.run_command_with_exit_code") as mock_run:
            mock_run.return_value = iter([])

            wrapper = MockWrapper()
            exec(wrapper, service="web", command="ls", use_shlex_split=False)

            called_cmd = mock_run.call_args[0][0]
            assert "exec" in called_cmd
            assert "web" in called_cmd
            # use_shlex_split should NOT appear in the command
            assert "--use-shlex-split" not in called_cmd
            assert "--use_shlex_split" not in called_cmd

    def test_decorator_with_list_positional_param(self):
        """Test that list positional parameters are expanded"""

        class MockWrapper:
            docker_compose_cmd = ["docker", "compose", "-f", "test.yml"]

        @docker_command("restart", positional_params=["services"])
        def restart(self, services: list[str] = [], stream: bool = False):
            pass

        with patch("frappe_manager.docker.docker_compose.run_command_with_exit_code") as mock_run:
            mock_run.return_value = iter([])

            wrapper = MockWrapper()
            restart(wrapper, services=["web", "db", "redis"])

            called_cmd = mock_run.call_args[0][0]
            assert "restart" in called_cmd
            assert "web" in called_cmd
            assert "db" in called_cmd
            assert "redis" in called_cmd

    def test_decorator_with_empty_list_positional(self):
        """Test that empty list positional params don't add anything"""

        class MockWrapper:
            docker_compose_cmd = ["docker", "compose", "-f", "test.yml"]

        @docker_command("up", positional_params=["services"])
        def up(self, services: list[str] = [], stream: bool = False):
            pass

        with patch("frappe_manager.docker.docker_compose.run_command_with_exit_code") as mock_run:
            mock_run.return_value = iter([])

            wrapper = MockWrapper()
            up(wrapper, services=[])

            called_cmd = mock_run.call_args[0][0]
            assert "up" in called_cmd
            # Command should be just: ["docker", "compose", "-f", "test.yml", "up"]
            assert called_cmd.count("up") == 1

    def test_decorator_preserves_function_metadata(self):
        """Test that decorator preserves function name and docstring"""

        @docker_command("test")
        def my_function(self):
            """This is a test docstring"""

        assert my_function.__name__ == "my_function"
        assert my_function.__doc__ == "This is a test docstring"

    def test_decorator_with_default_values(self):
        """Test that decorator applies default values correctly"""

        class MockWrapper:
            docker_compose_cmd = ["docker", "compose", "-f", "test.yml"]

        @docker_command("up")
        def up(self, detach: bool = True, build: bool = False, stream: bool = False):
            pass

        with patch("frappe_manager.docker.docker_compose.run_command_with_exit_code") as mock_run:
            mock_run.return_value = iter([])

            wrapper = MockWrapper()
            # Call without specifying detach (should use default True)
            up(wrapper)

            called_cmd = mock_run.call_args[0][0]
            assert "--detach" in called_cmd
            assert "--build" not in called_cmd  # False default, shouldn't appear

    def test_decorator_with_none_positional_param(self):
        """Test that None positional parameters are not added"""

        class MockWrapper:
            docker_compose_cmd = ["docker", "compose", "-f", "test.yml"]

        @docker_command("ps", positional_params=["service"])
        def ps(self, service: list[str] = None, stream: bool = False):
            pass

        with patch("frappe_manager.docker.docker_compose.run_command_with_exit_code") as mock_run:
            mock_run.return_value = iter([])

            wrapper = MockWrapper()
            ps(wrapper, service=None)

            called_cmd = mock_run.call_args[0][0]
            assert "ps" in called_cmd
            # Should not have any service name after 'ps'
            assert called_cmd[-1] == "ps"


class _RecordingOutput:
    """Minimal OutputHandler stand-in that records live_lines() and drains its iterator."""

    should_stream_docker = True

    def __init__(self):
        self.live_lines_kwargs: list[dict] = []
        self.displayed: list[tuple[str, bytes]] = []

    def live_lines(self, iterator, **kwargs):
        self.live_lines_kwargs.append(kwargs)
        self.displayed.extend(iterator)


class _StreamWrapper:
    docker_compose_cmd: ClassVar[list[str]] = ["docker", "compose", "-f", "test.yml"]

    def __init__(self, output):
        self.output = output


@docker_command("up")
def _up(self, detach: bool = True, stream: bool | None = None):
    pass


DOCKER_LINES = [("stdout", b"creating\n"), ("stderr", b"warn\n"), ("exit_code", b"0")]


class TestAutoStreamingBranch:
    """`self.output and stream_param is None` decides tee-and-materialize vs. hand back the iterator."""

    @pytest.mark.timeout(15)
    def test_unset_stream_tees_to_live_lines_and_returns_subprocess_output(self):
        output = _RecordingOutput()
        wrapper = _StreamWrapper(output)

        with patch("frappe_manager.docker.docker_compose.run_command_with_exit_code") as mock_run:
            mock_run.return_value = iter(DOCKER_LINES)
            result = _up(wrapper)

        # The handler decided to stream, so the subprocess was asked for a live iterator...
        assert mock_run.call_args[1]["stream"] is True
        # ...it was displayed live...
        assert len(output.live_lines_kwargs) == 1
        assert output.live_lines_kwargs[0]["padding"] == (0, 0, 0, 2)
        assert output.displayed == DOCKER_LINES
        # ...and the caller still got a fully materialized result, not an iterator.
        assert isinstance(result, SubprocessOutput)
        assert result.stdout == ["creating\n"]
        assert result.stderr == ["warn\n"]
        assert result.exit_code == 0

    @pytest.mark.timeout(15)
    def test_explicit_stream_true_returns_iterator_untouched(self):
        output = _RecordingOutput()
        wrapper = _StreamWrapper(output)

        with patch("frappe_manager.docker.docker_compose.run_command_with_exit_code") as mock_run:
            mock_run.return_value = iter(DOCKER_LINES)
            result = _up(wrapper, stream=True)

        # The caller owns the iterator: nothing may consume or display it behind their back.
        assert not output.live_lines_kwargs
        assert not output.displayed
        assert not isinstance(result, SubprocessOutput)
        assert list(result) == DOCKER_LINES

    @pytest.mark.timeout(15)
    def test_explicit_stream_true_without_output_handler_returns_iterator(self):
        wrapper = _StreamWrapper(None)

        with patch("frappe_manager.docker.docker_compose.run_command_with_exit_code") as mock_run:
            mock_run.return_value = iter(DOCKER_LINES)
            result = _up(wrapper, stream=True)

        assert not isinstance(result, SubprocessOutput)
        assert list(result) == DOCKER_LINES

    @pytest.mark.timeout(15)
    def test_unset_stream_without_output_handler_does_not_stream(self):
        wrapper = _StreamWrapper(None)

        with patch("frappe_manager.docker.docker_compose.run_command_with_exit_code") as mock_run:
            mock_run.return_value = SubprocessOutput([], [], [], 0)
            result = _up(wrapper)

        assert mock_run.call_args[1]["stream"] is False
        assert result is mock_run.return_value


class TestCaptureOutputForwarding:
    """`capture_output` is a transport flag, so the decorator -- not the argv builder -- owns it.

    It is in every subcommand's exclude list (it must never become a `--capture-output`
    flag), and the decorator used to end with a bare `run_command_with_exit_code(full_cmd,
    stream=False)`. The runner then defaulted to capturing, so an interactive
    `docker compose exec <svc> /bin/bash` (ServicesManager.shell) had its stdio piped into
    a SubprocessOutput that is only returned after the shell exits: a dead terminal.
    """

    @pytest.fixture
    def wrapper(self, tmp_path):
        compose_file = tmp_path / "docker-compose.yml"
        compose_file.write_text("services: {}\n")
        # No output handler: the non-streaming tail of the decorator is what is under test.
        return DockerComposeWrapper(path=compose_file)

    @pytest.mark.timeout(15)
    def test_capture_output_false_reaches_the_subprocess_runner(self, wrapper):
        with patch("frappe_manager.docker.docker_compose.run_command_with_exit_code") as mock_run:
            mock_run.return_value = None
            wrapper.exec("global-db", command="/bin/bash", capture_output=False)

        args, kwargs = mock_run.call_args
        assert kwargs == {"stream": False, "capture_output": False}
        # ...and it still is not a compose option.
        assert "--capture-output" not in args[0]
        assert args[0][-3:] == ["exec", "global-db", "/bin/bash"]

    @pytest.mark.timeout(15)
    def test_capture_output_defaults_to_capturing(self, wrapper):
        with patch("frappe_manager.docker.docker_compose.run_command_with_exit_code") as mock_run:
            mock_run.return_value = SubprocessOutput([], [], [], 0)
            wrapper.exec("global-db", command="ls")

        assert mock_run.call_args[1]["capture_output"] is True

    @pytest.mark.timeout(15)
    def test_subcommands_without_the_parameter_keep_the_bare_runner_call(self, wrapper):
        """Only methods that declare `capture_output` forward it; `down` has no such knob."""
        with patch("frappe_manager.docker.docker_compose.run_command_with_exit_code") as mock_run:
            mock_run.return_value = SubprocessOutput([], [], [], 0)
            wrapper.down(stream=False)

        assert mock_run.call_args[1] == {"stream": False}


class TestDecoratorIntegrationWithDockerCompose:
    """Test decorator integration with actual DockerComposeWrapper (with mocking)"""

    @pytest.fixture
    def temp_compose_file(self, tmp_path):
        """Create a temporary compose file for testing"""
        compose_file = tmp_path / "docker-compose.yml"
        compose_file.write_text("""
version: '3'
services:
  web:
    image: nginx
  db:
    image: postgres
""")
        return compose_file

    def test_decorator_works_with_real_wrapper(self, temp_compose_file):
        """Test that decorator works with DockerComposeWrapper instance"""

        wrapper = DockerComposeWrapper(path=temp_compose_file)

        # Verify the wrapper has the expected command structure
        assert wrapper.docker_compose_cmd == [
            "docker",
            "compose",
            "-f",
            str(temp_compose_file.absolute()),
        ]
