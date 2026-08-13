"""Characterization tests for ``BenchAppManager._container_run``.

``_container_run`` is a 4-way matrix -- (``compose.run`` when ``use_run`` else
``compose.exec``) x (``capture_output`` returning a filtered ``SubprocessOutput``
else streaming into ``output.live_lines``) -- plus a fifth path: ``use_run`` with
a ``provision_image`` set short-circuits to ``_run_in_provision_image``. The four
compose branches differ only in a couple of keyword arguments, so they are
obvious merge candidates; this file pins the observable behaviour of each so a
later dedup cannot silently change it.

The asymmetry most at risk when merging: the ``run`` path folds the working
directory into the shell string (``/bin/bash -c 'cd {workdir} && {command}'``)
and passes ``rm``/``entrypoint`` but no ``workdir``/``user``, while the ``exec``
path passes ``workdir``/``user`` as compose options and wraps the command
verbatim (``/bin/bash -c '{command}'``). Both are pinned exactly, kwarg by kwarg.

Everything below the method is mocked at the seam: ``docker_client``, ``output``
and ``bench_config``. No docker daemon, no filesystem, no network.
"""

# ruff: noqa: SLF001 -- the units under test are private methods of BenchAppManager.

from unittest.mock import MagicMock

import pytest

from frappe_manager.docker import DOCKER_LINE_NOISE, DockerException
from frappe_manager.docker.subprocess_output import SubprocessOutput
from frappe_manager.site_manager.exceptions import BenchOperationException
from frappe_manager.site_manager.modules.bench_app import BenchAppManager

TRICKY_SCRIPTS = [
    "bench --site all migrate",
    'echo "hello world"',
    "ls && echo done",
    "python3 -c 'print(1)'",
]

BENCH_NAME = "example.localhost"
DEFAULT_WORKDIR = "/workspace/frappe-bench"
ENTRYPOINT = "/exec-entrypoint.sh"


def _make_manager(provision_image=None, external_db=False):
    """A BenchAppManager whose docker/output/config collaborators are mocks.

    ``external_db=False`` makes ``_site_env()`` empty (no MYSQL_HOME), which is
    the common case; a MagicMock bench_config would otherwise return a truthy
    database config and inject MYSQL_HOME into every assertion.
    """
    manager = BenchAppManager.__new__(BenchAppManager)
    manager.bench_name = BENCH_NAME
    manager.docker_client = MagicMock()
    manager.output = MagicMock()
    manager.bench_config = MagicMock()
    manager.bench_config.get_database_config.return_value = MagicMock() if external_db else None
    manager.provision_image = provision_image
    manager.logger = MagicMock()
    return manager


def _output(combined=None, exit_code=0):
    lines = ["ok"] if combined is None else combined
    return SubprocessOutput(stdout=list(lines), stderr=[], combined=list(lines), exit_code=exit_code)


class TestUseRunCapture:
    """use_run=True, capture_output=True -> compose.run(stream=False), filtered output returned."""

    def test_calls_compose_run_only(self):
        manager = _make_manager()
        manager.docker_client.compose.run.return_value = _output()

        manager._container_run("bench build", capture_output=True, use_run=True)

        assert manager.docker_client.compose.run.call_count == 1
        manager.docker_client.compose.exec.assert_not_called()
        manager.output.live_lines.assert_not_called()

    def test_exact_kwargs(self):
        manager = _make_manager()
        manager.docker_client.compose.run.return_value = _output()

        manager._container_run("bench build", capture_output=True, use_run=True)

        call = manager.docker_client.compose.run.call_args
        assert call.args == ()
        assert call.kwargs == {
            "service": "frappe",
            "command": "/bin/bash -c 'cd /workspace/frappe-bench && bench build'",
            "rm": True,
            "stream": False,
            "entrypoint": ENTRYPOINT,
            "env": None,
        }

    def test_workdir_is_folded_into_the_command_not_passed_as_an_option(self):
        manager = _make_manager()
        manager.docker_client.compose.run.return_value = _output()

        manager._container_run("bench build", capture_output=True, use_run=True, workdir="/opt/elsewhere")

        kwargs = manager.docker_client.compose.run.call_args.kwargs
        assert kwargs["command"] == "/bin/bash -c 'cd /opt/elsewhere && bench build'"
        assert "workdir" not in kwargs
        assert "user" not in kwargs

    def test_user_is_ignored_on_the_run_path(self):
        manager = _make_manager()
        manager.docker_client.compose.run.return_value = _output()

        manager._container_run("bench build", capture_output=True, use_run=True, user="root")

        assert "user" not in manager.docker_client.compose.run.call_args.kwargs

    def test_service_is_forwarded(self):
        manager = _make_manager()
        manager.docker_client.compose.run.return_value = _output()

        manager._container_run("bench build", capture_output=True, use_run=True, service="worker")

        assert manager.docker_client.compose.run.call_args.kwargs["service"] == "worker"

    def test_returns_the_docker_output_object_with_warnings_filtered(self):
        manager = _make_manager()
        raw = _output(
            combined=[
                'time="2024-01-01T00:00:00Z" level=warning msg="network default not found"',
                "real line",
                '  time="2024-01-01T00:00:00Z" level=error msg="indented noise"',
            ],
            exit_code=3,
        )
        manager.docker_client.compose.run.return_value = raw

        result = manager._container_run("bench build", capture_output=True, use_run=True)

        assert result is raw
        assert result.combined == ["real line"]
        assert result.exit_code == 3
        # Only `combined` is filtered; stdout/stderr are handed back untouched.
        assert len(result.stdout) == 3


