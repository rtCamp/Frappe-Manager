"""Characterization of the global-database manager: endpoint resolution and the exact SQL/argv it issues.

`database_service_manager.py` owns every credential and every statement fm sends to the shared
MariaDB server that all benches on the machine use. Two things can silently break every bench at
once:

1. **Endpoint resolution.** `DatabaseServerServiceInfo` decides *which* server a password travels
   to. `external` is the switch: False means "a compose service fm owns, exec the client into that
   container", True means "a DNS name fm does not own, run the client from the bench's frappe
   container instead". Getting that default wrong routes the global-db root password at a foreign
   host, or tries to exec into a container that does not exist.
2. **Statement shape.** Every method here is a string-building function around a shell-out. Nothing
   in this file validates its own SQL, so the argv/SQL *is* the contract. These tests pin the exact
   strings and the exact collaborator calls; no query is ever executed.

Also pinned: every guard that refuses a destructive action (`add_user` on an existing user,
`db_export`/`db_import` on a missing database, `remove_user`'s refusal to drop users other than the
one named).

Docker and the filesystem are mocked at their seams. No test reaches a daemon, a network or a real
database.
"""

# SLF001: `_run_user`, `_env`, `_is_service_running` and `_compose_exec_or_run` are exactly the
# routing decisions this module is being characterized for -- they have no public surface.
# S105/S106: every "password" here is a fixture string that never leaves the process.
# ruff: noqa: SLF001, S105, S106

import json
from pathlib import Path
from unittest import mock

import pytest
from pydantic import ValidationError

from frappe_manager.docker import DOCKER_LINE_NOISE
from frappe_manager.docker.docker_exceptions import DockerException
from frappe_manager.docker.subprocess_output import SubprocessOutput
from frappe_manager.services_manager.database_service_manager import (
    DatabaseServerServiceInfo,
    MariaDBManager,
)
from frappe_manager.services_manager.services_exceptions import (
    DatabaseServiceDBCreateFailed,
    DatabaseServiceDBExportFailed,
    DatabaseServiceDBImportFailed,
    DatabaseServiceDBNotFoundError,
    DatabaseServiceDBRemoveFailError,
    DatabaseServiceException,
    DatabaseServicePasswordNotFound,
    DatabaseServiceQueryAccessDenied,
    DatabaseServiceStartTimeout,
    DatabaseServiceUserRemoveFailError,
)
from frappe_manager.site_manager.bench_config import DatabaseConfig
from frappe_manager.site_manager.exceptions import BenchException

# --- helpers ---


def make_output(stdout=(), stderr=(), exit_code=0) -> SubprocessOutput:
    stdout = list(stdout)
    stderr = list(stderr)
    return SubprocessOutput(stdout=stdout, stderr=stderr, combined=stdout + stderr, exit_code=exit_code)


def docker_error(*, stdout=(), stderr=(), exit_code=1) -> DockerException:
    return DockerException(["docker", "compose", "exec"], make_output(stdout, stderr, exit_code))


def make_info(**overrides) -> DatabaseServerServiceInfo:
    fields: dict = {"host": "global-db", "user": "root", "port": 3306, "password": "rootpw"}
    fields.update(overrides)
    return DatabaseServerServiceInfo(**fields)


def make_manager(
    info: DatabaseServerServiceInfo | None = None,
    *,
    running: bool = True,
    container_names: dict | None = None,
    **kwargs,
) -> MariaDBManager:
    """A MariaDBManager whose docker client and compose file are mocks.

    `running` drives `_is_service_running` through the real code path: the compose file reports a
    container name for the service and the docker client reports a matching status row.
    """
    info = info or make_info()
    compose_file_manager = mock.MagicMock()
    names = container_names if container_names is not None else {"global-db": "fm-global-db", "frappe": "fm-frappe"}
    compose_file_manager.get_container_names.return_value = names
    docker_client = mock.MagicMock()
    docker_client.compose.get_all_services_status.return_value = [
        {"Name": container, "Service": service, "State": "running" if running else "exited"}
        for service, container in names.items()
    ]
    return MariaDBManager(
        info,
        compose_file_manager,
        docker_client,
        output_handler=mock.MagicMock(),
        **kwargs,
    )


