"""The observable contract of `DockerClient`, the seam every docker call in fm goes through.

Every method on `DockerClient` is a thin argv builder in front of exactly one call to
`run_command_with_exit_code`. That makes argv the entire contract: a dropped flag, a swapped
positional or a `--force` that quietly became `--forced` still "works" all the way down to the
docker CLI, so nothing in fm notices until a user's container is not removed. These tests therefore
assert the *complete* argv list (not a substring, not a prefix) plus the streaming/stdin keywords
for every command builder, so a later refactor of `parameters_to_options`, of the exclude lists, or
of the option ordering cannot pass silently.

Also pinned, because callers branch on them:
  * `version()` / `server_running()` -- how a verdict is reached, including the swallowed
    `DockerException` path that is how `docker version` behaves when the daemon is down.
  * the inspect family -- what a failed inspect degrades to (`[]` / `{}`), and JSON-lines vs
    single-document parsing.
  * `TempContainer` -- the exact `run`/`rm` argv it emits, and the error translation on failure.
  * compose dispatch -- that a compose file path produces a `docker compose -f <abs path>` prefix.

The subprocess boundary (`run_command_with_exit_code`) is mocked in every test: no docker daemon is
reachable here and nothing in this file may invoke one. Characterization only -- where current
behaviour looks wrong it is pinned as-is and flagged in the docstring of the test.
"""

import json
from pathlib import Path
from unittest.mock import Mock, patch

import pytest

from frappe_manager.docker.docker_client import DockerClient, TempContainer
from frappe_manager.docker.docker_compose import DockerComposeWrapper
from frappe_manager.docker.docker_exceptions import DockerException
from frappe_manager.docker.subprocess_output import SubprocessOutput

DOCKER = "docker"


def _output(stdout: list[str] | None = None, exit_code: int = 0) -> SubprocessOutput:
    lines = stdout or []
    return SubprocessOutput(stdout=list(lines), stderr=[], combined=list(lines), exit_code=exit_code)


@pytest.fixture
def runner():
    """Patch the single subprocess boundary every DockerClient method funnels through."""
    with patch("frappe_manager.docker.docker_client.run_command_with_exit_code") as mock:
        mock.return_value = _output()
        yield mock


def argv(mock: Mock) -> list:
    """The full_cmd positional handed to run_command_with_exit_code."""
    return mock.call_args.args[0]


def opts(mock: Mock) -> dict:
    return mock.call_args.kwargs


@pytest.fixture
def client() -> DockerClient:
    return DockerClient()


class TestConstruction:
    def test_command_prefix_is_bare_docker_and_no_compose_without_a_path(self, client):
        assert client.docker_cmd == [DOCKER]
        assert client.compose is None
        assert client.output is None

    def test_a_compose_path_builds_a_wrapper_sharing_the_output_handler(self, tmp_path):
        compose_file = tmp_path / "docker-compose.yml"
        handler = Mock()

        c = DockerClient(compose_file, output=handler)

        assert isinstance(c.compose, DockerComposeWrapper)
        assert c.compose.compose_file_path == compose_file
        assert c.compose.output is handler
        assert c.output is handler

    def test_a_relative_compose_path_is_absolutised_for_the_compose_prefix(self, tmp_path, monkeypatch):
        """Compose is invoked from arbitrary cwds, so the -f path must not stay relative."""
        monkeypatch.chdir(tmp_path)

        c = DockerClient(Path("docker-compose.yml"))

        assert c.compose.compose_file_path.is_absolute()
        assert c.compose.docker_compose_cmd == [
            DOCKER,
            "compose",
            "-f",
            (tmp_path / "docker-compose.yml").as_posix(),
        ]


