"""
Characterization tests for DockerComposeWrapper.cp (docker_compose.py).

Why this file exists: `DockerComposeWrapper.cp` is a near-twin of `DockerClient.cp`
(docker_client.py). The client side is already pinned; the compose side was not, so the
pair could not be merged safely. These tests pin the compose side precisely enough that a
later merge is verifiable, i.e. every asymmetry the merge must preserve:

- the argv it builds: `docker_compose_cmd` prefix + `cp` + flags + source + destination,
  with the container prefixes folded into the two positionals (never as options);
- which parameters are forwarded as options (`archive`, `follow_link`) and which are
  always omitted (`source`, `destination`, `source_container`, `destination_container`,
  `stream`, `self`), including flag ORDER;
- how it decides success/failure and streaming: unlike the client, compose's `cp` is a
  pure argv builder wrapped by @docker_command(use_original_implementation=True). The
  decorator owns execution, so `stream=None` (the compose-only default) delegates the
  decision to the output handler, `stream=True` hands back the raw iterator untouched and
  `stream=False` forces the materialized path;
- what it returns: whatever the boundary returned, unwrapped and unmodified;
- its error path: DockerException propagates untouched, and on the explicit-stream path
  nothing is executed until the caller consumes the iterator.

Behaviour is pinned as-is; no bug is fixed here.
"""

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from frappe_manager.docker.docker_compose import DockerComposeWrapper
from frappe_manager.docker.docker_exceptions import DockerException
from frappe_manager.docker.subprocess_output import SubprocessOutput

BOUNDARY = "frappe_manager.docker.docker_compose.run_command_with_exit_code"


def make_wrapper(tmp_path: Path, output=None) -> DockerComposeWrapper:
    compose_file = tmp_path / "docker-compose.yml"
    compose_file.write_text("services: {}\n")
    return DockerComposeWrapper(compose_file, output=output)


def make_handler(should_stream: bool) -> MagicMock:
    handler = MagicMock()
    handler.should_stream_docker = should_stream
    return handler


def argv_of(wrapper: DockerComposeWrapper, **kwargs) -> list:
    """Run cp against a stubbed boundary and return the argv it was handed."""
    with patch(BOUNDARY) as boundary:
        boundary.return_value = SubprocessOutput([], [], [], 0)
        wrapper.cp(**kwargs)
    return boundary.call_args[0][0]


