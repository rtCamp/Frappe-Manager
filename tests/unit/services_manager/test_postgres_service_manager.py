"""`PostgresManager`: the same Protocol as `MariaDBManager`, a different engine underneath.

These pin the places where the two genuinely diverge, because those are the places a reader
reasoning from the MariaDB side will get wrong:

- connecting needs a database, so server-level questions are asked of `postgres`;
- `DROP ROLE` fails while the role owns anything, so `DROP OWNED BY` has to come first;
- `CREATE DATABASE` has no `IF NOT EXISTS` and cannot run in a transaction;
- `pg_dump` emits no roles, so an engine-wide dump has to be `pg_dumpall`.

Every command is asserted at the docker seam: what reaches the container, never a mocked
answer echoed back.
"""

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from frappe_manager.docker.docker_exceptions import DockerException
from frappe_manager.docker.subprocess_output import SubprocessOutput
from frappe_manager.services_manager.database_service_manager import DatabaseServerServiceInfo
from frappe_manager.services_manager.postgres_service_manager import PostgresManager
from frappe_manager.services_manager.services_exceptions import (
    DatabaseServiceDBExportFailed,
    DatabaseServiceDBNotFoundError,
    DatabaseServiceStartTimeout,
    DatabaseServiceUserRemoveFailError,
)

SERVICE = "frappe"


def _output(*lines: str) -> SubprocessOutput:
    return SubprocessOutput(stdout=list(lines), stderr=[], combined=list(lines), exit_code=0)


@pytest.fixture
def manager():
    """A manager whose docker seam records commands and replays canned answers."""
    docker = MagicMock()
    docker.compose.get_all_services_status.return_value = [{"Name": SERVICE, "State": "running"}]
    docker.compose.exec.return_value = _output()
    compose = MagicMock()
    compose.get_container_names.return_value = {SERVICE: SERVICE}
    return PostgresManager(
        DatabaseServerServiceInfo(
            host="pg.example", port=5432, user="postgres", password="s3cret", external=True
        ),
        compose,
        docker,
        run_on_compose_service=SERVICE,
        output_handler=MagicMock(),
    )


def commands(manager) -> list[str]:
    return [call.kwargs.get("command", "") for call in manager.docker_client.compose.exec.call_args_list]


class TestHowItConnects:
    def test_the_password_travels_in_the_environment(self):
        """Never in a connection URI: that would put it in argv, where the container's own
        process listing shows it to anything that can run `ps`."""
        docker = MagicMock()
        docker.compose.get_all_services_status.return_value = [{"Name": SERVICE, "State": "running"}]
        docker.compose.exec.return_value = _output()
        compose = MagicMock()
        compose.get_container_names.return_value = {SERVICE: SERVICE}
        m = PostgresManager(
            DatabaseServerServiceInfo(host="h", port=5432, user="u", password="s3cret", external=True),
            compose,
            docker,
            run_on_compose_service=SERVICE,
            output_handler=MagicMock(),
        )

        m.get_all_databases()

        env = docker.compose.exec.call_args.kwargs["env"]
        assert "PGPASSWORD=s3cret" in env
        assert "s3cret" not in docker.compose.exec.call_args.kwargs["command"]

    def test_server_level_questions_are_asked_of_the_maintenance_database(self, manager):
        """Postgres has no "connect without selecting a database", so asking whether the site's
        database exists cannot be asked while connected to it."""
        manager.get_all_databases()

        assert "-d postgres" in commands(manager)[0]

    def test_a_refused_statement_is_a_failure(self, manager):
        """Without ON_ERROR_STOP psql prints the error and exits 0, so every caller would read a
        refusal as success."""
        manager.get_all_databases()

        assert "ON_ERROR_STOP=1" in commands(manager)[0]

    def test_an_external_endpoint_runs_the_client_in_the_bench(self):
        """There is no container to exec into for a server fm does not run, so the client runs in
        the bench's frappe service and dials it over the network."""
        m = PostgresManager(
            DatabaseServerServiceInfo(host="pg.example", port=5432, user="u", password="p", external=True),
            MagicMock(),
            MagicMock(),
            output_handler=MagicMock(),
        )

        assert m.run_on_compose_service == "frappe"


class TestReadiness:
    def test_readiness_asks_the_server_rather_than_running_a_query(self, manager):
        """`pg_isready` reports "accepting connections" distinctly from "starting up"; a server
        replaying WAL answers the second, and a query that happens to succeed cannot tell them
        apart."""
        manager.docker_client.compose.exec.return_value = _output("pg.example:5432 - accepting connections")

        assert manager.is_db_running() is True
        assert "pg_isready" in commands(manager)[0]

    def test_a_server_that_never_answers_raises_rather_than_looping_forever(self, manager):
        manager.docker_client.compose.exec.side_effect = DockerException(["psql"], _output("down"))

        with pytest.raises(DatabaseServiceStartTimeout):
            manager.wait_till_db_start(interval=0, timeout=2)


class TestDatabases:
    def test_creating_an_existing_database_is_a_no_op(self, manager):
        """`CREATE DATABASE` has no `IF NOT EXISTS` and cannot run inside a transaction, so
        existence is a separate question rather than part of the statement."""
        manager.docker_client.compose.exec.return_value = _output("shop_app")

        manager.db_create("shop_app")

        assert not any("CREATE DATABASE" in command for command in commands(manager))

    def test_creating_a_new_database_issues_the_statement(self, manager):
        manager.docker_client.compose.exec.return_value = _output("postgres")

        manager.db_create("shop_app")

        assert any("CREATE DATABASE" in command for command in commands(manager))

    def test_dropping_a_database_terminates_its_sessions(self, manager):
        """Without `WITH (FORCE)` the drop fails whenever anything still holds a connection,
        which on a bench being deleted is the normal case rather than the exception."""
        manager.remove_db("shop_app")

        assert "WITH (FORCE)" in commands(manager)[0]

    def test_a_template_database_is_never_listed(self, manager):
        """`template0` and `template1` exist on every server and belong to nobody; listing them
        would offer fm's own callers a database they must never touch."""
        manager.get_all_databases()

        assert "not datistemplate" in commands(manager)[0]