# --- DatabaseServerServiceInfo: the external switch ---


def test_an_endpoint_is_fm_owned_unless_it_is_explicitly_declared_external():
    """`external` defaults to False: an endpoint is a compose service fm owns until stated otherwise.

    Every caller that builds this object for the global db relies on the default. If it flipped,
    fm would treat its own container as a foreign host.
    """
    assert make_info().external is False
    assert make_info(external=True).external is True


def test_a_fm_owned_endpoint_runs_the_client_inside_the_database_container_itself():
    manager = make_manager(make_info(host="global-db"))
    assert manager.run_on_compose_service == "global-db"
    # the engine image has no `frappe` user; passing one breaks the `compose run` fallback
    assert manager._run_user is None


def test_an_external_endpoint_runs_the_client_from_the_bench_frappe_container():
    """An external host is a DNS name, not a compose service: there is no container to exec into."""
    manager = make_manager(make_info(host="db.example.com", external=True))
    assert manager.run_on_compose_service == "frappe"
    # the bench image does have a frappe user, and `compose run` needs one that exists
    assert manager._run_user == "frappe"


def test_an_explicit_run_on_compose_service_overrides_both_defaults():
    internal = make_manager(make_info(host="global-db"), run_on_compose_service="frappe")
    assert internal.run_on_compose_service == "frappe"
    assert internal._run_user == "frappe"

    external = make_manager(make_info(host="db.example.com", external=True), run_on_compose_service="global-db")
    assert external.run_on_compose_service == "global-db"
    assert external._run_user is None


# --- DatabaseServerServiceInfo.import_from_compose_file ---


def _compose_file_with(envs: dict, secret_text: str | None = None, tmp_path: Path | None = None):
    compose_file_manager = mock.MagicMock()
    compose_file_manager.get_envs.return_value = envs
    if secret_text is not None:
        assert tmp_path is not None
        secret = tmp_path / "db_root_password.txt"
        secret.write_text(secret_text)
        compose_file_manager.get_secret_file_path.return_value = secret
    return compose_file_manager


def test_compose_import_prefers_the_secret_file_over_an_inline_password(tmp_path):
    compose_file_manager = _compose_file_with(
        {"MYSQL_ROOT_PASSWORD_FILE": "/run/secrets/db_root_password", "MYSQL_ROOT_PASSWORD": "inline"},
        secret_text="from-secret-file",
        tmp_path=tmp_path,
    )

    info = DatabaseServerServiceInfo.import_from_compose_file("global-db", compose_file_manager)

    assert info.password == "from-secret-file"
    compose_file_manager.get_secret_file_path.assert_called_once_with("db_root_password")
    compose_file_manager.get_envs.assert_called_once_with(container="global-db")


def test_compose_import_falls_back_to_the_inline_root_password_env():
    compose_file_manager = _compose_file_with({"MYSQL_ROOT_PASSWORD": "inline-pw"})

    info = DatabaseServerServiceInfo.import_from_compose_file("global-db", compose_file_manager)

    assert info.password == "inline-pw"
    compose_file_manager.get_secret_file_path.assert_not_called()


def test_compose_import_hardcodes_root_at_the_service_name_and_the_engine_port():
    """The root password can only ever travel to the compose service it was minted for.

    host is the compose service name and external is False, so this credential cannot be pointed
    at a foreign server by anything downstream.
    """
    compose_file_manager = _compose_file_with({"MYSQL_ROOT_PASSWORD": "inline-pw"})

    info = DatabaseServerServiceInfo.import_from_compose_file("global-db", compose_file_manager)

    assert (info.user, info.host, info.port, info.external) == ("root", "global-db", 3306, False)
    assert info.name is None


