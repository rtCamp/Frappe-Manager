"""Characterization of `ServicesManager`: the lifecycle of the shared global services stack.

`services.py` manages the one global-db + global-nginx-proxy stack that every bench on the machine
depends on. It is the only place that decides:

* whether the stack must be **created** at all (`self.path.exists()`), and what happens when that
  creation fails (`ServicesNotCreated`, chained from the original error);
* whether a compose file that should exist has vanished (`ServicesComposeNotExist`);
* whether the stack must be **started** before a command can run, and the deliberate exemption for
  `fm service ...` subcommands which must be able to act on a stopped stack;
* the difference between a service being **absent**, **stopped** and **running** -- three states
  that `is_service_running` collapses into one boolean, and which drive whether `compose up` is
  issued at all;
* the platform split (Darwin gets a different template and no explicit container user).

None of that is covered elsewhere, and a refactor here breaks every bench at once. These tests pin
current behaviour, mocking docker at the `DockerClient`/`ComposeFile` seam and using `tmp_path` for
the filesystem. No test reaches a docker daemon, a network, or a real ~/frappe.
"""

import contextlib
import os
from pathlib import Path
from unittest import mock

import pytest

from frappe_manager import GLOBAL_DB_IMAGE
from frappe_manager.docker.docker_exceptions import DockerException
from frappe_manager.docker.subprocess_output import SubprocessOutput
from frappe_manager.output_manager.rich_output import RichOutputHandler
from frappe_manager.services_manager.services import ServicesManager
from frappe_manager.services_manager.services_exceptions import (
    ServicesComposeNotExist,
    ServicesException,
    ServicesNotCreated,
)

SERVICES_MODULE = "frappe_manager.services_manager.services"


# --- helpers ---


def make_manager(
    path: Path,
    *,
    services=("global-db", "global-nginx-proxy"),
    containers=None,
    statuses=None,
    invoked_subcommand: str | None = None,
) -> ServicesManager:
    """A ServicesManager with the collaborators `init()` would have installed, all mocked."""
    manager = ServicesManager(
        path=path,
        invoked_subcommand=invoked_subcommand,
        output_handler=mock.MagicMock(),
    )
    containers = (
        containers
        if containers is not None
        else {"global-db": "fm-global-db", "global-nginx-proxy": "fm-global-nginx-proxy"}
    )
    manager.compose_file_manager = mock.MagicMock()
    manager.compose_file_manager.get_services_list.return_value = list(services)
    manager.compose_file_manager.get_container_names.return_value = containers
    manager.docker_client = mock.MagicMock()
    manager.docker_client.compose.get_all_services_status.return_value = (
        statuses
        if statuses is not None
        else [{"Name": container, "Service": service, "State": "running"} for service, container in containers.items()]
    )
    return manager


def running_statuses(containers: dict, states: dict) -> list[dict]:
    return [
        {"Name": container, "Service": service, "State": states[service]}
        for service, container in containers.items()
        if service in states
    ]