class TestUseRunStream:
    """use_run=True, capture_output=False -> compose.run(stream=True) piped to live_lines."""

    def test_calls_compose_run_only(self):
        manager = _make_manager()
        manager.docker_client.compose.run.return_value = iter([])

        manager._container_run("bench build", use_run=True)

        assert manager.docker_client.compose.run.call_count == 1
        manager.docker_client.compose.exec.assert_not_called()

    def test_exact_kwargs(self):
        manager = _make_manager()
        manager.docker_client.compose.run.return_value = iter([])

        manager._container_run("bench build", use_run=True, workdir="/opt/elsewhere")

        call = manager.docker_client.compose.run.call_args
        assert call.args == ()
        assert call.kwargs == {
            "service": "frappe",
            "command": "/bin/bash -c 'cd /opt/elsewhere && bench build'",
            "rm": True,
            "entrypoint": ENTRYPOINT,
            "env": None,
            "stream": True,
        }

    def test_streams_the_iterator_verbatim_with_noise_filters(self):
        manager = _make_manager()
        stream = iter([("stdout", b"line\n")])
        manager.docker_client.compose.run.return_value = stream

        manager._container_run("bench build", use_run=True)

        manager.output.live_lines.assert_called_once_with(stream, line_filters=DOCKER_LINE_NOISE)

    def test_returns_none(self):
        manager = _make_manager()
        manager.docker_client.compose.run.return_value = iter([])

        assert manager._container_run("bench build", use_run=True) is None


class TestExecCapture:
    """use_run=False, capture_output=True -> compose.exec(stream=False), filtered output returned."""

    def test_calls_compose_exec_only(self):
        manager = _make_manager()
        manager.docker_client.compose.exec.return_value = _output()

        manager._container_run("bench build", capture_output=True)

        assert manager.docker_client.compose.exec.call_count == 1
        manager.docker_client.compose.run.assert_not_called()
        manager.output.live_lines.assert_not_called()

    def test_exact_kwargs(self):
        manager = _make_manager()
        manager.docker_client.compose.exec.return_value = _output()

        manager._container_run("bench build", capture_output=True)

        call = manager.docker_client.compose.exec.call_args
        assert call.args == ()
        assert call.kwargs == {
            "service": "frappe",
            "command": "/bin/bash -c 'bench build'",
            "user": "frappe",
            "workdir": DEFAULT_WORKDIR,
            "env": None,
            "stream": False,
        }

    def test_workdir_is_a_compose_option_and_never_a_cd_prefix(self):
        manager = _make_manager()
        manager.docker_client.compose.exec.return_value = _output()

        manager._container_run("bench build", capture_output=True, workdir="/opt/elsewhere", user="root")

        kwargs = manager.docker_client.compose.exec.call_args.kwargs
        assert kwargs["workdir"] == "/opt/elsewhere"
        assert kwargs["user"] == "root"
        assert kwargs["command"] == "/bin/bash -c 'bench build'"
        assert "cd " not in kwargs["command"]
        assert "rm" not in kwargs
        assert "entrypoint" not in kwargs

    def test_returns_the_docker_output_object_with_warnings_filtered(self):
        manager = _make_manager()
        raw = _output(
            combined=['time="2024-01-01T00:00:00Z" level=info msg="noise"', "real line"],
            exit_code=1,
        )
        manager.docker_client.compose.exec.return_value = raw

        result = manager._container_run("bench build", capture_output=True)

        assert result is raw
        assert result.combined == ["real line"]
        assert result.exit_code == 1

    def test_empty_combined_output_is_returned_unchanged(self):
        manager = _make_manager()
        raw = _output(combined=[])
        manager.docker_client.compose.exec.return_value = raw

        result = manager._container_run("bench build", capture_output=True)

        assert result is raw
        assert result.combined == []