class TestVersion:
    def test_argv_asks_for_json_and_never_streams(self, client, runner):
        runner.return_value = _output(['{"Client": {}}'])

        client.version()

        assert argv(runner) == [DOCKER, "version", "--format", "json"]
        assert opts(runner) == {"stream": False}

    def test_stdout_lines_are_joined_before_being_parsed(self, client, runner):
        runner.return_value = _output(["{", '"Server": {"Version": "24.0"},', '"Client": {}', "}"])

        assert client.version() == {"Server": {"Version": "24.0"}, "Client": {}}

    def test_a_failed_docker_version_is_swallowed_and_its_stdout_parsed_instead(self, client, runner):
        """`docker version` exits non-zero when the daemon is down but still prints the client JSON."""
        runner.side_effect = DockerException(
            [DOCKER, "version"],
            _output(['{"Client": {"Version": "24.0"}}'], exit_code=1),
        )

        assert client.version() == {"Client": {"Version": "24.0"}}

    def test_a_failed_docker_version_with_unparseable_stdout_raises_a_json_error(self, client, runner):
        """SUSPICION (pinned, not fixed): the rescue path assumes the failure printed JSON.

        A daemon failure whose stdout is a plain-text error turns into a JSONDecodeError escaping
        `server_running()`, not a False verdict.
        """
        runner.side_effect = DockerException(
            [DOCKER, "version"],
            _output(["Cannot connect to the Docker daemon"], exit_code=1),
        )

        with pytest.raises(json.JSONDecodeError):
            client.version()


class TestServerRunning:
    def test_a_populated_server_key_means_running(self, client):
        with patch.object(client, "version", return_value={"Client": {}, "Server": {"Version": "24.0"}}):
            assert client.server_running() is True

    def test_an_empty_server_key_means_not_running_without_a_group_diagnostic(self, client):
        """The Server key present but falsy is a reachable-but-broken daemon, not a permission issue."""
        with (
            patch.object(client, "version", return_value={"Client": {}, "Server": {}}),
            patch("frappe_manager.docker.docker_client.is_current_user_in_group") as group_check,
        ):
            assert client.server_running() is False
            group_check.assert_not_called()

    def test_a_missing_server_key_triggers_the_docker_group_diagnostic(self, client):
        """No Server key at all is the permission-denied shape, so the user gets the group hint."""
        with (
            patch.object(client, "version", return_value={"Client": {"Version": "24.0"}}),
            patch("frappe_manager.docker.docker_client.is_current_user_in_group") as group_check,
        ):
            assert client.server_running() is False
            group_check.assert_called_once_with("docker")

    def test_the_group_diagnostic_verdict_never_overrides_the_false_return(self, client):
        with (
            patch.object(client, "version", return_value={}),
            patch("frappe_manager.docker.docker_client.is_current_user_in_group", return_value=True),
        ):
            assert client.server_running() is False

    def test_is_running_is_a_pure_alias_of_server_running(self, client):
        with patch.object(client, "server_running", return_value=True) as inner:
            assert client.is_running() is True
            inner.assert_called_once_with()


class TestCp:
    def test_plain_host_to_host_copy_carries_no_flags(self, client, runner):
        client.cp(source="/host/a", destination="/host/b")

        assert argv(runner) == [DOCKER, "cp", "/host/a", "/host/b"]
        assert opts(runner) == {"stream": False}

    def test_source_container_prefixes_only_the_source(self, client, runner):
        client.cp(source="/etc/hosts", destination="./out", source_container="c1")

        assert argv(runner) == [DOCKER, "cp", "c1:/etc/hosts", "./out"]

    def test_destination_container_prefixes_only_the_destination(self, client, runner):
        client.cp(source="./in", destination="/etc/hosts", destination_container="c2")

        assert argv(runner) == [DOCKER, "cp", "./in", "c2:/etc/hosts"]

    def test_both_containers_prefix_both_operands(self, client, runner):
        client.cp(source="/a", destination="/b", source_container="c1", destination_container="c2")

        assert argv(runner) == [DOCKER, "cp", "c1:/a", "c2:/b"]

    def test_flags_precede_the_operands_in_declaration_order(self, client, runner):
        client.cp(source="/a", destination="/b", archive=True, follow_link=True)

        assert argv(runner) == [DOCKER, "cp", "--archive", "--follow-link", "/a", "/b"]

    def test_false_flags_are_omitted_entirely(self, client, runner):
        client.cp(source="/a", destination="/b", archive=False, follow_link=False)

        assert argv(runner) == [DOCKER, "cp", "/a", "/b"]

    def test_stream_is_forwarded_and_the_boundary_result_returned_unchanged(self, client, runner):
        sentinel = object()
        runner.return_value = sentinel

        assert client.cp(source="/a", destination="/b", stream=True) is sentinel
        assert opts(runner) == {"stream": True}