def write_compose(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    compose = path / "docker-compose.yml"
    compose.write_text("services: {}\n")
    return compose


# --- construction ---


def test_the_compose_path_is_always_derived_from_the_services_directory(tmp_path):
    manager = ServicesManager(path=tmp_path / "services", output_handler=mock.MagicMock())

    assert manager.compose_path == tmp_path / "services" / "docker-compose.yml"


def test_the_invoked_subcommand_is_remembered_because_it_gates_auto_start(tmp_path):
    assert ServicesManager(path=tmp_path, output_handler=mock.MagicMock()).invoked_subcommand is None
    assert (
        ServicesManager(
            path=tmp_path, invoked_subcommand="services", output_handler=mock.MagicMock()
        ).invoked_subcommand
        == "services"
    )


def test_a_manager_without_an_output_handler_gets_its_own_rich_handler(tmp_path):
    assert isinstance(ServicesManager(path=tmp_path).output, RichOutputHandler)


# --- init(): wiring and the platform split ---


class InitHarness:
    """The collaborators `init()` constructs, mocked at the services module seam."""

    def __init__(self, stack: contextlib.ExitStack):
        enter = stack.enter_context
        self.compose_file = enter(mock.patch(f"{SERVICES_MODULE}.ComposeFile"))
        self.docker_client = enter(mock.patch(f"{SERVICES_MODULE}.DockerClient"))
        self.proxy_storage = enter(mock.patch(f"{SERVICES_MODULE}.ProxyStoragePaths"))
        self.nginx_controller = enter(mock.patch(f"{SERVICES_MODULE}.NginxController"))
        self.write_headers = enter(mock.patch.object(ServicesManager, "set_frappe_headers_conf"))


@contextlib.contextmanager
def init_harness(system: str = "Linux"):
    with contextlib.ExitStack() as stack:
        stack.enter_context(mock.patch(f"{SERVICES_MODULE}.platform.system", return_value=system))
        yield InitHarness(stack)


@pytest.mark.parametrize(
    ("system", "expected_template"),
    [("Darwin", "docker-compose.services.osx.tmpl"), ("Linux", "docker-compose.services.tmpl")],
)
def test_init_picks_the_compose_template_that_matches_the_host_platform(tmp_path, system, expected_template):
    with init_harness(system) as harness:
        ServicesManager(path=tmp_path, output_handler=mock.MagicMock()).init()

    assert harness.compose_file.call_args.args[0] == tmp_path / "docker-compose.yml"
    assert harness.compose_file.call_args.kwargs["template_name"] == expected_template


def test_init_points_the_docker_client_at_the_services_compose_file(tmp_path):
    output = mock.MagicMock()
    with init_harness() as harness:
        ServicesManager(path=tmp_path, output_handler=output).init()

    harness.docker_client.assert_called_once_with(
        compose_file_path=tmp_path / "docker-compose.yml",
        output=output,
    )


def test_init_wires_the_proxy_against_the_global_nginx_proxy_service(tmp_path):
    with init_harness() as harness:
        ServicesManager(path=tmp_path, output_handler=mock.MagicMock()).init()

    compose_file = harness.compose_file.return_value
    harness.proxy_storage.assert_called_once_with("global-nginx-proxy", compose_file)
    harness.nginx_controller.assert_called_once_with(
        "global-nginx-proxy",
        compose_file,
        harness.docker_client.return_value,
    )


def test_init_keeps_the_legacy_proxy_manager_facade_pointing_at_the_new_collaborators(tmp_path):
    """Older call sites still say `services.proxy_manager.restart()`; that must stay wired."""
    with init_harness() as harness:
        manager = ServicesManager(path=tmp_path, output_handler=mock.MagicMock())
        manager.init()

    assert manager.proxy_manager.dirs is harness.proxy_storage.return_value.dirs
    assert manager.proxy_manager.restart is harness.nginx_controller.return_value.restart
    assert manager.proxy_manager.reload is harness.nginx_controller.return_value.reload


def test_init_writes_the_fm_headers_conf_into_the_proxy_confd_mount(tmp_path):
    with init_harness() as harness:
        harness.proxy_storage.return_value.dirs.confd.host = tmp_path / "confd"
        manager = ServicesManager(path=tmp_path, output_handler=mock.MagicMock())
        manager.init()

    assert manager.fm_headers_path == tmp_path / "confd" / "fm_headers.conf"
    harness.write_headers.assert_called_once_with()


# --- set_frappe_headers_conf ---


def test_the_headers_conf_is_rendered_with_the_running_fm_version(tmp_path):
    template = tmp_path / "fm_headers.conf.tmpl"
    template.write_text("add_header X-Fm {{ current_version }};")
    confd = tmp_path / "confd"
    confd.mkdir()

    manager = ServicesManager(path=tmp_path, output_handler=mock.MagicMock())
    manager.fm_headers_path = confd / "fm_headers.conf"
    with (
        mock.patch(f"{SERVICES_MODULE}.get_template_path", return_value=template),
        mock.patch(f"{SERVICES_MODULE}.get_current_fm_version", return_value="1.2.3"),
    ):
        manager.set_frappe_headers_conf()

    assert manager.fm_headers_path.read_text() == "add_header X-Fm v1.2.3;"


def test_no_headers_conf_is_written_when_the_proxy_confd_mount_does_not_exist_yet(tmp_path):
    manager = ServicesManager(path=tmp_path, output_handler=mock.MagicMock())
    manager.fm_headers_path = tmp_path / "absent" / "fm_headers.conf"

    with mock.patch(f"{SERVICES_MODULE}.get_template_path") as template_path:
        manager.set_frappe_headers_conf()

    template_path.assert_not_called()
    assert not manager.fm_headers_path.exists()


# --- exists() ---


def test_the_stack_exists_only_when_its_compose_file_is_on_disk(tmp_path):
    manager = ServicesManager(path=tmp_path, output_handler=mock.MagicMock())
    assert manager.exists() is False

    write_compose(tmp_path)
    assert manager.exists() is True


# --- entrypoint_checks: creation branch ---


def _patch_database_manager():
    return (
        mock.patch(f"{SERVICES_MODULE}.MariaDBManager"),
        mock.patch(f"{SERVICES_MODULE}.DatabaseServerServiceInfo"),
    )


def test_a_missing_services_directory_triggers_a_clean_creation_and_an_image_pull(tmp_path):
    services_path = tmp_path / "services"
    manager = make_manager(services_path)
    maria, info = _patch_database_manager()
    with maria, info, mock.patch.object(ServicesManager, "create") as create:
        create.side_effect = lambda **_: write_compose(services_path)
        manager.entrypoint_checks()

    create.assert_called_once_with(clean_install=True)
    manager.docker_client.compose.pull.assert_called_once_with(stream=False)


def test_creation_without_start_never_brings_the_stack_up(tmp_path):
    services_path = tmp_path / "services"
    manager = make_manager(services_path)
    maria, info = _patch_database_manager()
    with maria, info, mock.patch.object(ServicesManager, "create") as create:
        create.side_effect = lambda **_: write_compose(services_path)
        manager.entrypoint_checks(start=False)

    manager.docker_client.compose.up.assert_not_called()


def test_creation_with_start_brings_the_stack_up_without_pulling_again(tmp_path):
    """`pull="never"` -- the explicit pull above already fetched the images."""
    services_path = tmp_path / "services"
    manager = make_manager(services_path)
    maria, info = _patch_database_manager()
    with maria, info, mock.patch.object(ServicesManager, "create") as create:
        create.side_effect = lambda **_: write_compose(services_path)
        manager.entrypoint_checks(start=True)

    assert manager.docker_client.compose.up.call_args_list == [
        mock.call(services=[], detach=True, pull="never"),
    ]


def test_a_failed_creation_is_reported_as_services_not_created_and_keeps_the_original_cause(tmp_path):
    manager = make_manager(tmp_path / "services")
    cause = RuntimeError("disk full")
    maria, info = _patch_database_manager()
    with (
        maria,
        info,
        mock.patch.object(ServicesManager, "create", side_effect=cause),
        pytest.raises(ServicesNotCreated) as excinfo,
    ):
        manager.entrypoint_checks()

    assert excinfo.value.__cause__ is cause
    manager.output.error.assert_called_once()
    manager.docker_client.compose.pull.assert_not_called()


def test_an_existing_services_directory_is_never_recreated(tmp_path):
    write_compose(tmp_path)
    manager = make_manager(tmp_path)
    maria, info = _patch_database_manager()
    with maria, info, mock.patch.object(ServicesManager, "create") as create:
        manager.entrypoint_checks()

    create.assert_not_called()
    manager.docker_client.compose.pull.assert_not_called()


# --- entrypoint_checks: missing compose file ---


def test_a_services_directory_without_a_compose_file_is_a_hard_error(tmp_path):
    tmp_path.joinpath("marker").write_text("")
    manager = make_manager(tmp_path)
    maria, info = _patch_database_manager()
    with maria, info, pytest.raises(ServicesComposeNotExist) as excinfo:
        manager.entrypoint_checks()

    assert str(manager.compose_path) in str(excinfo.value)


# --- entrypoint_checks: start branch ---


def test_a_fully_running_stack_is_left_alone(tmp_path):
    write_compose(tmp_path)
    manager = make_manager(tmp_path)
    maria, info = _patch_database_manager()
    with maria, info:
        manager.entrypoint_checks(start=True)

    manager.docker_client.compose.up.assert_not_called()
    manager.compose_file_manager.get_services_list.assert_any_call(exclude_disabled=True)


def test_a_partly_stopped_stack_is_brought_up_pulling_only_what_is_missing(tmp_path):
    write_compose(tmp_path)
    containers = {"global-db": "fm-global-db", "global-nginx-proxy": "fm-global-nginx-proxy"}
    manager = make_manager(
        tmp_path,
        containers=containers,
        statuses=running_statuses(containers, {"global-db": "running", "global-nginx-proxy": "exited"}),
    )
    maria, info = _patch_database_manager()
    with maria, info:
        manager.entrypoint_checks(start=True)

    manager.docker_client.compose.up.assert_called_once_with(services=[], detach=True, pull="missing")


def test_a_service_with_no_container_row_at_all_counts_as_not_running(tmp_path):
    """Absent is not the same as stopped, but both must lead to a start."""
    write_compose(tmp_path)
    containers = {"global-db": "fm-global-db", "global-nginx-proxy": "fm-global-nginx-proxy"}
    manager = make_manager(
        tmp_path,
        containers=containers,
        statuses=running_statuses(containers, {"global-db": "running"}),
    )
    maria, info = _patch_database_manager()
    with maria, info:
        manager.entrypoint_checks(start=True)

    manager.docker_client.compose.up.assert_called_once_with(services=[], detach=True, pull="missing")


def test_status_rows_for_foreign_containers_are_ignored(tmp_path):
    """`get_all_services_status` can report containers that are not part of this stack."""
    write_compose(tmp_path)
    containers = {"global-db": "fm-global-db", "global-nginx-proxy": "fm-global-nginx-proxy"}
    statuses = running_statuses(containers, {"global-db": "running", "global-nginx-proxy": "running"})
    statuses.append({"Name": "some-other-bench", "Service": "global-db", "State": "exited"})
    manager = make_manager(tmp_path, containers=containers, statuses=statuses)
    maria, info = _patch_database_manager()
    with maria, info:
        manager.entrypoint_checks(start=True)

    manager.docker_client.compose.up.assert_not_called()


@pytest.mark.parametrize("family", ["services", "self"])
def test_the_services_and_self_command_families_are_exempt_from_auto_start(tmp_path, family):
    """`fm services stop global-db` and `fm self stop` must act on a stopped stack, not start it.

    This used to be pinned as `invoked_subcommand="service"`, which no command ever produces: the
    root callback passes `ctx.invoked_subcommand`, and for a sub-Typer registered as
    `add_typer(services_app, name="services")` that value is the GROUP name. The guard was
    therefore dead, and `fm services stop global-db` first ran `compose up` for both globals --
    restarting a `global-nginx-proxy` the operator had deliberately stopped.
    """
    write_compose(tmp_path)
    containers = {"global-db": "fm-global-db"}
    manager = make_manager(
        tmp_path,
        containers=containers,
        statuses=running_statuses(containers, {"global-db": "exited"}),
        invoked_subcommand=family,
    )
    maria, info = _patch_database_manager()
    with maria, info:
        manager.entrypoint_checks(start=True)

    manager.docker_client.compose.up.assert_not_called()
    manager.docker_client.compose.get_all_services_status.assert_not_called()


@pytest.mark.parametrize("family", ["start", "create", None])
def test_every_other_command_still_gets_the_stack_started(tmp_path, family):
    """The exemption is narrow: a bench command on a stopped stack must still bring it up."""
    write_compose(tmp_path)
    containers = {"global-db": "fm-global-db"}
    manager = make_manager(
        tmp_path,
        containers=containers,
        statuses=running_statuses(containers, {"global-db": "exited"}),
        invoked_subcommand=family,
    )
    maria, info = _patch_database_manager()
    with maria, info:
        manager.entrypoint_checks(start=True)

    manager.docker_client.compose.up.assert_called_once_with(services=[], detach=True, pull="missing")


def test_a_stopped_stack_is_not_started_when_start_was_not_requested(tmp_path):
    write_compose(tmp_path)
    containers = {"global-db": "fm-global-db"}
    manager = make_manager(
        tmp_path,
        containers=containers,
        statuses=running_statuses(containers, {"global-db": "exited"}),
    )
    maria, info = _patch_database_manager()
    with maria, info:
        manager.entrypoint_checks(start=False)

    manager.docker_client.compose.up.assert_not_called()


def test_entrypoint_checks_always_installs_a_database_manager_for_the_global_db(tmp_path):
    write_compose(tmp_path)
    manager = make_manager(tmp_path)
    maria, info = _patch_database_manager()
    with maria as maria_cls, info as info_cls:
        manager.entrypoint_checks()

    info_cls.import_from_compose_file.assert_called_once_with("global-db", manager.compose_file_manager)
    maria_cls.assert_called_once_with(
        info_cls.import_from_compose_file.return_value,
        manager.compose_file_manager,
        manager.docker_client,
        output_handler=manager.output,
    )
    assert manager.database_manager is maria_cls.return_value


# --- is_service_running: absent vs stopped vs running ---


def test_a_running_service_is_reported_running(tmp_path):
    containers = {"global-db": "fm-global-db"}
    manager = make_manager(
        tmp_path, containers=containers, statuses=running_statuses(containers, {"global-db": "running"})
    )

    assert manager.is_service_running("global-db") is True


def test_a_stopped_service_is_reported_not_running(tmp_path):
    containers = {"global-db": "fm-global-db"}
    manager = make_manager(
        tmp_path, containers=containers, statuses=running_statuses(containers, {"global-db": "exited"})
    )

    assert manager.is_service_running("global-db") is False


def test_a_service_absent_from_the_compose_file_is_reported_not_running(tmp_path):
    manager = make_manager(tmp_path, containers={"global-db": "fm-global-db"})

    assert manager.is_service_running("global-nginx-proxy") is False


def test_a_declared_service_with_no_container_row_is_reported_not_running(tmp_path):
    manager = make_manager(tmp_path, containers={"global-db": "fm-global-db"}, statuses=[])

    assert manager.is_service_running("global-db") is False


# --- start / stop / restart ---


def test_starting_with_no_names_starts_the_whole_stack_without_pulling(tmp_path):
    manager = make_manager(tmp_path)

    manager.start_service()

    manager.docker_client.compose.up.assert_called_once_with(
        services=[], detach=True, pull="never", force_recreate=False
    )


def test_starting_named_services_passes_them_through_and_can_force_a_recreate(tmp_path):
    manager = make_manager(tmp_path)

    manager.start_service(["global-db"], force_recreate=True)

    manager.docker_client.compose.up.assert_called_once_with(
        services=["global-db"], detach=True, pull="never", force_recreate=True
    )


def test_stopping_uses_a_bounded_default_timeout_and_honours_an_override(tmp_path):
    manager = make_manager(tmp_path)

    manager.stop_service()
    manager.stop_service(["global-db"], timeout=1)

    assert manager.docker_client.compose.stop.call_args_list == [
        mock.call(services=[], timeout=10),
        mock.call(services=["global-db"], timeout=1),
    ]


def test_restarting_with_no_names_restarts_the_whole_stack(tmp_path):
    manager = make_manager(tmp_path)

    manager.restart_service()

    manager.docker_client.compose.restart.assert_called_once_with(services=[])


# --- generate_compose ---


def test_generate_compose_converts_user_dicts_into_uid_gid_pairs_and_commits_once(tmp_path):
    manager = make_manager(tmp_path)
    cf = manager.compose_file_manager

    manager.generate_compose(
        {
            "environment": {"global-db": {"A": "1"}},
            "labels": {"global-db": {"L": "1"}},
            "user": {"global-db": {"uid": 501, "gid": 20}},
        }
    )

    cf.with_envs.assert_called_once_with({"global-db": {"A": "1"}})
    cf.with_labels.assert_called_once_with({"global-db": {"L": "1"}})
    cf.with_users.assert_called_once_with({"global-db": (501, 20)})
    cf.commit.assert_called_once_with()


def test_generate_compose_with_nothing_to_apply_never_commits(tmp_path):
    manager = make_manager(tmp_path)

    manager.generate_compose({})

    manager.compose_file_manager.commit.assert_not_called()
    manager.compose_file_manager.with_envs.assert_not_called()


def test_any_failure_while_building_the_compose_file_becomes_services_not_created(tmp_path):
    manager = make_manager(tmp_path)
    manager.compose_file_manager.with_envs.side_effect = KeyError("services")

    with pytest.raises(ServicesNotCreated):
        manager.generate_compose({"environment": {"global-db": {"A": "1"}}})


# --- shell / remove_itself ---


def test_a_shell_without_a_user_does_not_pass_one_to_compose_exec(tmp_path):
    manager = make_manager(tmp_path)

    manager.shell("global-db")

    manager.output.stop.assert_called_once_with()
    manager.docker_client.compose.exec.assert_called_once_with("global-db", command="/bin/bash", capture_output=False)


def test_a_shell_with_a_user_execs_as_that_user(tmp_path):
    manager = make_manager(tmp_path)

    manager.shell("global-db", user="frappe")

    manager.docker_client.compose.exec.assert_called_once_with(
        "global-db", user="frappe", command="/bin/bash", capture_output=False
    )


def test_a_nonzero_shell_exit_is_a_warning_not_a_crash(tmp_path):
    manager = make_manager(tmp_path)
    manager.docker_client.compose.exec.side_effect = DockerException(
        ["docker", "compose", "exec"],
        SubprocessOutput(stdout=[], stderr=[], combined=[], exit_code=130),
    )

    manager.shell("global-db")

    assert "130" in manager.output.warning.call_args.args[0]


def test_removing_the_stack_deletes_the_whole_services_directory(tmp_path):
    services_path = tmp_path / "services"
    (services_path / "mariadb").mkdir(parents=True)
    (services_path / "mariadb" / "x").write_text("data")
    manager = ServicesManager(path=services_path, output_handler=mock.MagicMock())

    manager.remove_itself()

    assert not services_path.exists()


# --- create(): the parts that decide what lands on disk ---


def _create_manager(tmp_path: Path) -> ServicesManager:
    manager = make_manager(tmp_path / "services")
    manager.compose_file_manager.yml = {
        "services": {"global-nginx-proxy": {"networks": ["global-frontend-network"]}},
        "networks": {"global-frontend-network": {"ipam": {"config": [{"subnet": "10.0.0.0/24"}]}}},
    }
    return manager


class CreateHarness:
    """Every collaborator seam `create()` reaches through, mocked; nothing touches a daemon."""

    def __init__(self, stack: contextlib.ExitStack, tmp_path: Path, system: str, configured: bool):
        self.stack = stack
        enter = stack.enter_context

        self.fm_config = mock.MagicMock()
        self.fm_config.network.configured = configured
        self.fm_config.network.subnet_cidr = "10.5.0.0/24"
        self.fm_config.network.proxy_ip = "10.5.0.2"

        enter(mock.patch(f"{SERVICES_MODULE}.platform.system", return_value=system))
        enter(mock.patch(f"{SERVICES_MODULE}.CLI_DIR", tmp_path / "cli"))
        enter(mock.patch(f"{SERVICES_MODULE}.FMConfigManager.import_from_toml", return_value=self.fm_config))
        enter(mock.patch.object(ServicesManager, "set_frappe_headers_conf"))
        enter(
            mock.patch(
                f"{SERVICES_MODULE}.random_password_generate",
                side_effect=lambda **kw: "p" * kw["password_length"],
            )
        )
        self.host_run_cp = enter(mock.patch(f"{SERVICES_MODULE}.host_run_cp"))
        self.get_unix_groups = enter(mock.patch(f"{SERVICES_MODULE}.get_unix_groups", return_value={"docker": 999}))
        self.detect_running_network = enter(mock.patch(f"{SERVICES_MODULE}.detect_running_network", return_value=None))
        self.get_docker_network_subnets = enter(
            mock.patch(f"{SERVICES_MODULE}.get_docker_network_subnets", return_value=["10.0.0.0/24"])
        )
        self.find_available_subnet = enter(
            mock.patch(f"{SERVICES_MODULE}.find_available_subnet", return_value="10.7.0.0/24")
        )
        self.compute_network_config = enter(
            mock.patch(
                f"{SERVICES_MODULE}.compute_network_config",
                return_value={"subnet_cidr": "10.7.0.0/24", "proxy_ip": "10.7.0.2"},
            )
        )
        self.pick_proxy_ip = enter(mock.patch(f"{SERVICES_MODULE}.pick_proxy_ip", return_value="10.9.0.9"))

    def stub_generate_compose(self):
        return self.stack.enter_context(mock.patch.object(ServicesManager, "generate_compose"))


@contextlib.contextmanager
def create_harness(tmp_path: Path, *, system: str = "Linux", configured: bool = True):
    with contextlib.ExitStack() as stack:
        yield CreateHarness(stack, tmp_path, system, configured)


def test_create_lays_out_every_directory_the_stack_mounts(tmp_path):
    manager = _create_manager(tmp_path)

    with create_harness(tmp_path):
        manager.create()

    for folder in ("mariadb/conf", "mariadb/logs", "secrets", "nginx-proxy/certs", "nginx-proxy/confd"):
        assert (manager.path / folder).is_dir(), folder


def test_create_adds_a_host_data_dir_on_linux_but_not_on_darwin(tmp_path):
    linux = _create_manager(tmp_path / "linux")
    with create_harness(tmp_path / "linux", system="Linux"):
        linux.create()
    assert (linux.path / "mariadb" / "data").is_dir()

    darwin = _create_manager(tmp_path / "osx")
    with create_harness(tmp_path / "osx", system="Darwin"):
        darwin.create()
    assert not (darwin.path / "mariadb" / "data").exists()


def test_create_drops_the_explicit_container_user_on_darwin(tmp_path):
    """Docker Desktop maps uid/gid itself; a pinned user breaks the bind mounts."""
    manager = _create_manager(tmp_path)

    with create_harness(tmp_path, system="Darwin"):
        manager.create()

    removed = [call.args[0] for call in manager.compose_file_manager.remove_container_user.call_args_list]
    assert removed == ["global-nginx-proxy", "global-db"]


def test_create_keeps_the_container_user_on_linux_and_puts_the_proxy_in_the_docker_group(tmp_path):
    manager = _create_manager(tmp_path)

    with create_harness(tmp_path, system="Linux") as harness:
        generate = harness.stub_generate_compose()
        manager.create()

    manager.compose_file_manager.remove_container_user.assert_not_called()
    users = generate.call_args.args[0]["user"]
    assert users["global-nginx-proxy"]["gid"] == 999
    assert users["global-db"]["uid"] == os.getuid()
    assert users["global-db"]["gid"] == os.getgid()


def test_create_gives_the_proxy_no_explicit_user_entry_on_darwin(tmp_path):
    manager = _create_manager(tmp_path)

    with create_harness(tmp_path, system="Darwin") as harness:
        generate = harness.stub_generate_compose()
        manager.create()

    assert set(generate.call_args.args[0]["user"]) == {"global-db"}
    harness.get_unix_groups.assert_not_called()


def test_create_refuses_to_continue_when_the_host_has_no_docker_group(tmp_path):
    manager = _create_manager(tmp_path)

    with create_harness(tmp_path, system="Linux") as harness:
        harness.get_unix_groups.return_value = {}
        with pytest.raises(ServicesException) as excinfo:
            manager.create()

    assert "docker group not found" in str(excinfo.value)


def test_create_seeds_both_database_secrets_and_registers_them_in_the_compose_file(tmp_path):
    manager = _create_manager(tmp_path)

    with create_harness(tmp_path):
        manager.create()

    db_password = manager.path / "secrets" / "db_password.txt"
    db_root_password = manager.path / "secrets" / "db_root_password.txt"
    assert len(db_password.read_text()) == 16
    assert len(db_root_password.read_text()) == 24
    manager.compose_file_manager.set_secret_file_path.assert_has_calls(
        [
            mock.call("db_password", str(db_password.absolute())),
            mock.call("db_root_password", str(db_root_password.absolute())),
        ]
    )
    manager.compose_file_manager.write_to_file.assert_called_once_with()


def test_create_seeds_the_mariadb_config_from_the_same_image_the_compose_file_runs(tmp_path):
    manager = _create_manager(tmp_path)

    with create_harness(tmp_path) as harness:
        manager.create()

    harness.host_run_cp.assert_called_once_with(
        image=GLOBAL_DB_IMAGE,
        source="/etc/mysql/.",
        destination=str((manager.path / "mariadb/conf").absolute()),
        docker=manager.docker_client,
    )


def test_create_declares_the_root_password_as_a_secret_file_never_an_inline_env(tmp_path):
    manager = _create_manager(tmp_path)

    with create_harness(tmp_path) as harness:
        generate = harness.stub_generate_compose()
        manager.create()

    envs = generate.call_args.args[0]["environment"]["global-db"]
    # S105: these are secret *file paths*, which is precisely the point of the assertion.
    assert envs["MYSQL_ROOT_PASSWORD_FILE"] == "/run/secrets/db_root_password"
    assert "MYSQL_ROOT_PASSWORD" not in envs
    assert envs["MYSQL_PASSWORD_FILE"] == "/run/secrets/db_password"


def test_create_pins_the_configured_subnet_and_proxy_ip_into_the_compose_yaml(tmp_path):
    manager = _create_manager(tmp_path)

    with create_harness(tmp_path, configured=True) as harness:
        manager.create()

    yml = manager.compose_file_manager.yml
    assert yml["networks"]["global-frontend-network"]["ipam"]["config"][0]["subnet"] == "10.5.0.0/24"
    nets = yml["services"]["global-nginx-proxy"]["networks"]
    assert nets["global-frontend-network"]["ipv4_address"] == "10.5.0.2"
    # an already configured network is never re-detected or re-allocated
    harness.detect_running_network.assert_not_called()
    harness.find_available_subnet.assert_not_called()


def test_pinning_the_proxy_ip_rewrites_a_list_of_networks_into_a_mapping(tmp_path):
    """The template lists networks by name; a static IP needs the mapping form, and the other
    networks the proxy is on must survive the rewrite."""
    manager = _create_manager(tmp_path)
    manager.compose_file_manager.yml["services"]["global-nginx-proxy"]["networks"] = [
        "global-frontend-network",
        "global-backend-network",
    ]

    with create_harness(tmp_path):
        manager.create()

    nets = manager.compose_file_manager.yml["services"]["global-nginx-proxy"]["networks"]
    assert nets["global-backend-network"] == {}
    assert nets["global-frontend-network"] == {"ipv4_address": "10.5.0.2"}


def test_pinning_the_proxy_ip_keeps_the_other_settings_on_an_existing_network_entry(tmp_path):
    manager = _create_manager(tmp_path)
    manager.compose_file_manager.yml["services"]["global-nginx-proxy"]["networks"] = {
        "global-frontend-network": {"aliases": ["proxy"]},
    }

    with create_harness(tmp_path):
        manager.create()

    entry = manager.compose_file_manager.yml["services"]["global-nginx-proxy"]["networks"]
    assert entry["global-frontend-network"] == {"aliases": ["proxy"], "ipv4_address": "10.5.0.2"}


def test_a_proxy_with_no_networks_key_at_all_still_gets_its_static_ip(tmp_path):
    manager = _create_manager(tmp_path)
    del manager.compose_file_manager.yml["services"]["global-nginx-proxy"]["networks"]

    with create_harness(tmp_path):
        manager.create()

    nets = manager.compose_file_manager.yml["services"]["global-nginx-proxy"]["networks"]
    assert nets == {"global-frontend-network": {"ipv4_address": "10.5.0.2"}}


def test_a_network_entry_that_is_not_a_mapping_is_replaced_rather_than_crashed_on(tmp_path):
    manager = _create_manager(tmp_path)
    manager.compose_file_manager.yml["services"]["global-nginx-proxy"]["networks"] = {
        "global-frontend-network": None,
    }

    with create_harness(tmp_path):
        manager.create()

    nets = manager.compose_file_manager.yml["services"]["global-nginx-proxy"]["networks"]
    assert nets["global-frontend-network"] == {"ipv4_address": "10.5.0.2"}


def test_a_compose_file_with_no_frontend_network_block_does_not_abort_the_creation(tmp_path):
    """The subnet write is best-effort: a template without that block must still install."""
    manager = _create_manager(tmp_path)
    manager.compose_file_manager.yml["networks"] = {}

    with create_harness(tmp_path):
        manager.create()

    assert (manager.path / "secrets" / "db_password.txt").exists()


def test_a_compose_file_with_no_proxy_service_does_not_abort_the_creation(tmp_path):
    manager = _create_manager(tmp_path)
    manager.compose_file_manager.yml["services"] = {}

    with create_harness(tmp_path):
        manager.create()

    assert (manager.path / "secrets" / "db_root_password.txt").exists()


def test_create_reuses_an_already_running_network_instead_of_allocating_a_new_subnet(tmp_path):
    manager = _create_manager(tmp_path)

    with create_harness(tmp_path, configured=False) as harness:
        harness.detect_running_network.return_value = {"subnet_cidr": "10.9.0.0/24", "proxy_ip": "10.9.0.5"}
        manager.create()

    assert harness.fm_config.network.subnet_cidr == "10.9.0.0/24"
    assert harness.fm_config.network.proxy_ip == "10.9.0.5"
    harness.fm_config.export_to_toml.assert_called_once_with()
    harness.find_available_subnet.assert_not_called()


def test_a_running_network_with_no_attached_proxy_gets_a_free_ip_rather_than_an_empty_one(tmp_path):
    manager = _create_manager(tmp_path)

    with create_harness(tmp_path, configured=False) as harness:
        harness.detect_running_network.return_value = {"subnet_cidr": "10.9.0.0/24", "proxy_ip": None}
        manager.create()

    harness.pick_proxy_ip.assert_called_once_with("10.9.0.0/24", "fm-global-frontend-network")
    assert harness.fm_config.network.proxy_ip == "10.9.0.9"


def test_create_allocates_a_free_subnet_when_no_network_is_running(tmp_path):
    manager = _create_manager(tmp_path)

    with create_harness(tmp_path, configured=False) as harness:
        manager.create()

    harness.find_available_subnet.assert_called_once_with(["10.0.0.0/24"])
    harness.compute_network_config.assert_called_once_with("10.7.0.0/24", "fm-global-frontend-network")
    assert harness.fm_config.network.subnet_cidr == "10.7.0.0/24"
    assert harness.fm_config.network.proxy_ip == "10.7.0.2"
    harness.fm_config.export_to_toml.assert_called_once_with()


def test_create_moves_the_old_stack_aside_when_a_backup_is_asked_for(tmp_path):
    manager = _create_manager(tmp_path)
    manager.path.mkdir(parents=True)
    (manager.path / "old-marker").write_text("keep me")

    with create_harness(tmp_path):
        manager.create(backup=True)

    backups = list((tmp_path / "cli" / "backups").iterdir())
    assert len(backups) == 1
    assert backups[0].name.startswith("services_")
    assert (backups[0] / "old-marker").read_text() == "keep me"


def test_create_wipes_the_old_stack_when_no_backup_is_asked_for(tmp_path):
    manager = _create_manager(tmp_path)
    manager.path.mkdir(parents=True)
    (manager.path / "old-marker").write_text("gone")

    with create_harness(tmp_path):
        manager.create(backup=False)

    assert not (manager.path / "old-marker").exists()
    assert not (tmp_path / "cli" / "backups").exists()


def test_a_clean_install_tears_down_previous_containers_and_volumes(tmp_path):
    manager = _create_manager(tmp_path)

    with create_harness(tmp_path):
        manager.create(clean_install=True)

    manager.docker_client.compose.down.assert_called_once_with(
        remove_orphans=True, timeout=10, volumes=True, stream=False
    )


def test_a_non_clean_install_leaves_existing_containers_and_volumes_alone(tmp_path):
    manager = _create_manager(tmp_path)

    with create_harness(tmp_path):
        manager.create(clean_install=False)

    manager.docker_client.compose.down.assert_not_called()


def test_create_fails_loudly_when_a_required_directory_cannot_be_made(tmp_path):
    manager = _create_manager(tmp_path)
    real_mkdir = Path.mkdir

    def refuse_everything_below_the_root(self, *args, **kwargs):
        if self != manager.path:
            raise PermissionError("read-only fs")
        return real_mkdir(self, *args, **kwargs)

    with (
        create_harness(tmp_path),
        mock.patch.object(Path, "mkdir", refuse_everything_below_the_root),
        pytest.raises(ServicesNotCreated) as excinfo,
    ):
        manager.create()

    assert "Failed to create global services required dir" in str(excinfo.value)