class TestExecStream:
    """use_run=False, capture_output=False -> compose.exec(stream=True) piped to live_lines."""

    def test_calls_compose_exec_only(self):
        manager = _make_manager()
        manager.docker_client.compose.exec.return_value = iter([])

        manager._container_run("bench build")

        assert manager.docker_client.compose.exec.call_count == 1
        manager.docker_client.compose.run.assert_not_called()

    def test_exact_kwargs(self):
        manager = _make_manager()
        manager.docker_client.compose.exec.return_value = iter([])

        manager._container_run("bench build", workdir="/opt/elsewhere", user="root", service="worker")

        call = manager.docker_client.compose.exec.call_args
        assert call.args == ()
        assert call.kwargs == {
            "service": "worker",
            "command": "/bin/bash -c 'bench build'",
            "workdir": "/opt/elsewhere",
            "user": "root",
            "env": None,
            "stream": True,
        }

    def test_streams_the_iterator_verbatim_with_noise_filters(self):
        manager = _make_manager()
        stream = iter([("stdout", b"line\n")])
        manager.docker_client.compose.exec.return_value = stream

        manager._container_run("bench build")

        manager.output.live_lines.assert_called_once_with(stream, line_filters=DOCKER_LINE_NOISE)

    def test_returns_none(self):
        manager = _make_manager()
        manager.docker_client.compose.exec.return_value = iter([])

        assert manager._container_run("bench build") is None


class TestProvisionImageShortCircuit:
    """use_run=True with provision_image set bypasses compose entirely."""

    def test_delegates_to_run_in_provision_image_and_returns_its_value(self):
        manager = _make_manager(provision_image="fm/bake:latest")
        sentinel = _output()
        manager._run_in_provision_image = MagicMock(return_value=sentinel)

        result = manager._container_run(
            "bench build",
            capture_output=True,
            use_run=True,
            workdir="/opt/elsewhere",
        )

        assert result is sentinel
        manager._run_in_provision_image.assert_called_once_with(
            "bench build",
            capture_output=True,
            workdir="/opt/elsewhere",
            env={},
        )
        manager.docker_client.compose.run.assert_not_called()
        manager.docker_client.compose.exec.assert_not_called()
        manager.output.live_lines.assert_not_called()

    def test_command_is_passed_unwrapped(self):
        manager = _make_manager(provision_image="fm/bake:latest")
        manager._run_in_provision_image = MagicMock(return_value=None)

        manager._container_run("bench build", use_run=True)

        passed = manager._run_in_provision_image.call_args.args[0]
        assert passed == "bench build"
        assert "/bin/bash" not in passed
        assert "cd " not in passed

    def test_streaming_delegation_returns_none_and_does_not_call_live_lines_itself(self):
        manager = _make_manager(provision_image="fm/bake:latest")
        manager._run_in_provision_image = MagicMock(return_value=None)

        assert manager._container_run("bench build", use_run=True) is None
        manager.output.live_lines.assert_not_called()

    def test_provision_image_is_ignored_without_use_run(self):
        manager = _make_manager(provision_image="fm/bake:latest")
        manager._run_in_provision_image = MagicMock()
        manager.docker_client.compose.exec.return_value = iter([])

        manager._container_run("bench build")

        manager._run_in_provision_image.assert_not_called()
        assert manager.docker_client.compose.exec.call_count == 1

    def test_use_run_without_provision_image_goes_to_compose_run(self):
        manager = _make_manager(provision_image=None)
        manager.docker_client.compose.run.return_value = iter([])

        manager._container_run("bench build", use_run=True)

        assert manager.docker_client.compose.run.call_count == 1

    def test_provision_image_receives_the_merged_env_mapping_not_env_options(self):
        manager = _make_manager(provision_image="fm/bake:latest", external_db=True)
        manager._run_in_provision_image = MagicMock(return_value=None)

        manager._container_run("bench build", use_run=True, env={"FOO": "bar"})

        assert manager._run_in_provision_image.call_args.kwargs["env"] == {
            "MYSQL_HOME": f"/workspace/frappe-bench/config/tls/{BENCH_NAME}",
            "FOO": "bar",
        }


