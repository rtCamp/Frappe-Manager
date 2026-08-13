import json
import time
from pathlib import Path
from typing import TYPE_CHECKING, Any, Protocol

from pydantic import BaseModel

from frappe_manager.docker import DOCKER_LINE_NOISE, ComposeFile, DockerClient, DockerException
from frappe_manager.docker.subprocess_output import SubprocessOutput
from frappe_manager.output_manager import OutputHandler
from frappe_manager.output_manager.rich_output import RichOutputHandler
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
from frappe_manager.site_manager.exceptions import BenchException

if TYPE_CHECKING:
    # bench_config imports this module at module scope, so this one stays type-only.
    from frappe_manager.site_manager.bench_config import DatabaseConfig


# TODO this class will be used for validation for main config
class DatabaseServerServiceInfo(BaseModel):
    host: str
    user: str
    port: int
    password: str
    name: str | None = None
    # True when the endpoint is a database fm does not own. `host` is then a DNS name rather
    # than a compose service, so there is no container to exec the client into.
    external: bool = False

    @classmethod
    def import_from_compose_file(
        cls,
        compose_service_name: str,
        compose_file_manager: ComposeFile,
        raise_exception: bool = True,
    ):
        """
        Provides info about a database server
        """
        compose_service_envs = compose_file_manager.get_envs(container=compose_service_name)

        info: dict[str, Any] = {}
        info["user"] = "root"
        # this also being considered as servicename
        info["host"] = compose_service_name
        info["port"] = 3306

        # TODO use fm main config here
        # secrets or password ?
        if "MYSQL_ROOT_PASSWORD_FILE" in compose_service_envs:
            password_path: Path = compose_file_manager.get_secret_file_path("db_root_password")
            info["password"] = password_path.read_text()
        elif "MYSQL_ROOT_PASSWORD" in compose_service_envs:
            info["password"] = compose_service_envs["MYSQL_ROOT_PASSWORD"]
        elif raise_exception:
            raise DatabaseServicePasswordNotFound(compose_service_name)

        return cls(**info)

    @classmethod
    def from_database_config(cls, db_config: "DatabaseConfig", password: str) -> "DatabaseServerServiceInfo":
        """
        Provides info about an external database server, from `[database."<site>"]`.

        `password` is the SITE's own password, never a root or admin one. fm holds admin
        credentials only for the duration of a create, and routes them into Frappe's own
        provisioning call over stdin rather than through this object.
        """
        return cls(
            host=db_config.host,
            port=db_config.port,
            name=db_config.name,
            user=db_config.login_user,
            password=password,
            external=True,
        )

    @classmethod
    def import_from_bench(cls, bench_name: str, bench_path: Path, raise_exception=False, external: bool = False):
        """
        Provides info about a database server

        The site file wins over the common one. fm no longer writes `db_host` and `db_port` into
        `common_site_config.json` because the endpoint belongs to a site, so common is read only
        as a fallback for benches created before that cutover.
        """

        site_config_file: Path = bench_path / "workspace" / "frappe-bench" / "sites" / bench_name / "site_config.json"
        common_site_config_file: Path = bench_path / "workspace" / "frappe-bench" / "sites" / "common_site_config.json"

        info: dict[str, Any] = {"external": external}

        info["password"] = None

        if common_site_config_file.exists():
            with open(common_site_config_file) as f:
                common_site_config = json.load(f)
                if common_site_config:
                    info["host"] = common_site_config.get("db_host")
                    info["port"] = common_site_config.get("db_port")

        if site_config_file.exists():
            with open(site_config_file) as f:
                site_config = json.load(f)
                if site_config:
                    info["host"] = site_config.get("db_host") or info.get("host")
                    info["port"] = site_config.get("db_port") or info.get("port")
                    info["name"] = site_config["db_name"]
                    # v16 has a real `db_user` key, so the login user no longer has to equal the
                    # schema name. v15 has no such key and falls back to the name, as before.
                    info["user"] = site_config.get("db_user") or site_config["db_name"]
                    info["password"] = site_config["db_password"]

        if not info.get("port"):
            info["port"] = 3306

        if raise_exception and not info["password"]:
            raise BenchException(
                bench_name,
                f"Password for the db user doesn't exits in either {common_site_config_file.name},{site_config_file.name}",
            )

        return cls(**info)


