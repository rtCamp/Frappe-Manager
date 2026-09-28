"""`PostgresManager`: the Postgres half of :class:`DatabaseServiceManager`.

Same Protocol, same call sites, different engine. What is NOT shared with `MariaDBManager` is
almost everything below the surface, and the differences are the reason this is a class of its
own rather than a branch:

- **Connecting needs a database.** MariaDB will take a connection with no schema selected;
  Postgres will not. So every server-level query (does this database exist, does this role
  exist) is issued against the server's own `postgres` database, and only schema-level work
  connects to the site's.
- **`DROP ROLE` is not `DROP USER`.** Postgres refuses while the role still owns objects or
  holds grants, so the drop is preceded by `DROP OWNED BY`, which is the documented way to make
  a role droppable and the reason this cannot be one shared statement.
- **`CREATE DATABASE` cannot run inside a transaction**, which is why `psql -c` is used per
  statement rather than a single batched script.
- **The dump format is plain SQL on purpose.** `pg_dump -Fc` is smaller and restores in
  parallel, but only `pg_restore` can read it; plain SQL is what `psql` restores, what Frappe's
  own `get_command` produces, and what an operator can open. A deploy snapshot that only fm can
  read is worth less than one anybody can.

Secrets travel through `PGPASSWORD` in the environment, never in a connection URI, so they do
not appear in the container's process listing.
"""

import time
from pathlib import Path

from frappe_manager.docker import ComposeFile, DockerClient, DockerException
from frappe_manager.output_manager import OutputHandler
from frappe_manager.output_manager.rich_output import RichOutputHandler
from frappe_manager.services_manager.database_service_manager import (
    DatabaseServerServiceInfo,
    DatabaseServiceManager,
)
from frappe_manager.services_manager.services_exceptions import (
    DatabaseServiceDBCreateFailed,
    DatabaseServiceDBExportFailed,
    DatabaseServiceDBImportFailed,
    DatabaseServiceDBNotFoundError,
    DatabaseServiceDBRemoveFailError,
    DatabaseServiceException,
    DatabaseServiceStartTimeout,
    DatabaseServiceUserRemoveFailError,
)

# The database every server has and that nothing owns, used purely as a place to stand while
# asking about other databases. Postgres has no "connect to the server itself".
MAINTENANCE_DATABASE = "postgres"