class TestKill:
    def test_container_is_the_trailing_positional(self, client, runner):
        client.kill(container="frappe")

        assert argv(runner) == [DOCKER, "kill", "frappe"]

    def test_signal_becomes_a_flag_before_the_container(self, client, runner):
        client.kill(container="frappe", signal="SIGTERM")

        assert argv(runner) == [DOCKER, "kill", "--signal", "SIGTERM", "frappe"]

    def test_no_signal_means_no_flag(self, client, runner):
        client.kill(container="frappe", signal=None)

        assert argv(runner) == [DOCKER, "kill", "frappe"]


class TestRm:
    def test_bare_removal(self, client, runner):
        client.rm(container="frappe")

        assert argv(runner) == [DOCKER, "rm", "frappe"]

    def test_every_flag_is_emitted_in_declaration_order(self, client, runner):
        client.rm(container="frappe", force=True, link=True, volumes=True)

        assert argv(runner) == [DOCKER, "rm", "--force", "--link", "--volumes", "frappe"]

    def test_force_alone(self, client, runner):
        client.rm(container="frappe", force=True)

        assert argv(runner) == [DOCKER, "rm", "--force", "frappe"]


class TestRun:
    def test_pull_policy_is_always_emitted_even_when_defaulted(self, client, runner):
        """`--pull missing` is a non-empty string default, so it ships on every single run."""
        client.run(image="alpine")

        assert argv(runner) == [DOCKER, "run", "--pull", "missing", "alpine"]

    def test_full_option_set_keeps_declaration_order_with_env_and_image_last(self, client, runner):
        client.run(
            image="alpine:3.18",
            command="echo hi",
            env={"A": "1", "B": "2"},
            name="box",
            user="1000:1000",
            volume=["/h1:/c1", "/h2:/c2"],
            detach=True,
            entrypoint="bash",
            workdir="/workspace",
            platform="linux/amd64",
            pull="always",
            rm=True,
        )

        assert argv(runner) == [
            DOCKER,
            "run",
            "--name",
            "box",
            "--user",
            "1000:1000",
            "--volume",
            "/h1:/c1",
            "--volume",
            "/h2:/c2",
            "--detach",
            "--entrypoint",
            "bash",
            "--workdir",
            "/workspace",
            "--platform",
            "linux/amd64",
            "--pull",
            "always",
            "--rm",
            "--env",
            "A=1",
            "--env",
            "B=2",
            "alpine:3.18",
            "echo",
            "hi",
        ]

    def test_each_volume_gets_its_own_flag(self, client, runner):
        client.run(image="alpine", volume=["/a:/a", "/b:/b", "/c:/c"])

        assert argv(runner) == [
            DOCKER,
            "run",
            "--volume",
            "/a:/a",
            "--volume",
            "/b:/b",
            "--volume",
            "/c:/c",
            "--pull",
            "missing",
            "alpine",
        ]

    def test_an_empty_volume_list_adds_nothing(self, client, runner):
        client.run(image="alpine", volume=[])

        assert argv(runner) == [DOCKER, "run", "--pull", "missing", "alpine"]

    def test_command_is_shlex_split_so_quoted_arguments_stay_one_token(self, client, runner):
        client.run(image="alpine", command="sh -c 'echo a b'")

        assert argv(runner)[-3:] == ["sh", "-c", "echo a b"]

    def test_use_shlex_split_false_passes_the_command_as_a_single_argument(self, client, runner):
        client.run(image="alpine", command="sh -c 'echo a b'", use_shlex_split=False)

        assert argv(runner) == [DOCKER, "run", "--pull", "missing", "alpine", "sh -c 'echo a b'"]

    def test_an_empty_command_string_appends_nothing(self, client, runner):
        client.run(image="alpine", command="")

        assert argv(runner) == [DOCKER, "run", "--pull", "missing", "alpine"]

    def test_env_is_never_turned_into_an_option_by_the_generic_converter(self, client, runner):
        """`env` is excluded and hand-expanded; a `--env {'A': '1'}` dict would be a hard failure."""
        client.run(image="alpine", env={"A": "1"})

        assert "--env" in argv(runner)
        assert argv(runner).count("--env") == 1
        assert argv(runner)[argv(runner).index("--env") + 1] == "A=1"

    def test_a_non_dict_env_is_ignored(self, client, runner):
        client.run(image="alpine", env=None)

        assert "--env" not in argv(runner)

    def test_stream_defaults_to_false_and_is_forwarded(self, client, runner):
        client.run(image="alpine")
        assert opts(runner) == {"stream": False}

        client.run(image="alpine", stream=True)
        assert opts(runner) == {"stream": True}

    def test_a_boundary_failure_propagates_untouched(self, client, runner):
        boom = DockerException([DOCKER, "run", "alpine"], _output(exit_code=125))
        runner.side_effect = boom

        with pytest.raises(DockerException) as excinfo:
            client.run(image="alpine")

        assert excinfo.value is boom
        assert excinfo.value.output.exit_code == 125


