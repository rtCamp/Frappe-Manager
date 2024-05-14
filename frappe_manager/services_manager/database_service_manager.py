import time
from pathlib import Path
from typing import Dict, Any, Optional, Protocol, List, Union
from frappe_manager.compose_project.compose_project import ComposeProject
from frappe_manager.docker_wrapper.DockerException import DockerException
from frappe_manager.services_manager.services_exceptions import (
    DatabaseServiceDBCreateFailed,
    DatabaseServiceDBExportFailed,
    DatabaseServiceDBImportFailed,
    DatabaseServiceDBNotFoundError,
    DatabaseServiceDBRemoveFailError,
    DatabaseServiceException,
    DatabaseServicePasswordNotFound,
    DatabaseServiceStartTimeout,
    DatabaseServiceUserRemoveFailError,
)
from frappe_manager.display_manager.DisplayManager import richprint
from pydantic import BaseModel

from frappe_manager.docker_wrapper.subprocess_output import SubprocessOutput


# TODO this class will be used for validation for main config
class DatabaseServerServiceInfo(BaseModel):
    host: str
    user: str
    port: int
    password: str
    secret_path: Path

    @classmethod
    def import_from_compose_file(cls, compose_service_name: str, compose_project: ComposeProject):
        """
        Provides info about a database server
        """
        compose_service_envs = compose_project.compose_file_manager.get_envs(container=compose_service_name)

        info: Dict[str, Any] = {}
        info["user"] = 'root'
        # this also being considered as servicename
        info["host"] = compose_service_name
        info["port"] = 3306

        # TODO use fm main config here
        # secrets or password ?
        if 'MYSQL_ROOT_PASSWORD_FILE' in compose_service_envs:
            password_path: Path = compose_project.compose_file_manager.get_secret_file_path('db_root_password')
            info['secret_path'] = password_path
            info["password"] = password_path.read_text()
        elif 'MYSQL_ROOT_PASSWORD' in compose_service_envs:
            info['secret_path'] = 'env'
            info["password"] = compose_service_envs['MYSQL_ROOT_PASSWORD']
        else:
            raise DatabaseServicePasswordNotFound(compose_service_name)

        return cls(**info)


class DatabaseServiceManager(Protocol):
    database_server_info: DatabaseServerServiceInfo
    compose_project: ComposeProject

    def __init__(self, database_server_info: DatabaseServerServiceInfo, compose_project: ComposeProject) -> None:
        ...

    def remove_user(self, db_user: str, db_user_host: str = '%', remove_all_host: bool = False):
        ...

    def add_user(self, db_user: str, db_pass: str, db_user_host: str = '%', force: bool = False, timeout=25):
        ...

    def grant_user_privilages(self, db_user: str, db_name: str):
        ...

    def check_user_exists(self, db_user: str):
        ...

    def check_db_exists(self, db_name: str):
        ...

    def remove_db(self, db_name: str):
        ...

    def wait_till_db_start(self, interval: int = 5, timeout: int = 30) -> bool:
        ...

    def db_import(self, db_name: str, host_db_file_path: Path, force: bool = False):
        ...