def test_compose_import_raises_when_no_password_env_is_present_and_asked_to():
    compose_file_manager = _compose_file_with({"MYSQL_DATABASE": "root"})

    with pytest.raises(DatabaseServicePasswordNotFound) as excinfo:
        DatabaseServerServiceInfo.import_from_compose_file("global-db", compose_file_manager)

    assert excinfo.value.service_name == "global-db"


def test_compose_import_without_raise_exception_still_refuses_to_build_a_passwordless_endpoint():
    """`raise_exception=False` suppresses only the friendly error; pydantic still rejects the model.

    Pinned as-is: there is no code path that yields an endpoint object with no password.
    """
    compose_file_manager = _compose_file_with({"MYSQL_DATABASE": "root"})

    with pytest.raises(ValidationError):
        DatabaseServerServiceInfo.import_from_compose_file("global-db", compose_file_manager, raise_exception=False)


# --- DatabaseServerServiceInfo.from_database_config ---


def test_from_database_config_marks_the_endpoint_external_and_uses_the_site_login_user():
    db_config = DatabaseConfig(host="db.example.com", port=3307, name="site_db", user="site_login")

    info = DatabaseServerServiceInfo.from_database_config(db_config, "site-password")

    assert info.external is True
    assert (info.host, info.port, info.name, info.user, info.password) == (
        "db.example.com",
        3307,
        "site_db",
        "site_login",
        "site-password",
    )


def test_from_database_config_falls_back_to_the_schema_name_when_no_login_user_is_declared():
    db_config = DatabaseConfig(host="db.example.com", name="site_db")

    info = DatabaseServerServiceInfo.from_database_config(db_config, "pw")

    assert info.user == "site_db"
    assert info.port == 3306


# --- DatabaseServerServiceInfo.import_from_bench ---


def _write_bench_configs(bench_path: Path, bench_name: str, site: dict | None, common: dict | None):
    sites = bench_path / "workspace" / "frappe-bench" / "sites"
    (sites / bench_name).mkdir(parents=True, exist_ok=True)
    if common is not None:
        (sites / "common_site_config.json").write_text(json.dumps(common))
    if site is not None:
        (sites / bench_name / "site_config.json").write_text(json.dumps(site))


def test_bench_import_lets_the_site_config_win_over_the_common_one(tmp_path):
    _write_bench_configs(
        tmp_path,
        "test.local",
        site={"db_host": "site-host", "db_port": 3399, "db_name": "sdb", "db_password": "sp"},
        common={"db_host": "common-host", "db_port": 3388},
    )

    info = DatabaseServerServiceInfo.import_from_bench("test.local", tmp_path)

    assert (info.host, info.port) == ("site-host", 3399)


def test_bench_import_falls_back_to_the_common_config_for_benches_created_before_the_cutover(tmp_path):
    _write_bench_configs(
        tmp_path,
        "test.local",
        site={"db_name": "sdb", "db_password": "sp"},
        common={"db_host": "common-host", "db_port": 3388},
    )

    info = DatabaseServerServiceInfo.import_from_bench("test.local", tmp_path)

    assert (info.host, info.port) == ("common-host", 3388)


def test_bench_import_uses_the_v16_db_user_key_when_present(tmp_path):
    _write_bench_configs(
        tmp_path,
        "test.local",
        site={"db_host": "h", "db_name": "sdb", "db_user": "distinct_user", "db_password": "sp"},
        common=None,
    )

    info = DatabaseServerServiceInfo.import_from_bench("test.local", tmp_path)

    assert info.user == "distinct_user"
    assert info.name == "sdb"


def test_bench_import_falls_back_to_the_schema_name_as_login_user_on_v15(tmp_path):
    _write_bench_configs(
        tmp_path,
        "test.local",
        site={"db_host": "h", "db_name": "sdb", "db_password": "sp"},
        common=None,
    )

    info = DatabaseServerServiceInfo.import_from_bench("test.local", tmp_path)

    assert info.user == "sdb"