class TestPull:
    def test_image_reference_is_the_trailing_positional(self, client, runner):
        client.pull(container_name="alpine:3.18")

        assert argv(runner) == [DOCKER, "pull", "alpine:3.18"]
        assert opts(runner) == {"stream": False}

    def test_all_tags_and_platform_precede_the_reference(self, client, runner):
        client.pull(container_name="alpine", all_tags=True, platform="linux/arm64")

        assert argv(runner) == [DOCKER, "pull", "--all-tags", "--platform", "linux/arm64", "alpine"]


class TestPush:
    def test_argv_is_just_push_plus_the_image(self, client, runner):
        client.push(image="reg.local/app:1")

        assert argv(runner) == [DOCKER, "push", "reg.local/app:1"]

    def test_push_streams_by_default_unlike_every_other_method(self, client, runner):
        """Push is the one builder whose `stream` default is True; callers rely on the iterator."""
        client.push(image="reg.local/app:1")

        assert opts(runner) == {"stream": True}

    def test_streaming_can_be_turned_off_explicitly(self, client, runner):
        client.push(image="reg.local/app:1", stream=False)

        assert opts(runner) == {"stream": False}


class TestLogin:
    # Not a credential: a literal for the stdin plumbing, kept out of the credential-heuristic namespace.
    STDIN_VALUE = "s3cret"

    def test_password_travels_on_stdin_and_never_in_argv(self, client, runner):
        client.login(registry="reg.local", username="bob", password=self.STDIN_VALUE)

        assert argv(runner) == [DOCKER, "login", "reg.local", "-u", "bob", "--password-stdin"]
        assert self.STDIN_VALUE not in argv(runner)
        assert opts(runner) == {"stream": False, "capture_output": False, "input_data": b"s3cret"}

    def test_a_bytes_password_is_forwarded_unencoded(self, client, runner):
        client.login(registry="reg.local", username="bob", password=b"raw-bytes")

        assert opts(runner)["input_data"] == b"raw-bytes"

    def test_the_stream_argument_is_ignored_by_design_of_the_stdin_path(self, client, runner):
        """SUSPICION (pinned, not fixed): `login(stream=True)` still runs non-streaming."""
        client.login(registry="reg.local", username="bob", password=self.STDIN_VALUE, stream=True)

        assert opts(runner)["stream"] is False

    def test_a_failed_login_propagates_the_docker_exception(self, client, runner):
        runner.side_effect = DockerException([DOCKER, "login"], _output(exit_code=1))

        with pytest.raises(DockerException):
            client.login(registry="reg.local", username="bob", password=self.STDIN_VALUE)

    def test_the_boundary_result_is_returned_verbatim(self, client, runner):
        runner.return_value = None

        assert client.login(registry="reg.local", username="bob", password=self.STDIN_VALUE) is None


class TestSaveAndLoad:
    def test_save_writes_to_o_before_listing_every_image(self, client, runner, tmp_path):
        target = tmp_path / "images.tar"

        client.save(images=["a:1", "b:2"], output_path=target)

        assert argv(runner) == [DOCKER, "save", "-o", str(target), "a:1", "b:2"]
        assert opts(runner) == {"stream": False}

    def test_save_accepts_a_single_image(self, client, runner, tmp_path):
        client.save(images=["only:1"], output_path=tmp_path / "x.tar")

        assert argv(runner)[-1] == "only:1"

    def test_load_reads_from_i(self, client, runner, tmp_path):
        source = tmp_path / "images.tar"

        client.load(input_path=source)

        assert argv(runner) == [DOCKER, "load", "-i", str(source)]

    def test_paths_are_stringified_not_left_as_path_objects(self, client, runner, tmp_path):
        client.load(input_path=tmp_path / "images.tar")

        assert all(isinstance(part, str) for part in argv(runner))