class MariaDBManager(DatabaseServiceManager):
    def __init__(
        self,
        database_server_info: DatabaseServerServiceInfo,
        compose_project: ComposeProject,
        run_on_compose_service: Optional[str] = None,
    ) -> None:
        """
        Database manager
        """
        self.database_server_info: DatabaseServerServiceInfo = database_server_info
        self.compose_project: ComposeProject = compose_project

        if not run_on_compose_service:
            self.run_on_compose_service: str = self.database_server_info.host
        else:
            self.run_on_compose_service: str = run_on_compose_service

        self.base_command = f"/usr/bin/mariadb -u{self.database_server_info.user} -p'{self.database_server_info.password}' -P{self.database_server_info.port} -h{self.database_server_info.host} "
        self.base_query = '-e '
        self.quiet = True

    def db_run_query(
        self, query: str, raise_exception_obj: Optional[DatabaseServiceException] = None, capture_output: bool = False
    ):
        base_command = self.base_command

        if capture_output:
            base_command += '--batch --skip-column-names '

        db_query = base_command + self.base_query + query

        try:
            output = self.compose_project.docker.compose.exec(
                self.run_on_compose_service, command=db_query, stream=not capture_output
            )
            if capture_output:
                return output
            richprint.live_lines(output)
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
        db_started_command = f"mysqladmin  -P{self.database_server_info.port} -h{self.database_server_info.host} -u'{self.database_server_info.user}' -p'{self.database_server_info.password}' ping"
        try:
            output = self.compose_project.docker.compose.exec(
                self.run_on_compose_service, command=db_started_command, stream=False
            )
            return 'mysqld is alive' in " ".join(output.stdout)
        except DockerException as e:
            return False

    def get_db_users(self) -> Dict[str, str]:
        show_db_user_command = f"'SELECT User, Host FROM mysql.user;'"
        exception = DatabaseServiceException(self.database_server_info.host, 'Failed to determine mysql users.')
        output: SubprocessOutput = self.db_run_query(
            show_db_user_command, raise_exception_obj=exception, capture_output=True
        )
        user_list: Dict[str, str] = {}
        for line in output.stdout:
            username, host = line.split('\t')
            user_list[username] = host
        return user_list

    def check_user_exists(self, username: str, host: Optional[str] = None) -> bool:
        user_list = self.get_db_users()
        if not username in user_list:
            return False
        if not host:
            return True
        if not user_list[username] == host:
            return False
        return True

    def get_all_databases(self) -> List[str]:
        db_exits_commmand = f"'SHOW DATABASES;'"
        db_exits_exception = DatabaseServiceException(
            self.database_server_info.host, 'Failed to get list of all databases.'
        )
        output: SubprocessOutput = self.db_run_query(db_exits_commmand, db_exits_exception, capture_output=True)
        return output.stdout

    def check_db_exists(self, db_name: str):
        databases = self.get_all_databases()
        return db_name in databases

    def remove_user(self, db_user: str, db_user_host: str = '%', remove_all_host: bool = False):
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
            self.database_server_info.host, f'Failed to grant prvilages for user {db_user} on {db_name}.'
        )
        self.db_run_query(grant_user_command, grant_user_exception)

    def add_user(self, db_user: str, db_pass: str, db_user_host: str = '%', force: bool = False, timeout=25):
        if self.check_user_exists(db_user, db_user_host):
            if force:
                self.remove_user(db_user, db_user_host)
            else:
                raise DatabaseServiceException(
                    self.run_on_compose_service, f'User {db_user} for {db_user_host} already exists.'
                )

        add_user_command = f"'CREATE USER `{db_user}`@`%` IDENTIFIED BY \"{db_pass}\";'"
        add_user_exception = DatabaseServiceException(self.database_server_info.host, f'Failed to add user {db_user}.')
        self.db_run_query(add_user_command, add_user_exception)

    def db_export(self, db_name: str, export_file_path: Union[str, Path]):
        if not self.check_db_exists(db_name):
            raise DatabaseServiceDBNotFoundError(db_name, self.run_on_compose_service)

        if isinstance(export_file_path, Path):
            export_file_path = str(export_file_path.absolute())

        db_export_command = f"mysqldump -u'{self.database_server_info.user}' -p'{self.database_server_info.password}' -h'{self.database_server_info.host}' -P{self.database_server_info.port} {db_name} --result-file={export_file_path}"

        try:
            output = self.compose_project.docker.compose.exec(
                self.run_on_compose_service, command=db_export_command, stream=False
            )
        except DockerException:
            raise DatabaseServiceDBExportFailed(self.run_on_compose_service, db_name)

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
            output = self.compose_project.docker.compose.cp(source, destination, stream=False)
            output = self.compose_project.docker.compose.exec(
                self.run_on_compose_service, command=db_import_command, stream=False
            )
        except DockerException:
            raise DatabaseServiceDBImportFailed(self.run_on_compose_service, source)