def test_bench_import_defaults_the_port_to_the_engine_default(tmp_path):
    _write_bench_configs(
        tmp_path,
        "test.local",
        site={"db_host": "h", "db_name": "sdb", "db_password": "sp"},
        common=None,
    )

    assert DatabaseServerServiceInfo.import_from_bench("test.local", tmp_path).port == 3306


def test_bench_import_defaults_to_a_fm_owned_endpoint_but_honours_an_explicit_external_flag(tmp_path):
    _write_bench_configs(
        tmp_path,
        "test.local",
        site={"db_host": "h", "db_name": "sdb", "db_password": "sp"},
        common=None,
    )

    assert DatabaseServerServiceInfo.import_from_bench("test.local", tmp_path).external is False
    assert DatabaseServerServiceInfo.import_from_bench("test.local", tmp_path, external=True).external is True


def test_bench_import_raises_a_bench_error_when_asked_to_and_no_password_is_recorded(tmp_path):
    _write_bench_configs(tmp_path, "test.local", site=None, common={"db_host": "h"})

    with pytest.raises(BenchException):
        DatabaseServerServiceInfo.import_from_bench("test.local", tmp_path, raise_exception=True)


def test_bench_import_tolerates_an_empty_common_config_object(tmp_path):
    """A literally empty JSON object must not be read as `{"db_host": None}`."""
    _write_bench_configs(
        tmp_path,
        "test.local",
        site={"db_host": "site-host", "db_name": "sdb", "db_password": "sp"},
        common={},
    )

    info = DatabaseServerServiceInfo.import_from_bench("test.local", tmp_path)

    assert info.host == "site-host"


# --- MariaDBManager: client flags and env ---


def test_client_flags_carry_user_password_port_and_host_in_that_exact_order():
    manager = make_manager(make_info(host="global-db", user="root", port=3306, password="s3cr3t"))

    assert manager.client_flags == "-u'root' -p's3cr3t' -P3306 -h'global-db'"


def test_the_base_command_is_the_canonical_mariadb_client_not_a_legacy_mysql_symlink():
    """MariaDB 11.x images dropped the mysql/mysqladmin/mysqldump symlinks."""
    manager = make_manager()

    assert manager.base_command == f"/usr/bin/mariadb {manager.client_flags} "
    assert manager.base_query == "-e "


def test_no_option_file_env_is_emitted_for_the_global_database():
    """global-db carries no TLS, so nothing should make the client read a my.cnf."""
    assert make_manager()._env is None


def test_a_mysql_home_is_emitted_as_the_env_that_makes_the_client_read_its_option_file():
    manager = make_manager(mysql_home="/opt/tls")

    assert manager._env == ["MYSQL_HOME=/opt/tls"]


# --- MariaDBManager: exec vs run ---


def test_a_running_service_is_recognised_by_matching_its_compose_container_name():
    manager = make_manager(running=True)

    assert manager._is_service_running("global-db") is True


def test_a_stopped_service_is_reported_not_running():
    manager = make_manager(running=False)

    assert manager._is_service_running("global-db") is False


def test_a_service_with_no_container_at_all_is_reported_not_running():
    manager = make_manager(container_names={"global-db": "fm-global-db"})

    assert manager._is_service_running("redis-cache") is False


def test_a_running_service_gets_a_compose_exec_carrying_the_command_and_env():
    manager = make_manager(running=True, mysql_home="/opt/tls")

    manager._compose_exec_or_run("SOME COMMAND", stream=False, user="frappe", rm=True)

    manager.docker_client.compose.exec.assert_called_once_with(
        "global-db",
        command="SOME COMMAND",
        stream=False,
        env=["MYSQL_HOME=/opt/tls"],
    )
    manager.docker_client.compose.run.assert_not_called()