class DatabaseServiceManager(Protocol):
    database_server_info: DatabaseServerServiceInfo
    compose_file_manager: ComposeFile
    docker_client: DockerClient

    def __init__(
        self,
        database_server_info: DatabaseServerServiceInfo,
        compose_file_manager: ComposeFile,
        docker_client: DockerClient,
    ) -> None: ...

    def remove_user(self, db_user: str, db_user_host: str = "%", remove_all_host: bool = False): ...

    def add_user(self, db_user: str, db_pass: str, db_user_host: str = "%", force: bool = False, timeout=25): ...

    def grant_user_privilages(self, db_user: str, db_name: str): ...

    def check_user_exists(self, db_user: str): ...

    def check_db_exists(self, db_name: str): ...

    def remove_db(self, db_name: str): ...

    def wait_till_db_start(self, interval: int = 5, timeout: int = 30) -> bool: ...

    def db_import(self, db_name: str, host_db_file_path: Path, force: bool = False): ...


class MariaDBManager(DatabaseServiceManager):
    def __init__(
        self,
        database_server_info: DatabaseServerServiceInfo,
        compose_file_manager: ComposeFile,
        docker_client: DockerClient,
        run_on_compose_service: str | None = None,
        output_handler: OutputHandler | None = None,
        mysql_home: str | None = None,
    ) -> None:
        """
        Database manager
        """
        self.database_server_info: DatabaseServerServiceInfo = database_server_info
        self.compose_file_manager: ComposeFile = compose_file_manager
        self.docker_client: DockerClient = docker_client
        self.output = output_handler or RichOutputHandler()

        self.run_on_compose_service: str

        if run_on_compose_service:
            self.run_on_compose_service = run_on_compose_service
        elif self.database_server_info.external:
            # An external endpoint has no container to exec into: `host` is a DNS name, not a
            # compose service. The bench's frappe service ships the mariadb client, so the
            # client runs there and dials the endpoint over the network.
            self.run_on_compose_service = "frappe"
        else:
            self.run_on_compose_service = self.database_server_info.host

        # Credentials and endpoint are emitted together, from one object, so a password can
        # only ever travel to the host it was minted for. `import_from_compose_file` is the
        # only source of the global-db root password and it hardcodes `host` to the compose
        # service name, which is why that password cannot reach an external server.
        self.client_flags = (
            f"-u'{self.database_server_info.user}' -p'{self.database_server_info.password}' "
            f"-P{self.database_server_info.port} -h'{self.database_server_info.host}'"
        )

        # Canonical client names only. MariaDB 11.x images no longer ship the legacy
        # mysql/mysqladmin/mysqldump symlinks (verified absent in mariadb:11.8), while
        # mariadb, mariadb-admin and mariadb-dump exist in both the engine image and
        # the bench image.
        self.base_command = f"/usr/bin/mariadb {self.client_flags} "
        self.base_query = "-e "

        # Every CLI shell-out is plaintext unless the client reads an option file, and
        # MYSQL_HOME=<dir> is what makes it read <dir>/my.cnf. None means global-db, where
        # there is no TLS to carry, so no env is emitted at all.
        self._env: list[str] | None = [f"MYSQL_HOME={mysql_home}"] if mysql_home else None

        # `compose run` needs a user that exists in the TARGET image. The bench image
        # has frappe; the engine image does not (`unable to find user frappe`), which
        # broke every fallback call against a stopped global-db. `compose exec` ignores
        # this, so only the run path was affected.
        self._run_user: str | None = "frappe" if self.run_on_compose_service == "frappe" else None

    def _is_service_running(self, service: str) -> bool:
        """Check if a service is running."""
        all_statuses = self.docker_client.compose.get_all_services_status()
        containers = self.compose_file_manager.get_container_names()
        service_container = containers.get(service)

        for status in all_statuses:
            if status.get("Name") == service_container:
                return status.get("State") == "running"
        return False

    def _compose_exec_or_run(
        self,
        command: str,
        stream: bool = False,
        user: str | None = None,
        rm: bool = False,
        entrypoint: str | None = None,
    ):
        """
        Executes a command using compose.exec if the service is running,
        otherwise uses compose.run.
        """
        if self._is_service_running(self.run_on_compose_service):
            return self.docker_client.compose.exec(
                self.run_on_compose_service,
                command=command,
                stream=stream,
                env=self._env,
            )
        return self.docker_client.compose.run(
            self.run_on_compose_service,
            # command=command,
            stream=stream,
            user=user,
            rm=rm,
            entrypoint=command,
            env=self._env,
        )

    def db_run_query(
        self,
        query: str,
        raise_exception_obj: DatabaseServiceException | None = None,
        capture_output: bool = False,
    ):
        base_command = self.base_command

        if capture_output:
            base_command += "--batch --skip-column-names "

        db_query = base_command + self.base_query + query

        try:
            output = self._compose_exec_or_run(
                db_query,
                stream=not capture_output,
                user=self._run_user,
                rm=True,
            )
            if capture_output:
                return output
            self.output.live_lines(output, line_filters=DOCKER_LINE_NOISE)
        except DockerException as e:
            if raise_exception_obj:
                raise raise_exception_obj
            raise e

    def wait_till_db_start(self, interval: int = 5, timeout: int = 30) -> bool:
        for i in range(timeout):
            if not self.is_db_running():
                time.sleep(interval)
            else:
                return True
        total_timeout = interval * timeout
        raise DatabaseServiceStartTimeout(total_timeout, self.run_on_compose_service)

    def is_db_running(self) -> bool:
        db_started_command = f"mariadb-admin {self.client_flags} ping"
        try:
            output = self._compose_exec_or_run(
                db_started_command,
                stream=False,
                user=self._run_user,
                rm=True,
                entrypoint=None,
            )
            return "mysqld is alive" in " ".join(output.stdout)
        except DockerException as e:
            return False

    def get_db_users(self) -> dict[str, str]:
        show_db_user_command = "'SELECT User, Host FROM mysql.user;'"
        exception = DatabaseServiceException(self.database_server_info.host, "Failed to determine mysql users.")
        output: SubprocessOutput = self.db_run_query(
            show_db_user_command,
            raise_exception_obj=exception,
            capture_output=True,
        )
        user_list: dict[str, str] = {}
        for line in output.stdout:
            username, host = line.split("\t")
            user_list[username] = host
        return user_list

    def check_user_exists(self, username: str, host: str | None = None) -> bool:
        user_list = self.get_db_users()
        if username not in user_list:
            return False
        if not host:
            return True
        if not user_list[username] == host:
            return False
        return True

    def get_all_databases(self) -> list[str]:
        db_exits_commmand = "'SHOW DATABASES;'"
        db_exits_exception = DatabaseServiceException(
            self.database_server_info.host,
            "Failed to get list of all databases.",
        )
        try:
            output: SubprocessOutput = self.db_run_query(db_exits_commmand, capture_output=True)
            return output.stdout
        except DockerException as e:
            if "access denied" in " ".join(e.output.combined).lower():
                raise DatabaseServiceQueryAccessDenied(db_exits_commmand)
        raise db_exits_exception

    def check_db_exists(self, db_name: str):
        databases = self.get_all_databases()
        return db_name in databases

    def remove_user(self, db_user: str, db_user_host: str = "%", remove_all_host: bool = False):
        users = {db_user: db_user_host}

        if remove_all_host:
            users = self.get_db_users()

        for user, host in users.items():
            if db_user == user:
                remove_db_user_command = f"'DROP USER `{user}`@`{host}`;'"
                remove_db_user_exception = DatabaseServiceUserRemoveFailError(user, host)
                self.db_run_query(remove_db_user_command, remove_db_user_exception)

    def remove_db(self, db_name: str):
        remove_db_command = f"'DROP DATABASE `{db_name}`;'"
        remove_db_exception = DatabaseServiceDBRemoveFailError(db_name, self.database_server_info.host)
        self.db_run_query(remove_db_command, remove_db_exception)

    def grant_user_privilages(self, db_user: str, db_name: str):
        grant_user_command = f"'GRANT ALL PRIVILEGES ON `{db_name}`.* TO `{db_user}`@`%`;'"
        grant_user_exception = DatabaseServiceException(
            self.database_server_info.host,
            f"Failed to grant prvilages for user {db_user} on {db_name}.",
        )
        self.db_run_query(grant_user_command, grant_user_exception)

    def add_user(self, db_user: str, db_pass: str, db_user_host: str = "%", force: bool = False, timeout=25):
        if self.check_user_exists(db_user, db_user_host):
            if force:
                self.remove_user(db_user, db_user_host)
            else:
                raise DatabaseServiceException(
                    self.run_on_compose_service,
                    f"User {db_user} for {db_user_host} already exists.",
                )

        add_user_command = f"'CREATE USER `{db_user}`@`%` IDENTIFIED BY \"{db_pass}\";'"
        add_user_exception = DatabaseServiceException(self.database_server_info.host, f"Failed to add user {db_user}.")
        self.db_run_query(add_user_command, add_user_exception)

    def db_export(self, db_name: str, export_file_path: str | Path):
        if not self.check_db_exists(db_name):
            raise DatabaseServiceDBNotFoundError(db_name, self.run_on_compose_service)

        if isinstance(export_file_path, Path):
            export_file_path = str(export_file_path.absolute())

        db_export_command = f"mariadb-dump {self.client_flags} {db_name} --result-file={export_file_path}"

        try:
            output = self._compose_exec_or_run(
                db_export_command,
                stream=False,
                user=self._run_user,
                rm=True,
                entrypoint=db_export_command,
            )
        except DockerException:
            raise DatabaseServiceDBExportFailed(self.run_on_compose_service, db_name)

    def db_export_all(self, export_file_path: str | Path):
        """Dump every database, including the mysql schema, into one file.

        db_export covers a single schema, which is the right unit for a bench. An
        engine-level operation needs the whole server: without the grant tables a
        restore would come back with no users, so the sites could not connect.
        """
        if isinstance(export_file_path, Path):
            export_file_path = str(export_file_path.absolute())

        db_export_command = (
            f"mariadb-dump {self.client_flags} "
            "--all-databases --single-transaction --quick --routines --events --triggers "
            f"--result-file={export_file_path}"
        )

        try:
            self._compose_exec_or_run(
                db_export_command,
                stream=False,
                user=self._run_user,
                rm=True,
                entrypoint=db_export_command,
            )
        except DockerException:
            raise DatabaseServiceDBExportFailed(self.run_on_compose_service, "--all-databases")

    def db_create(self, db_name):
        create_db_command = f"'CREATE DATABASE IF NOT EXISTS `{db_name}`';"
        create_db_exception = DatabaseServiceDBCreateFailed(self.run_on_compose_service, db_name)
        self.db_run_query(create_db_command, create_db_exception)

    def db_import(self, db_name: str, host_db_file_path: Path, force: bool = False):
        if not self.check_db_exists(db_name):
            if force:
                self.db_create(db_name)
            else:
                raise DatabaseServiceDBNotFoundError(db_name, self.run_on_compose_service)

        container_db_file_name = host_db_file_path.name
        source = str(host_db_file_path.absolute())

        destination = f"{self.run_on_compose_service}:/tmp/{container_db_file_name}"
        db_import_command = self.base_command + f" {db_name} -e 'source /tmp/{container_db_file_name}'"

        try:
            output = self.docker_client.compose.cp(source, destination, stream=False)
            output = self._compose_exec_or_run(
                db_import_command,
                stream=False,
                user=self._run_user,
                rm=True,
                entrypoint=None,
            )
        except DockerException:
            raise DatabaseServiceDBImportFailed(self.run_on_compose_service, source)