class PostgresManager(DatabaseServiceManager):
    def __init__(
        self,
        database_server_info: DatabaseServerServiceInfo,
        compose_file_manager: ComposeFile,
        docker_client: DockerClient,
        run_on_compose_service: str | None = None,
        output_handler: OutputHandler | None = None,
        sslrootcert: str | None = None,
    ) -> None:
        self.database_server_info: DatabaseServerServiceInfo = database_server_info
        self.compose_file_manager: ComposeFile = compose_file_manager
        self.docker_client: DockerClient = docker_client
        self.output = output_handler or RichOutputHandler()

        self.run_on_compose_service: str
        if run_on_compose_service:
            self.run_on_compose_service = run_on_compose_service
        elif self.database_server_info.external:
            # An external endpoint has no container to exec into: `host` is a DNS name, not a
            # compose service. The bench's frappe service carries the psql client and dials it.
            self.run_on_compose_service = "frappe"
        else:
            self.run_on_compose_service = self.database_server_info.host

        # Credentials and endpoint are emitted together, from one object, so a password can only
        # ever travel to the host it was minted for -- the same rule MariaDBManager follows.
        self.client_flags = (
            f"-h '{self.database_server_info.host}' -p {self.database_server_info.port} "
            f"-U '{self.database_server_info.user}'"
        )
        self._env: list[str] = [f"PGPASSWORD={self.database_server_info.password}"]
        if sslrootcert:
            self._env.append(f"PGSSLROOTCERT={sslrootcert}")
            self._env.append("PGSSLMODE=verify-full")

        # `compose run` needs a user that exists in the TARGET image; the bench image has frappe
        # and the engine image does not. Same trap MariaDBManager documents.
        self._run_user: str | None = "frappe" if self.run_on_compose_service == "frappe" else None

    def _is_service_running(self, service: str) -> bool:
        all_statuses = self.docker_client.compose.get_all_services_status()
        service_container = self.compose_file_manager.get_container_names().get(service)
        return any(
            status.get("Name") == service_container and status.get("State") == "running"
            for status in all_statuses
        )

    def _compose_exec_or_run(self, command: str, stream: bool = False, entrypoint: str | None = None):
        if self._is_service_running(self.run_on_compose_service):
            return self.docker_client.compose.exec(
                self.run_on_compose_service, command=command, stream=stream, env=self._env
            )
        return self.docker_client.compose.run(
            self.run_on_compose_service,
            stream=stream,
            user=self._run_user,
            rm=True,
            entrypoint=entrypoint or command,
            env=self._env,
        )

    def _psql(self, sql: str, *, database: str = MAINTENANCE_DATABASE, capture_output: bool = False):
        """One statement, unaligned and tuple-only so the answer needs no parsing back out.

        `ON_ERROR_STOP=1` is what turns a refused statement into a non-zero exit: without it psql
        prints the error and exits 0, so every caller would read a failure as success.
        """
        flags = "-tA -v ON_ERROR_STOP=1" if capture_output else "-v ON_ERROR_STOP=1"
        return self._compose_exec_or_run(
            f'/usr/bin/psql {self.client_flags} -d {database} {flags} -c "{sql}"'
        )

    def db_run_query(self, query: str, on_failure=None, capture_output: bool = False):
        try:
            return self._psql(query, capture_output=capture_output)
        except DockerException as e:
            if on_failure:
                raise on_failure() from e
            raise

    def is_db_running(self) -> bool:
        """`pg_isready`, which is the server's own readiness answer rather than a query that
        happens to succeed: it reports "accepting connections" distinctly from "starting up",
        and a server replaying WAL answers the second."""
        try:
            output = self._compose_exec_or_run(f"/usr/bin/pg_isready {self.client_flags}")
            return "accepting connections" in " ".join(output.stdout)
        except DockerException:
            return False

    def wait_till_db_start(self, interval: int = 5, timeout: int = 30) -> bool:
        for _ in range(timeout):
            if self.is_db_running():
                return True
            time.sleep(interval)
        raise DatabaseServiceStartTimeout(interval * timeout, self.run_on_compose_service)

    def get_db_users(self) -> dict[str, str]:
        """Roles that can log in. The `%` is a MariaDB concept with no Postgres equivalent -- a
        role is not scoped to a client host there, pg_hba.conf is -- so every role reports the
        same placeholder rather than inventing a per-host answer."""
        output = self.db_run_query(
            "select rolname from pg_roles where rolcanlogin",
            on_failure=lambda: DatabaseServiceException(
                self.database_server_info.host, "Failed to list postgres roles."
            ),
            capture_output=True,
        )
        return {line.strip(): "%" for line in output.stdout if line.strip()}

    def check_user_exists(self, username: str, host: str | None = None) -> bool:
        return username in self.get_db_users()

    def get_all_databases(self) -> list[str]:
        output = self.db_run_query(
            "select datname from pg_database where not datistemplate",
            on_failure=lambda: DatabaseServiceException(
                self.database_server_info.host, "Failed to get list of all databases."
            ),
            capture_output=True,
        )
        return [line.strip() for line in output.stdout if line.strip()]

    def check_db_exists(self, db_name: str) -> bool:
        return db_name in self.get_all_databases()

    def db_create(self, db_name):
        """`CREATE DATABASE` cannot run inside a transaction and has no `IF NOT EXISTS`, so
        existence is asked first rather than folded into the statement."""
        if self.check_db_exists(db_name):
            return
        self.db_run_query(
            f'CREATE DATABASE \\"{db_name}\\"',
            on_failure=lambda: DatabaseServiceDBCreateFailed(self.run_on_compose_service, db_name),
        )

    def remove_db(self, db_name: str):
        """`WITH (FORCE)` terminates existing sessions first. Without it the drop fails whenever
        anything still holds a connection, which on a bench being deleted is the normal case, not
        the exception."""
        self.db_run_query(
            f'DROP DATABASE IF EXISTS \\"{db_name}\\" WITH (FORCE)',
            on_failure=lambda: DatabaseServiceDBRemoveFailError(db_name, self.database_server_info.host),
        )

    def remove_user(self, db_user: str, db_user_host: str = "%", remove_all_host: bool = False):
        """`DROP OWNED BY` first, and that is not optional.

        Postgres refuses to drop a role that still owns an object or holds a grant anywhere, and
        reports it as a dependency error listing objects in databases the caller may not even be
        connected to. `DROP OWNED BY` is the documented way to make a role droppable, and it is
        run per database because it only ever affects the one it runs in.
        """
        if not self.check_user_exists(db_user):
            return

        for database in self.get_all_databases():
            try:
                self._psql(f'DROP OWNED BY \\"{db_user}\\" CASCADE', database=database)
            except DockerException:
                # A database this login cannot enter cannot be holding objects it owns either,
                # and refusing here would block a drop that is about to succeed.
                continue

        self.db_run_query(
            f'DROP ROLE IF EXISTS \\"{db_user}\\"',
            on_failure=lambda: DatabaseServiceUserRemoveFailError(db_user, db_user_host),
        )

    def db_export(self, db_name: str, export_file_path: str | Path):
        if not self.check_db_exists(db_name):
            raise DatabaseServiceDBNotFoundError(db_name, self.run_on_compose_service)

        destination = str(Path(export_file_path).absolute())
        # `--clean --if-exists` so the dump can be restored OVER an existing database, which is
        # what a rollback does. Plain SQL rather than -Fc: psql restores it, Frappe's own backups
        # produce it, and an operator can read it.
        command = (
            f"/usr/bin/pg_dump {self.client_flags} -d {db_name} "
            f"--clean --if-exists --no-owner --no-privileges --file={destination}"
        )
        try:
            self._compose_exec_or_run(command)
        except DockerException as e:
            raise DatabaseServiceDBExportFailed(self.run_on_compose_service, db_name) from e

    def db_export_all(self, export_file_path: str | Path):
        """`pg_dumpall`, because an engine-level dump has to carry the ROLES.

        `pg_dump` covers one database and emits no `CREATE ROLE` at all, so a restore from it
        comes back with databases nobody can log into. That is the same reason the MariaDB side
        dumps the `mysql` schema rather than just the site schemas.
        """
        destination = str(Path(export_file_path).absolute())
        command = f"/usr/bin/pg_dumpall {self.client_flags} --clean --file={destination}"
        try:
            self._compose_exec_or_run(command)
        except DockerException as e:
            raise DatabaseServiceDBExportFailed(self.run_on_compose_service, "--all-databases") from e

    def db_import(self, db_name: str, host_db_file_path: Path, force: bool = False):
        if not self.check_db_exists(db_name):
            if not force:
                raise DatabaseServiceDBNotFoundError(db_name, self.run_on_compose_service)
            self.db_create(db_name)

        source = str(host_db_file_path.absolute())
        # Inside the container, alongside the mariadb path that does the same (database_service_manager.py:437).
        container_path = f"/tmp/{host_db_file_path.name}"  # noqa: S108
        command = (
            f"/usr/bin/psql {self.client_flags} -d {db_name} -v ON_ERROR_STOP=1 --file={container_path}"
        )
        try:
            self.docker_client.compose.cp(source, f"{self.run_on_compose_service}:{container_path}", stream=False)
            self._compose_exec_or_run(command)
        except DockerException as e:
            raise DatabaseServiceDBImportFailed(self.run_on_compose_service, source) from e