class TestNetworkLs:
    def test_argv_requests_only_the_name_column(self, client, runner):
        runner.return_value = _output([])

        client.network_ls()

        assert argv(runner) == [DOCKER, "network", "ls", "--format", "{{.Name}}"]

    def test_names_are_stripped_and_blank_lines_dropped(self, client, runner):
        runner.return_value = _output(["  bridge  ", "", "   ", "host\n"])

        assert client.network_ls() == ["bridge", "host"]

    def test_no_networks_returns_an_empty_list(self, client, runner):
        runner.return_value = _output([])

        assert client.network_ls() == []

    def test_a_custom_format_replaces_the_default(self, client, runner):
        runner.return_value = _output([])

        client.network_ls(format="{{.ID}}")

        assert argv(runner) == [DOCKER, "network", "ls", "--format", "{{.ID}}"]


class TestNetworkInspect:
    def test_network_name_precedes_the_format_flag(self, client, runner):
        runner.return_value = _output(["[]"])

        client.network_inspect(network_name="fm-network")

        assert argv(runner) == [
            DOCKER,
            "network",
            "inspect",
            "fm-network",
            "--format",
            "{{json .IPAM.Config}}",
        ]

    def test_joined_stdout_is_parsed_as_one_json_document(self, client, runner):
        runner.return_value = _output(['[{"Subnet":', '"10.0.0.0/24"}]'])

        assert client.network_inspect(network_name="fm") == [{"Subnet": "10.0.0.0/24"}]

    def test_a_missing_network_degrades_to_an_empty_list(self, client, runner):
        runner.side_effect = DockerException([DOCKER, "network", "inspect"], _output(exit_code=1))

        assert client.network_inspect(network_name="ghost") == []

    def test_empty_stdout_degrades_to_an_empty_list(self, client, runner):
        runner.return_value = _output([])

        assert client.network_inspect(network_name="fm") == []


class TestContainerInspect:
    def test_container_name_precedes_the_format_flag(self, client, runner):
        runner.return_value = _output(["[{}]"])

        client.container_inspect(container_name="fm-nginx")

        assert argv(runner) == [
            DOCKER,
            "inspect",
            "fm-nginx",
            "--format",
            "{{json .NetworkSettings.Networks}}",
        ]

    def test_a_json_array_is_unwrapped_to_its_first_element(self, client, runner):
        runner.return_value = _output(['[{"fm-network": {"IPAddress": "10.0.0.2"}}, {"ignored": 1}]'])

        assert client.container_inspect(container_name="fm") == {"fm-network": {"IPAddress": "10.0.0.2"}}

    def test_a_bare_json_object_is_returned_as_is(self, client, runner):
        runner.return_value = _output(['{"fm-network": {}}'])

        assert client.container_inspect(container_name="fm") == {"fm-network": {}}

    def test_a_missing_container_degrades_to_an_empty_dict(self, client, runner):
        runner.side_effect = DockerException([DOCKER, "inspect"], _output(exit_code=1))

        assert client.container_inspect(container_name="ghost") == {}

    def test_empty_stdout_degrades_to_an_empty_dict(self, client, runner):
        runner.return_value = _output([])

        assert client.container_inspect(container_name="fm") == {}

    def test_an_empty_json_array_returns_a_list_despite_the_dict_annotation(self, client, runner):
        """SUSPICION (pinned, not fixed): `[]` bypasses the unwrap and is returned verbatim,

        so a caller doing `.get(...)` on the declared `dict` return would raise AttributeError.
        """
        runner.return_value = _output(["[]"])

        assert client.container_inspect(container_name="fm") == []