class TestEnvOptions:
    """`env` + the site's own MYSQL_HOME become a `KEY=VALUE` list, or None when empty."""

    @pytest.mark.parametrize("use_run", [True, False])
    def test_no_env_and_no_external_db_yields_none(self, use_run):
        manager = _make_manager()
        seam = manager.docker_client.compose.run if use_run else manager.docker_client.compose.exec
        seam.return_value = iter([])

        manager._container_run("bench build", use_run=use_run)

        assert seam.call_args.kwargs["env"] is None

    @pytest.mark.parametrize("use_run", [True, False])
    def test_empty_env_dict_yields_none(self, use_run):
        manager = _make_manager()
        seam = manager.docker_client.compose.run if use_run else manager.docker_client.compose.exec
        seam.return_value = iter([])

        manager._container_run("bench build", use_run=use_run, env={})

        assert seam.call_args.kwargs["env"] is None

    @pytest.mark.parametrize("use_run", [True, False])
    def test_caller_env_becomes_key_value_strings(self, use_run):
        manager = _make_manager()
        seam = manager.docker_client.compose.run if use_run else manager.docker_client.compose.exec
        seam.return_value = iter([])

        manager._container_run("bench build", use_run=use_run, env={"FOO": "bar", "BAZ": "qux"})

        assert seam.call_args.kwargs["env"] == ["FOO=bar", "BAZ=qux"]

    @pytest.mark.parametrize("use_run", [True, False])
    def test_external_db_injects_site_mysql_home_first(self, use_run):
        manager = _make_manager(external_db=True)
        seam = manager.docker_client.compose.run if use_run else manager.docker_client.compose.exec
        seam.return_value = iter([])

        manager._container_run("bench build", use_run=use_run, env={"FOO": "bar"})

        assert seam.call_args.kwargs["env"] == [
            f"MYSQL_HOME=/workspace/frappe-bench/config/tls/{BENCH_NAME}",
            "FOO=bar",
        ]

    @pytest.mark.parametrize("use_run", [True, False])
    def test_caller_env_overrides_the_site_mysql_home(self, use_run):
        manager = _make_manager(external_db=True)
        seam = manager.docker_client.compose.run if use_run else manager.docker_client.compose.exec
        seam.return_value = iter([])

        manager._container_run("bench build", use_run=use_run, env={"MYSQL_HOME": "/custom"})

        assert seam.call_args.kwargs["env"] == ["MYSQL_HOME=/custom"]

    @pytest.mark.parametrize("capture_output", [True, False])
    def test_env_options_reach_the_capture_and_stream_variants_alike(self, capture_output):
        manager = _make_manager()
        manager.docker_client.compose.exec.return_value = _output() if capture_output else iter([])

        manager._container_run("bench build", capture_output=capture_output, env={"FOO": "bar"})

        assert manager.docker_client.compose.exec.call_args.kwargs["env"] == ["FOO=bar"]


class TestCommandWrapping:
    """The two paths wrap `command` differently; both wrappings are verbatim (no quoting)."""

    def test_run_path_prefixes_cd_workdir(self):
        for script in TRICKY_SCRIPTS:
            manager = _make_manager()
            manager.docker_client.compose.run.return_value = iter([])
            manager._container_run(script, use_run=True, workdir="/wd")
            assert manager.docker_client.compose.run.call_args.kwargs["command"] == (
                f"/bin/bash -c 'cd /wd && {script}'"
            ), script

    def test_exec_path_does_not_prefix_cd_workdir(self):
        for script in TRICKY_SCRIPTS:
            manager = _make_manager()
            manager.docker_client.compose.exec.return_value = iter([])
            manager._container_run(script, workdir="/wd")
            assert manager.docker_client.compose.exec.call_args.kwargs["command"] == (f"/bin/bash -c '{script}'"), (
                script
            )


class _RecordingFailure(BenchOperationException):
    """A real BenchOperationException whose set_output is observable without rich rendering."""

    def __init__(self):
        super().__init__(BENCH_NAME, "build failed")
        self.set_output_calls: list[SubprocessOutput] = []

    def set_output(self, output):
        self.set_output_calls.append(output)


class TestDockerExceptionMapping:
    """A DockerException becomes `raise_exception_obj` when one was supplied, else propagates."""

    def test_maps_to_the_supplied_exception_with_docker_output_attached(self):
        manager = _make_manager()
        failure_output = _output(combined=["boom"], exit_code=1)
        docker_error = DockerException(["docker", "compose", "exec"], failure_output)
        manager.docker_client.compose.exec.side_effect = docker_error
        wrapper = _RecordingFailure()

        with pytest.raises(BenchOperationException) as excinfo:
            manager._container_run("bench build", raise_exception_obj=wrapper)

        assert excinfo.value is wrapper
        assert wrapper.set_output_calls == [failure_output]

    def test_propagates_when_no_exception_object_given(self):
        manager = _make_manager()
        docker_error = DockerException(["docker", "compose", "run"], _output(combined=["boom"], exit_code=1))
        manager.docker_client.compose.run.side_effect = docker_error

        with pytest.raises(DockerException) as excinfo:
            manager._container_run("bench build", use_run=True, capture_output=True)

        assert excinfo.value is docker_error

    def test_live_lines_failures_are_not_swallowed(self):
        manager = _make_manager()
        manager.docker_client.compose.exec.return_value = iter([])
        manager.output.live_lines.side_effect = RuntimeError("stream died")

        with pytest.raises(RuntimeError, match="stream died"):
            manager._container_run("bench build")