def test_a_stopped_service_gets_a_compose_run_that_smuggles_the_command_in_as_the_entrypoint():
    """The run fallback overrides the image entrypoint rather than passing a command."""
    manager = make_manager(running=False)

    manager._compose_exec_or_run("SOME COMMAND", stream=False, user="frappe", rm=True, entrypoint="ignored")

    manager.docker_client.compose.run.assert_called_once_with(
        "global-db",
        stream=False,
        user="frappe",
        rm=True,
        entrypoint="SOME COMMAND",
        env=None,
    )
    manager.docker_client.compose.exec.assert_not_called()


# --- MariaDBManager.db_run_query ---


def test_a_captured_query_asks_for_machine_readable_output_and_returns_it_verbatim():
    manager = make_manager(running=True)
    expected = make_output(["row1"])
    manager.docker_client.compose.exec.return_value = expected

    result = manager.db_run_query("'SELECT 1;'", capture_output=True)

    assert result is expected
    sent = manager.docker_client.compose.exec.call_args.kwargs["command"]
    assert sent == f"/usr/bin/mariadb {manager.client_flags} --batch --skip-column-names -e 'SELECT 1;'"
    assert manager.docker_client.compose.exec.call_args.kwargs["stream"] is False
    manager.output.live_lines.assert_not_called()


def test_an_uncaptured_query_streams_and_is_rendered_through_the_docker_noise_filter():
    manager = make_manager(running=True)
    streamed = object()
    manager.docker_client.compose.exec.return_value = streamed

    assert manager.db_run_query("'SELECT 1;'") is None

    sent = manager.docker_client.compose.exec.call_args.kwargs["command"]
    assert sent == f"/usr/bin/mariadb {manager.client_flags} -e 'SELECT 1;'"
    assert manager.docker_client.compose.exec.call_args.kwargs["stream"] is True
    manager.output.live_lines.assert_called_once_with(streamed, line_filters=DOCKER_LINE_NOISE)


def test_a_docker_failure_is_translated_into_the_domain_exception_the_caller_supplied():
    manager = make_manager(running=True)
    manager.docker_client.compose.exec.side_effect = docker_error(stderr=["boom"])
    domain = DatabaseServiceException("global-db", "translated")

    with pytest.raises(DatabaseServiceException) as excinfo:
        manager.db_run_query("'SELECT 1;'", raise_exception_obj=domain)

    assert excinfo.value is domain


def test_a_docker_failure_with_no_domain_exception_propagates_the_docker_error_unchanged():
    manager = make_manager(running=True)
    original = docker_error(stderr=["boom"])
    manager.docker_client.compose.exec.side_effect = original

    with pytest.raises(DockerException) as excinfo:
        manager.db_run_query("'SELECT 1;'")

    assert excinfo.value is original


# --- MariaDBManager: liveness ---


def test_liveness_is_decided_by_a_mariadb_admin_ping_reporting_mysqld_alive():
    manager = make_manager(running=True)
    manager.docker_client.compose.exec.return_value = make_output(["mysqld is alive"])

    assert manager.is_db_running() is True
    assert manager.docker_client.compose.exec.call_args.kwargs["command"] == (
        f"mariadb-admin {manager.client_flags} ping"
    )


def test_a_ping_that_does_not_say_alive_is_not_liveness():
    manager = make_manager(running=True)
    manager.docker_client.compose.exec.return_value = make_output(["connection refused"])

    assert manager.is_db_running() is False


def test_a_docker_failure_during_ping_is_swallowed_as_not_running():
    manager = make_manager(running=True)
    manager.docker_client.compose.exec.side_effect = docker_error(stderr=["no such container"])

    assert manager.is_db_running() is False


@pytest.mark.timeout(15)
def test_waiting_for_the_database_returns_as_soon_as_a_ping_succeeds():
    manager = make_manager()
    with (
        mock.patch.object(MariaDBManager, "is_db_running", side_effect=[False, False, True]) as ping,
        mock.patch("frappe_manager.services_manager.database_service_manager.time.sleep") as sleep,
    ):
        assert manager.wait_till_db_start(interval=5, timeout=30) is True

    assert ping.call_count == 3
    assert sleep.call_args_list == [mock.call(5), mock.call(5)]