class TestComposeCpArgv:
    """The argv `cp` builds. A merge must reproduce this list element for element."""

    @pytest.mark.timeout(15)
    def test_minimal_argv_is_prefix_plus_cp_plus_two_positionals(self, tmp_path):
        wrapper = make_wrapper(tmp_path)

        argv = argv_of(wrapper, source="/host/file", destination="/in/container")

        assert argv == [
            "docker",
            "compose",
            "-f",
            (tmp_path / "docker-compose.yml").as_posix(),
            "cp",
            "/host/file",
            "/in/container",
        ]

    @pytest.mark.timeout(15)
    def test_no_flags_are_emitted_when_both_bools_are_false(self, tmp_path):
        wrapper = make_wrapper(tmp_path)

        argv = argv_of(wrapper, source="a", destination="b", archive=False, follow_link=False)

        assert not [item for item in argv if isinstance(item, str) and item.startswith("--") and item != "--"]

    @pytest.mark.timeout(15)
    def test_archive_and_follow_link_are_flags_in_signature_order_before_positionals(self, tmp_path):
        wrapper = make_wrapper(tmp_path)

        argv = argv_of(wrapper, source="a", destination="b", archive=True, follow_link=True)

        assert argv[-4:] == ["--archive", "--follow-link", "a", "b"]

    @pytest.mark.timeout(15)
    def test_archive_alone_emits_only_archive(self, tmp_path):
        wrapper = make_wrapper(tmp_path)

        argv = argv_of(wrapper, source="a", destination="b", archive=True)

        assert argv[-3:] == ["--archive", "a", "b"]

    @pytest.mark.timeout(15)
    def test_follow_link_alone_emits_only_follow_link_with_dash_not_underscore(self, tmp_path):
        wrapper = make_wrapper(tmp_path)

        argv = argv_of(wrapper, source="a", destination="b", follow_link=True)

        assert argv[-3:] == ["--follow-link", "a", "b"]
        assert "--follow_link" not in argv

    @pytest.mark.timeout(15)
    def test_source_container_is_folded_into_the_source_positional(self, tmp_path):
        wrapper = make_wrapper(tmp_path)

        argv = argv_of(wrapper, source="/etc/conf", destination="/host/conf", source_container="frappe")

        assert argv[-2:] == ["frappe:/etc/conf", "/host/conf"]
        assert "--source-container" not in argv

    @pytest.mark.timeout(15)
    def test_destination_container_is_folded_into_the_destination_positional(self, tmp_path):
        wrapper = make_wrapper(tmp_path)

        argv = argv_of(wrapper, source="/host/conf", destination="/etc/conf", destination_container="nginx")

        assert argv[-2:] == ["/host/conf", "nginx:/etc/conf"]
        assert "--destination-container" not in argv

    @pytest.mark.timeout(15)
    def test_both_containers_are_folded_into_their_own_positional(self, tmp_path):
        wrapper = make_wrapper(tmp_path)

        argv = argv_of(
            wrapper,
            source="/a",
            destination="/b",
            source_container="from",
            destination_container="to",
        )

        assert argv[-2:] == ["from:/a", "to:/b"]

    @pytest.mark.timeout(15)
    def test_excluded_parameters_never_become_options(self, tmp_path):
        wrapper = make_wrapper(tmp_path)

        argv = argv_of(
            wrapper,
            source="/a",
            destination="/b",
            source_container="from",
            destination_container="to",
            archive=True,
            follow_link=True,
            stream=False,
        )

        for never in (
            "--source",
            "--destination",
            "--source-container",
            "--destination-container",
            "--stream",
            "--self",
        ):
            assert never not in argv

    @pytest.mark.timeout(15)
    def test_empty_container_names_are_falsy_and_add_no_prefix(self, tmp_path):
        wrapper = make_wrapper(tmp_path)

        argv = argv_of(
            wrapper,
            source="/a",
            destination="/b",
            source_container="",
            destination_container="",
        )

        assert argv[-2:] == ["/a", "/b"]

    @pytest.mark.timeout(15)
    def test_empty_positionals_are_still_forwarded_as_empty_arguments(self, tmp_path):
        """Pinned as-is: cp does not validate its positionals (see suspicions)."""
        wrapper = make_wrapper(tmp_path)

        argv = argv_of(wrapper, source="", destination="")

        assert argv[-3:] == ["cp", "", ""]

    @pytest.mark.timeout(15)
    def test_positional_call_builds_the_same_argv_as_keyword_call(self, tmp_path):
        wrapper = make_wrapper(tmp_path)

        with patch(BOUNDARY) as boundary:
            boundary.return_value = SubprocessOutput([], [], [], 0)
            wrapper.cp("/a", "/b", "from", "to", True, True)
        positional = boundary.call_args[0][0]

        keyword = argv_of(
            wrapper,
            source="/a",
            destination="/b",
            source_container="from",
            destination_container="to",
            archive=True,
            follow_link=True,
        )

        assert positional == keyword

    @pytest.mark.timeout(15)
    def test_cp_inherits_the_compose_file_override_prefix(self, tmp_path):
        compose_file = tmp_path / "docker-compose.yml"
        compose_file.write_text("services: {}\n")
        override = tmp_path / "docker-compose.override.yml"
        override.write_text("services: {}\n")
        wrapper = DockerComposeWrapper(compose_file)

        argv = argv_of(wrapper, source="/a", destination="/b")

        assert argv == [
            "docker",
            "compose",
            "-f",
            compose_file.as_posix(),
            "-f",
            override.as_posix(),
            "cp",
            "/a",
            "/b",
        ]


class TestComposeCpIsAnArgvBuilderNotAnExecutor:
    """The core asymmetry with docker_client.cp: compose's cp never runs anything itself."""

    @pytest.mark.timeout(15)
    def test_undecorated_implementation_returns_full_argv_and_runs_nothing(self, tmp_path):
        wrapper = make_wrapper(tmp_path)

        with patch(BOUNDARY) as boundary:
            result = DockerComposeWrapper.cp.__wrapped__(
                wrapper,
                source="/a",
                destination="/b",
                source_container="frappe",
                archive=True,
            )

        assert result == [*wrapper.docker_compose_cmd, "cp", "--archive", "frappe:/a", "/b"]
        boundary.assert_not_called()

    @pytest.mark.timeout(15)
    def test_argv_starts_with_the_live_prefix_object_contents(self, tmp_path):
        wrapper = make_wrapper(tmp_path)

        argv = argv_of(wrapper, source="/a", destination="/b")

        assert argv[: len(wrapper.docker_compose_cmd)] == wrapper.docker_compose_cmd
        assert wrapper.docker_compose_cmd[-1].endswith("docker-compose.yml")