class TestImageLabels:
    def test_argv_inspects_the_config_labels(self, client, runner):
        runner.return_value = _output(["null"])

        client.image_labels(image="alpine:3.18")

        assert argv(runner) == [DOCKER, "inspect", "alpine:3.18", "--format", "{{json .Config.Labels}}"]
        assert opts(runner) == {"stream": False}

    def test_labels_are_returned_as_a_dict(self, client, runner):
        runner.return_value = _output(['{"org.fm.version": "1.2"}'])

        assert client.image_labels(image="alpine") == {"org.fm.version": "1.2"}

    def test_a_json_null_label_set_becomes_an_empty_dict(self, client, runner):
        """Unlabelled images print `null`; callers must still get a mapping."""
        runner.return_value = _output(["null"])

        assert client.image_labels(image="alpine") == {}

    def test_an_unknown_image_degrades_to_an_empty_dict(self, client, runner):
        runner.side_effect = DockerException([DOCKER, "inspect"], _output(exit_code=1))

        assert client.image_labels(image="ghost") == {}

    def test_empty_stdout_degrades_to_an_empty_dict(self, client, runner):
        runner.return_value = _output([])

        assert client.image_labels(image="alpine") == {}


class TestImages:
    def test_argv_requests_json_and_never_streams(self, client, runner):
        runner.return_value = _output([])

        client.images()

        assert argv(runner) == [DOCKER, "images", "--format", "json"]
        assert opts(runner) == {"stream": False}

    def test_each_stdout_line_is_parsed_as_its_own_json_document(self, client, runner):
        """`docker images --format json` emits JSON lines, not one array."""
        runner.return_value = _output(['{"Repository": "alpine"}', '{"Repository": "nginx"}'])

        assert client.images() == [{"Repository": "alpine"}, {"Repository": "nginx"}]

    def test_no_images_returns_an_empty_list(self, client, runner):
        runner.return_value = _output([])

        assert client.images() == []


class TestTag:
    def test_source_then_target_positional_order(self, client, runner):
        client.tag(source_image="alpine:3.18", target_image="reg.local/alpine:prod")

        assert argv(runner) == [DOCKER, "tag", "alpine:3.18", "reg.local/alpine:prod"]
        assert opts(runner) == {"stream": False}


class TestRmi:
    def test_a_single_image_string_becomes_one_positional(self, client, runner):
        client.rmi(image="alpine:old")

        assert argv(runner) == [DOCKER, "rmi", "alpine:old"]

    def test_a_list_of_images_is_expanded_not_stringified(self, client, runner):
        client.rmi(image=["img1", "img2", "img3"])

        assert argv(runner) == [DOCKER, "rmi", "img1", "img2", "img3"]

    def test_flags_precede_the_images(self, client, runner):
        client.rmi(image=["img1", "img2"], force=True, no_prune=True)

        assert argv(runner) == [DOCKER, "rmi", "--force", "--no-prune", "img1", "img2"]

    def test_no_prune_uses_a_hyphen_not_an_underscore(self, client, runner):
        client.rmi(image="img", no_prune=True)

        assert argv(runner) == [DOCKER, "rmi", "--no-prune", "img"]


class TestCreateTempContainer:
    def test_an_explicit_name_is_used_verbatim(self, client):
        container = client.create_temp_container(image="alpine", name="my-temp")

        assert isinstance(container, TempContainer)
        assert container.name == "my-temp"
        assert container.image == "alpine"
        assert container.docker is client
        assert container.run_kwargs == {}

    def test_an_omitted_name_is_generated_with_a_temp_prefix(self, client):
        with patch("frappe_manager.utils.docker.generate_random_text", return_value="abc1234567") as gen:
            container = client.create_temp_container(image="alpine")

        gen.assert_called_once_with(10)
        assert container.name == "temp_abc1234567"

    def test_extra_kwargs_are_captured_for_the_eventual_run(self, client):
        container = client.create_temp_container(image="alpine", name="t", volume=["/a:/b"], user="root")

        assert container.run_kwargs == {"volume": ["/a:/b"], "user": "root"}

    def test_creating_the_wrapper_does_not_touch_the_subprocess_boundary(self, client, runner):
        client.create_temp_container(image="alpine", name="t")

        runner.assert_not_called()