@pytest.mark.timeout(15)
def test_waiting_gives_up_after_timeout_attempts_and_reports_interval_times_timeout_seconds():
    """`timeout` is an attempt count, not seconds; the reported budget is interval * timeout."""
    manager = make_manager()
    with (
        mock.patch.object(MariaDBManager, "is_db_running", return_value=False) as ping,
        mock.patch("frappe_manager.services_manager.database_service_manager.time.sleep") as sleep,
        pytest.raises(DatabaseServiceStartTimeout) as excinfo,
    ):
        manager.wait_till_db_start(interval=2, timeout=4)

    assert ping.call_count == 4
    assert sleep.call_count == 4
    assert "8" in str(excinfo.value)
    assert excinfo.value.service_name == "global-db"


# --- MariaDBManager: users ---


def test_the_user_list_is_parsed_from_tab_separated_batch_output():
    manager = make_manager(running=True)
    manager.docker_client.compose.exec.return_value = make_output(["root\tlocalhost", "admin\t%"])

    assert manager.get_db_users() == {"root": "localhost", "admin": "%"}
    assert "-e 'SELECT User, Host FROM mysql.user;'" in manager.docker_client.compose.exec.call_args.kwargs["command"]


def test_a_user_that_is_absent_from_the_user_list_does_not_exist():
    manager = make_manager(running=True)
    manager.docker_client.compose.exec.return_value = make_output(["root\tlocalhost"])

    assert manager.check_user_exists("admin") is False


def test_a_user_present_under_any_host_exists_when_no_host_is_demanded():
    manager = make_manager(running=True)
    manager.docker_client.compose.exec.return_value = make_output(["admin\tlocalhost"])

    assert manager.check_user_exists("admin") is True


def test_a_user_present_under_a_different_host_does_not_satisfy_a_host_specific_check():
    manager = make_manager(running=True)
    manager.docker_client.compose.exec.return_value = make_output(["admin\tlocalhost"])

    assert manager.check_user_exists("admin", "%") is False
    assert manager.check_user_exists("admin", "localhost") is True


def test_adding_a_user_issues_a_create_user_granting_access_from_any_host():
    manager = make_manager(running=True)
    with (
        mock.patch.object(MariaDBManager, "check_user_exists", return_value=False),
        mock.patch.object(MariaDBManager, "db_run_query") as query,
    ):
        manager.add_user("bench_user", "bench-pass")

    assert query.call_args.args[0] == "'CREATE USER `bench_user`@`%` IDENTIFIED BY \"bench-pass\";'"


def test_adding_a_user_that_already_exists_is_refused_and_issues_no_statement():
    manager = make_manager(running=True)
    with (
        mock.patch.object(MariaDBManager, "check_user_exists", return_value=True),
        mock.patch.object(MariaDBManager, "db_run_query") as query,
        pytest.raises(DatabaseServiceException) as excinfo,
    ):
        manager.add_user("bench_user", "bench-pass")

    query.assert_not_called()
    assert "already exists" in str(excinfo.value)


def test_forcing_an_existing_user_drops_it_first_and_then_recreates_it():
    manager = make_manager(running=True)
    with (
        mock.patch.object(MariaDBManager, "check_user_exists", return_value=True),
        mock.patch.object(MariaDBManager, "db_run_query") as query,
    ):
        manager.add_user("bench_user", "bench-pass", force=True)

    issued = [call.args[0] for call in query.call_args_list]
    assert issued == [
        "'DROP USER `bench_user`@`%`;'",
        "'CREATE USER `bench_user`@`%` IDENTIFIED BY \"bench-pass\";'",
    ]


def test_removing_a_user_drops_exactly_the_named_user_at_the_named_host():
    manager = make_manager(running=True)
    with mock.patch.object(MariaDBManager, "db_run_query") as query:
        manager.remove_user("bench_user", "localhost")

    assert query.call_args.args[0] == "'DROP USER `bench_user`@`localhost`;'"
    assert isinstance(query.call_args.args[1], DatabaseServiceUserRemoveFailError)