class TestRoles:
    def test_a_role_is_stripped_of_what_it_owns_before_it_is_dropped(self, manager):
        """Postgres refuses to drop a role that owns an object or holds a grant ANYWHERE, and
        reports it as a dependency error naming databases the caller may not be connected to.
        `DROP OWNED BY` per database is the documented way to make the role droppable."""
        manager.docker_client.compose.exec.return_value = _output("shop_role")

        manager.remove_user("shop_role")

        issued = commands(manager)
        owned = next(i for i, c in enumerate(issued) if "DROP OWNED BY" in c)
        dropped = next(i for i, c in enumerate(issued) if "DROP ROLE" in c)
        assert owned < dropped

    def test_a_database_the_login_cannot_enter_does_not_block_the_drop(self, manager):
        """A database this login cannot connect to cannot hold objects it owns either, so
        refusing there would block a drop that is about to succeed."""
        replies = [_output("shop_role"), _output("shop_role", "other_db")]

        def answer(*_args, **kwargs):
            command = kwargs.get("command", "")
            if "DROP OWNED BY" in command:
                raise DockerException(["psql"], _output("permission denied"))
            return replies.pop(0) if replies else _output()

        manager.docker_client.compose.exec.side_effect = answer

        manager.remove_user("shop_role")

        assert any("DROP ROLE" in command for command in commands(manager))

    def test_removing_a_role_that_does_not_exist_does_nothing(self, manager):
        manager.docker_client.compose.exec.return_value = _output("someone_else")

        manager.remove_user("shop_role")

        assert not any("DROP" in command for command in commands(manager))

    def test_a_failed_drop_is_reported_as_one(self, manager):
        def answer(*_args, **kwargs):
            if "DROP ROLE" in kwargs.get("command", ""):
                raise DockerException(["psql"], _output("still owns"))
            return _output("shop_role")

        manager.docker_client.compose.exec.side_effect = answer

        with pytest.raises(DatabaseServiceUserRemoveFailError):
            manager.remove_user("shop_role")

    def test_only_roles_that_can_log_in_are_listed(self, manager):
        """Postgres groups are roles too. Listing them would report a group as a site login."""
        manager.get_db_users()

        assert "rolcanlogin" in commands(manager)[0]


class TestDumps:
    def test_a_dump_can_be_restored_over_an_existing_database(self, manager):
        """That is what a rollback does, and a plain dump without `--clean` fails on every object
        that already exists."""
        manager.docker_client.compose.exec.return_value = _output("shop_app")

        manager.db_export("shop_app", Path("/tmp/shop.sql"))

        dump = next(c for c in commands(manager) if "pg_dump" in c)
        assert "--clean" in dump
        assert "--if-exists" in dump

    def test_the_dump_is_plain_sql(self, manager):
        """`-Fc` is smaller and restores in parallel, but only `pg_restore` reads it. Plain SQL is
        what psql restores, what Frappe's own backups produce, and what an operator can open."""
        manager.docker_client.compose.exec.return_value = _output("shop_app")

        manager.db_export("shop_app", Path("/tmp/shop.sql"))

        assert "-Fc" not in next(c for c in commands(manager) if "pg_dump" in c)

    def test_dumping_a_database_that_is_not_there_is_refused(self, manager):
        manager.docker_client.compose.exec.return_value = _output("postgres")

        with pytest.raises(DatabaseServiceDBNotFoundError):
            manager.db_export("shop_app", Path("/tmp/shop.sql"))

    def test_a_failed_dump_is_reported_as_one(self, manager):
        def answer(*_args, **kwargs):
            if "pg_dump" in kwargs.get("command", ""):
                raise DockerException(["pg_dump"], _output("no space left on device"))
            return _output("shop_app")

        manager.docker_client.compose.exec.side_effect = answer

        with pytest.raises(DatabaseServiceDBExportFailed):
            manager.db_export("shop_app", Path("/tmp/shop.sql"))

    def test_an_engine_wide_dump_carries_the_roles(self, manager):
        """`pg_dump` emits no `CREATE ROLE` at all, so a restore from it comes back with databases
        nobody can log into. Same reason the MariaDB side dumps the `mysql` schema."""
        manager.db_export_all(Path("/tmp/all.sql"))

        assert any("pg_dumpall" in command for command in commands(manager))

    def test_importing_into_a_missing_database_is_refused_unless_forced(self, manager):
        manager.docker_client.compose.exec.return_value = _output("postgres")

        with pytest.raises(DatabaseServiceDBNotFoundError):
            manager.db_import("shop_app", Path("/tmp/shop.sql"))

    def test_a_forced_import_creates_the_database_first(self, manager):
        manager.docker_client.compose.exec.return_value = _output("postgres")

        manager.db_import("shop_app", Path("/tmp/shop.sql"), force=True)

        issued = commands(manager)
        created = next(i for i, c in enumerate(issued) if "CREATE DATABASE" in c)
        imported = next(i for i, c in enumerate(issued) if "--file=" in c and "psql" in c)
        assert created < imported