class TestTempContainerLifecycle:
    @pytest.mark.timeout(15)
    def test_entering_starts_a_detached_sleeper_and_returns_self(self, client, runner):
        container = client.create_temp_container(image="alpine", name="tmp1")

        with container as entered:
            assert entered is container
            assert argv(runner) == [
                DOCKER,
                "run",
                "--name",
                "tmp1",
                "--detach",
                "--entrypoint",
                "bash",
                "--pull",
                "missing",
                "alpine",
                "tail",
                "-f",
                "/dev/null",
            ]
            assert opts(runner) == {"stream": False}

    @pytest.mark.timeout(15)
    def test_exiting_force_removes_the_container(self, client, runner):
        with client.create_temp_container(image="alpine", name="tmp1"):
            pass

        assert argv(runner) == [DOCKER, "rm", "--force", "tmp1"]
        assert opts(runner) == {"stream": False}

    @pytest.mark.timeout(15)
    def test_run_kwargs_are_merged_into_the_start_argv(self, client, runner):
        with client.create_temp_container(image="alpine", name="tmp1", user="root", volume=["/h:/c"]):
            start_argv = argv(runner)

        assert start_argv == [
            DOCKER,
            "run",
            "--name",
            "tmp1",
            "--user",
            "root",
            "--volume",
            "/h:/c",
            "--detach",
            "--entrypoint",
            "bash",
            "--pull",
            "missing",
            "alpine",
            "tail",
            "-f",
            "/dev/null",
        ]

    @pytest.mark.timeout(15)
    def test_a_failed_start_is_translated_into_a_runtime_error(self, client, runner):
        runner.side_effect = DockerException([DOCKER, "run"], _output(exit_code=125))

        with (
            pytest.raises(RuntimeError, match="Failed to create temp container"),
            client.create_temp_container(image="alpine", name="tmp1"),
        ):
            pass

    @pytest.mark.timeout(15)
    def test_a_container_that_never_started_is_not_removed(self, client, runner):
        container = client.create_temp_container(image="alpine", name="tmp1")

        assert container.__exit__(None, None, None) is False
        runner.assert_not_called()

    @pytest.mark.timeout(15)
    def test_cleanup_failures_are_swallowed_best_effort(self, client, runner):
        container = client.create_temp_container(image="alpine", name="tmp1")
        container.__enter__()
        runner.side_effect = DockerException([DOCKER, "rm"], _output(exit_code=1))

        assert container.__exit__(None, None, None) is False

    @pytest.mark.timeout(15)
    def test_the_body_exception_is_not_suppressed_but_cleanup_still_runs(self, client, runner):
        with pytest.raises(ValueError), client.create_temp_container(image="alpine", name="tmp1"):
            raise ValueError("boom")

        assert argv(runner) == [DOCKER, "rm", "--force", "tmp1"]


class TestComposeDispatch:
    """Compose work reaches docker through the client's wrapper; pin the argv prefix it produces."""

    @pytest.fixture
    def compose_runner(self):
        with patch("frappe_manager.docker.docker_compose.run_command_with_exit_code") as mock:
            mock.return_value = _output()
            yield mock

    def test_a_compose_operation_is_prefixed_with_the_clients_compose_file(self, tmp_path, compose_runner):
        compose_file = tmp_path / "docker-compose.yml"
        c = DockerClient(compose_file)

        c.compose.ps(all=True, service=["frappe"])

        assert argv(compose_runner) == [
            DOCKER,
            "compose",
            "-f",
            compose_file.as_posix(),
            "ps",
            "--all",
            "frappe",
        ]
        assert opts(compose_runner) == {"stream": False}

    def test_a_sibling_override_file_is_appended_after_the_base(self, tmp_path, compose_runner):
        compose_file = tmp_path / "docker-compose.yml"
        override = tmp_path / "docker-compose.override.yml"
        override.write_text("services: {}\n")
        c = DockerClient(compose_file)

        c.compose.ps()

        assert argv(compose_runner) == [
            DOCKER,
            "compose",
            "-f",
            compose_file.as_posix(),
            "-f",
            override.as_posix(),
            "ps",
        ]

    def test_compose_calls_never_reach_the_plain_docker_boundary(self, tmp_path, runner, compose_runner):
        c = DockerClient(tmp_path / "docker-compose.yml")

        c.compose.ps()

        runner.assert_not_called()
        compose_runner.assert_called_once()