def test_removing_a_user_from_all_hosts_never_touches_a_differently_named_user():
    """`remove_all_host` enumerates the whole server, so the name filter is the only guard."""
    manager = make_manager(running=True)
    users = {"bench_user": "localhost", "root": "%", "other": "%"}
    with (
        mock.patch.object(MariaDBManager, "get_db_users", return_value=users),
        mock.patch.object(MariaDBManager, "db_run_query") as query,
    ):
        manager.remove_user("bench_user", remove_all_host=True)

    assert [call.args[0] for call in query.call_args_list] == ["'DROP USER `bench_user`@`localhost`;'"]


def test_granting_privileges_scopes_the_grant_to_one_schema_for_one_user():
    manager = make_manager(running=True)
    with mock.patch.object(MariaDBManager, "db_run_query") as query:
        manager.grant_user_privilages("bench_user", "bench_db")

    assert query.call_args.args[0] == "'GRANT ALL PRIVILEGES ON `bench_db`.* TO `bench_user`@`%`;'"


# --- MariaDBManager: databases ---


def test_listing_databases_returns_the_captured_rows():
    manager = make_manager(running=True)
    manager.docker_client.compose.exec.return_value = make_output(["mysql", "bench_db"])

    assert manager.get_all_databases() == ["mysql", "bench_db"]
    assert "-e 'SHOW DATABASES;'" in manager.docker_client.compose.exec.call_args.kwargs["command"]


def test_an_access_denied_listing_is_reported_as_an_access_problem_not_a_generic_failure():
    manager = make_manager(running=True)
    manager.docker_client.compose.exec.side_effect = docker_error(stderr=["ERROR 1045: Access denied for user"])

    with pytest.raises(DatabaseServiceQueryAccessDenied):
        manager.get_all_databases()


def test_any_other_listing_failure_is_reported_as_a_generic_database_service_error():
    manager = make_manager(running=True)
    manager.docker_client.compose.exec.side_effect = docker_error(stderr=["connection refused"])

    with pytest.raises(DatabaseServiceException) as excinfo:
        manager.get_all_databases()

    assert not isinstance(excinfo.value, DatabaseServiceQueryAccessDenied)
    assert "Failed to get list of all databases." in str(excinfo.value)


def test_database_existence_is_decided_by_membership_in_the_listing():
    manager = make_manager(running=True)
    with mock.patch.object(MariaDBManager, "get_all_databases", return_value=["mysql", "bench_db"]):
        assert manager.check_db_exists("bench_db") is True
        assert manager.check_db_exists("missing_db") is False


def test_creating_a_database_is_idempotent_by_statement():
    manager = make_manager(running=True)
    with mock.patch.object(MariaDBManager, "db_run_query") as query:
        manager.db_create("bench_db")

    # Pinned verbatim, including the trailing semicolon sitting OUTSIDE the closing quote.
    assert query.call_args.args[0] == "'CREATE DATABASE IF NOT EXISTS `bench_db`';"
    assert isinstance(query.call_args.args[1], DatabaseServiceDBCreateFailed)


def test_dropping_a_database_names_exactly_one_schema():
    manager = make_manager(running=True)
    with mock.patch.object(MariaDBManager, "db_run_query") as query:
        manager.remove_db("bench_db")

    assert query.call_args.args[0] == "'DROP DATABASE `bench_db`;'"
    assert isinstance(query.call_args.args[1], DatabaseServiceDBRemoveFailError)


# --- MariaDBManager: export and import ---


def test_exporting_a_missing_database_is_refused_before_any_docker_call(tmp_path):
    manager = make_manager(running=True)
    with (
        mock.patch.object(MariaDBManager, "check_db_exists", return_value=False),
        pytest.raises(DatabaseServiceDBNotFoundError),
    ):
        manager.db_export("bench_db", tmp_path / "dump.sql")

    manager.docker_client.compose.exec.assert_not_called()
    manager.docker_client.compose.run.assert_not_called()