class TestComposeCpStreamDecision:
    """`stream=None` is compose-only: the decision is delegated to the output handler."""

    @pytest.mark.timeout(15)
    def test_stream_unset_without_handler_uses_the_materialized_path(self, tmp_path):
        wrapper = make_wrapper(tmp_path, output=None)
        expected = SubprocessOutput(["ok"], [], ["ok"], 0)

        with patch(BOUNDARY) as boundary:
            boundary.return_value = expected
            result = wrapper.cp(source="/a", destination="/b")

        assert boundary.call_args.kwargs["stream"] is False
        assert result is expected

    @pytest.mark.timeout(15)
    def test_stream_unset_with_handler_that_declines_uses_the_materialized_path(self, tmp_path):
        handler = make_handler(should_stream=False)
        wrapper = make_wrapper(tmp_path, output=handler)

        with patch(BOUNDARY) as boundary:
            boundary.return_value = SubprocessOutput([], [], [], 0)
            wrapper.cp(source="/a", destination="/b")

        assert boundary.call_args.kwargs["stream"] is False
        handler.live_lines.assert_not_called()

    @pytest.mark.timeout(15)
    def test_stream_unset_with_streaming_handler_displays_live_and_returns_materialized(self, tmp_path):
        handler = make_handler(should_stream=True)
        wrapper = make_wrapper(tmp_path, output=handler)
        lines = [("stdout", b"copied"), ("exit_code", b"0")]

        with patch(BOUNDARY) as boundary:
            boundary.return_value = iter(lines)
            result = wrapper.cp(source="/a", destination="/b")

        assert boundary.call_args.kwargs["stream"] is True
        handler.live_lines.assert_called_once()
        assert isinstance(result, SubprocessOutput)
        assert result.stdout == ["copied"]
        assert result.exit_code == 0

    @pytest.mark.timeout(15)
    def test_explicit_stream_true_returns_the_raw_iterator_and_never_displays_it(self, tmp_path):
        handler = make_handler(should_stream=False)
        wrapper = make_wrapper(tmp_path, output=handler)
        iterator = iter([("stdout", b"copied"), ("exit_code", b"0")])

        with patch(BOUNDARY) as boundary:
            boundary.return_value = iterator
            result = wrapper.cp(source="/a", destination="/b", stream=True)

        assert boundary.call_args.kwargs["stream"] is True
        assert result is iterator
        handler.live_lines.assert_not_called()

    @pytest.mark.timeout(15)
    def test_explicit_stream_false_overrides_a_streaming_handler(self, tmp_path):
        handler = make_handler(should_stream=True)
        wrapper = make_wrapper(tmp_path, output=handler)
        expected = SubprocessOutput([], [], [], 0)

        with patch(BOUNDARY) as boundary:
            boundary.return_value = expected
            result = wrapper.cp(source="/a", destination="/b", stream=False)

        assert boundary.call_args.kwargs["stream"] is False
        assert result is expected
        handler.live_lines.assert_not_called()


class TestComposeCpSuccessFailureAndErrors:
    """Success/failure is entirely the boundary's verdict; cp adds no judgement."""

    @pytest.mark.timeout(15)
    def test_nonzero_exit_code_is_returned_not_raised_on_the_materialized_path(self, tmp_path):
        wrapper = make_wrapper(tmp_path)
        expected = SubprocessOutput([], ["no such file"], ["no such file"], 1)

        with patch(BOUNDARY) as boundary:
            boundary.return_value = expected
            result = wrapper.cp(source="/a", destination="/b")

        assert result is expected
        assert result.exit_code == 1

    @pytest.mark.timeout(15)
    def test_docker_exception_from_the_boundary_propagates_untouched(self, tmp_path):
        wrapper = make_wrapper(tmp_path)
        failure = DockerException(["docker", "compose", "cp"], SubprocessOutput([], ["boom"], ["boom"], 1))

        with patch(BOUNDARY) as boundary:
            boundary.side_effect = failure
            with pytest.raises(DockerException) as raised:
                wrapper.cp(source="/a", destination="/b")

        assert raised.value is failure

    @pytest.mark.timeout(15)
    def test_explicit_stream_true_defers_failure_until_the_caller_consumes(self, tmp_path):
        wrapper = make_wrapper(tmp_path)
        failure = DockerException(["docker", "compose", "cp"], SubprocessOutput([], ["boom"], ["boom"], 1))

        def exploding_iterator():
            yield "stdout", b"starting"
            raise failure

        with patch(BOUNDARY) as boundary:
            boundary.return_value = exploding_iterator()
            result = wrapper.cp(source="/a", destination="/b", stream=True)
            assert next(iter(result)) == ("stdout", b"starting")
            with pytest.raises(DockerException):
                list(result)