def test_exporting_one_database_dumps_it_to_an_absolute_result_file(tmp_path):
    manager = make_manager(running=True)
    target = tmp_path / "dump.sql"
    with mock.patch.object(MariaDBManager, "check_db_exists", return_value=True):
        manager.db_export("bench_db", target)

    assert manager.docker_client.compose.exec.call_args.kwargs["command"] == (
        f"mariadb-dump {manager.client_flags} bench_db --result-file={target.absolute()}"
    )


def test_a_failed_export_is_reported_as_an_export_failure(tmp_path):
    manager = make_manager(running=True)
    manager.docker_client.compose.exec.side_effect = docker_error(stderr=["nope"])
    with (
        mock.patch.object(MariaDBManager, "check_db_exists", return_value=True),
        pytest.raises(DatabaseServiceDBExportFailed),
    ):
        manager.db_export("bench_db", tmp_path / "dump.sql")


def test_an_engine_wide_export_includes_the_grant_tables_and_a_consistent_snapshot(tmp_path):
    """Without --all-databases the mysql schema is missing and a restore comes back with no users."""
    manager = make_manager(running=True)
    target = tmp_path / "all.sql"

    manager.db_export_all(target)

    assert manager.docker_client.compose.exec.call_args.kwargs["command"] == (
        f"mariadb-dump {manager.client_flags} "
        "--all-databases --single-transaction --quick --routines --events --triggers "
        f"--result-file={target.absolute()}"
    )


def test_a_failed_engine_wide_export_names_all_databases_as_the_subject(tmp_path):
    manager = make_manager(running=True)
    manager.docker_client.compose.exec.side_effect = docker_error(stderr=["nope"])

    with pytest.raises(DatabaseServiceDBExportFailed) as excinfo:
        manager.db_export_all(tmp_path / "all.sql")

    assert "--all-databases" in str(excinfo.value)


def test_importing_into_a_missing_database_is_refused_before_the_file_is_copied(tmp_path):
    manager = make_manager(running=True)
    dump = tmp_path / "dump.sql"
    dump.write_text("-- sql")
    with (
        mock.patch.object(MariaDBManager, "check_db_exists", return_value=False),
        pytest.raises(DatabaseServiceDBNotFoundError),
    ):
        manager.db_import("bench_db", dump)

    manager.docker_client.compose.cp.assert_not_called()


def test_forcing_an_import_creates_the_missing_database_first(tmp_path):
    manager = make_manager(running=True)
    dump = tmp_path / "dump.sql"
    dump.write_text("-- sql")
    with (
        mock.patch.object(MariaDBManager, "check_db_exists", return_value=False),
        mock.patch.object(MariaDBManager, "db_create") as create,
    ):
        manager.db_import("bench_db", dump, force=True)

    create.assert_called_once_with("bench_db")


def test_an_import_copies_the_dump_into_the_container_tmp_then_sources_it(tmp_path):
    manager = make_manager(running=True)
    dump = tmp_path / "dump.sql"
    dump.write_text("-- sql")
    with mock.patch.object(MariaDBManager, "check_db_exists", return_value=True):
        manager.db_import("bench_db", dump)

    manager.docker_client.compose.cp.assert_called_once_with(
        str(dump.absolute()),
        "global-db:/tmp/dump.sql",
        stream=False,
    )
    assert manager.docker_client.compose.exec.call_args.kwargs["command"] == (
        f"{manager.base_command} bench_db -e 'source /tmp/dump.sql'"
    )


def test_a_failed_import_is_reported_as_an_import_failure(tmp_path):
    manager = make_manager(running=True)
    manager.docker_client.compose.cp.side_effect = docker_error(stderr=["nope"])
    dump = tmp_path / "dump.sql"
    dump.write_text("-- sql")
    with (
        mock.patch.object(MariaDBManager, "check_db_exists", return_value=True),
        pytest.raises(DatabaseServiceDBImportFailed),
    ):
        manager.db_import("bench_db", dump)
