import importlib
import shutil
import re
import json
from typing import List, Type
from pathlib import Path

from frappe_manager.docker_wrapper import DockerClient, DockerException

from frappe_manager.site_manager.SiteCompose import SiteCompose
from frappe_manager.site_manager.Richprint import richprint

class Site:
    def __init__(self,path: Path , name:str, verbose: bool = False):
        self.path= path
        self.name= name
        self.quiet = not verbose
        self.init()

    def init(self):
        """
        The function checks if the Docker daemon is running and exits with an error message if it is not.
        """
        self.composefile = SiteCompose(self.path / 'docker-compose.yml')
        self.docker = DockerClient(compose_file_path=self.composefile.compose_path)

        if not self.docker.server_running():
            richprint.exit("Docker daemon not running. Please start docker service.")

    def exists(self):
        """
        The `exists` function checks if a file or directory exists at the specified path.
        :return: a boolean value. If site path exits then returns `True` else `False`.
        """
        return self.path.exists()

    def validate_sitename(self) -> bool:
        """
        The function `validate_sitename` checks if a given sitename is valid by using a regular expression
        pattern.
        :return: a boolean value. If the sitename is valid, it returns True. If the sitename is not valid,
        it returns False.
        """
        sitename = self.name
        match = re.search(r'^[A-Za-z0-9](?:[A-Za-z0-9\-]{0,61}[A-Za-z0-9])?.localhost$',sitename)
        if not match:
            richprint.exit("The site name must follow a single-level subdomain Fully Qualified Domain Name (FQDN) format of localhost, such as 'suddomain.localhost'.")

    def get_frappe_container_hex(self) -> None | str:
        """
        The function `get_frappe_container_hex` searches for a Docker container with the name containing
        "-frappe" and returns its hexadecimal representation if found, otherwise returns None.
        :return: either a hexadecimal string representing the name of the Frappe container, or None if no
        Frappe container is found.
        """
        container_name = self.composefile.get_container_names()
        return container_name['frappe'].encode().hex()

    def migrate_site_compose(self) :
        """
        The `migrate_site` function checks the environment version and migrates it if necessary.
        :return: a boolean value,`True` if the site migrated else `False`.
        """
        if self.composefile.exists():
            richprint.change_head("Checking Environment Version")
            compose_version = self.composefile.get_version()
            fm_version = importlib.metadata.version('frappe-manager')
            if not compose_version == fm_version:
                status = self.composefile.migrate_compose(fm_version)
                if status:
                    richprint.print(f"Environment Migration Done: {compose_version} -> {fm_version}")
                else:
                    richprint.print(f"Environment Migration Failed: {compose_version} -> {fm_version}")
            else:
                richprint.print("Already Latest Environment Version")

    def generate_compose(self,inputs:dict) -> None:
        """
        The function `generate_compose` sets environment variables, extra hosts, and version information in
        a compose file and writes it to a file.
        
        :param inputs: The `inputs` parameter is a dictionary that contains the values which will be used in compose file.
        :type inputs: dict
        """
        self.composefile.set_envs('frappe',inputs['frappe_env'])
        self.composefile.set_envs('nginx',inputs['nginx_env'])
        self.composefile.set_extrahosts('frappe',inputs['extra_hosts'])
        self.composefile.set_container_names()
        fm_version = importlib.metadata.version('frappe-manager')
        self.composefile.set_version(fm_version)
        self.composefile.write_to_file()

    def create_dirs(self) -> bool:
        """
        The function `create_dirs` creates two directories, `workspace` and `certs`, within a specified
        path.
        """
        # create site dir
        self.path.mkdir(parents=True, exist_ok=True)
        # create compose bind dirs -> workspace
        workspace_path = self.path / 'workspace'
        workspace_path.mkdir(parents=True, exist_ok=True)
        certs_path = self.path / 'certs'
        certs_path.mkdir(parents=True, exist_ok=True)

    def start(self) -> bool:
        """
        The function starts Docker containers and prints the status of the operation.
        """
        status_text= 'Starting Docker Containers'
        richprint.change_head(status_text)
        try:
            output = self.docker.compose.up(detach=True,pull='never',stream=self.quiet)
            if self.quiet:
                richprint.live_lines(output, padding=(0,0,0,2))
            richprint.print(f"{status_text}: Done")
        except DockerException as e:
            richprint.exit(f"{status_text}: Failed")

    def pull(self):
        """
        The function pulls Docker images and displays the status of the operation.
        """
        status_text= 'Pulling Docker Images'
        richprint.change_head(status_text)
        try:
            output = self.docker.compose.pull(stream=self.quiet)
            richprint.stdout.clear_live()
            if self.quiet:
                richprint.live_lines(output, padding=(0,0,0,2))
            richprint.print(f"{status_text}: Done")
        except DockerException as e:
            richprint.warning(f"{status_text}: Failed")

    def logs(self,service:str, follow:bool=False):
        """
        The `logs` function prints the logs of a specified service, with the option to follow the logs in
        real-time.
        
        :param service: The "service" parameter is a string that specifies the name of the service whose
        logs you want to retrieve. It is used to filter the logs and only retrieve the logs for that
        specific service
        :type service: str
        :param follow: The `follow` parameter is a boolean flag that determines whether to continuously
        stream the logs or not. If `follow` is set to `True`, the logs will be streamed continuously as they
        are generated. If `follow` is set to `False`, only the existing logs will be returned, defaults to
        False
        :type follow: bool (optional)
        """
        output = self.docker.compose.logs(services=[service],no_log_prefix=True,follow=follow,stream=True)
        for source , line in output:
            line = line.decode()
            if source == 'stdout':
                if "[==".lower() in line.lower():
                    print(line)
                else:
                    richprint.stdout.print(line)

    def frappe_logs_till_start(self):
        """
        The function `frappe_logs_till_start` prints logs until a specific line is found and then stops.
        """
        status_text= 'Creating Site'
        richprint.change_head(status_text)
        try:
            output = self.docker.compose.logs(services=['frappe'],no_log_prefix=True,follow=True,stream=True)

            if self.quiet:
                richprint.live_lines(output, padding=(0,0,0,2),stop_string="INFO spawned: 'bench-dev' with pid")
            else:
                for source , line in self.docker.compose.logs(services=['frappe'],no_log_prefix=True,follow=True,stream=True):
                    if not source == 'exit_code':
                        line = line.decode()
                        if "[==".lower() in line.lower():
                            print(line)
                        else:
                            richprint.stdout.print(line)
                        if "INFO spawned: 'bench-dev' with pid".lower() in line.lower():
                            break
            richprint.print(f"{status_text}: Done")
        except DockerException as e:
            richprint.warning(f"{status_text}: Failed")


    def stop(self) -> bool:
        """
        The `stop` function stops containers and prints the status of the operation using the `richprint`
        module.
        """
        status_text= 'Stopping Containers'
        richprint.change_head(status_text)
        try:
            output = self.docker.compose.stop(timeout=10,stream=self.quiet)
            if self.quiet:
                richprint.live_lines(output, padding=(0,0,0,2))
            richprint.print(f"{status_text}: Done")
        except DockerException as e:
            richprint.exit(f"{status_text}: Failed")

    def running(self) -> bool:
        """
        The `running` function checks if all the services defined in a Docker Compose file are running.
        :return: a boolean value. If the number of running containers is greater than or equal to the number
        of services listed in the compose file, it returns True. Otherwise, it returns False.
        """
        try:
            output = self.docker.compose.ps(format='json',filter='running',stream=True)
            status: dict = {}
            for source,line in output:
                if source == 'stdout':
                    status = json.loads(line.decode())
            running_containers = len(status)
            if running_containers >= len(self.composefile.get_services_list()):
                return True
            return False
        except DockerException as e:
            richprint.exit(f"{e.stdout}{e.stderr}")

    def down(self) -> bool:
        """
        The `down` function removes containers using Docker Compose and prints the status of the operation.
        """
        if self.composefile.exists():
            status_text='Removing Containers'
            richprint.change_head(status_text)
            try:
                output = self.docker.compose.down(remove_orphans=True,volumes=True,timeout=2,stream=self.quiet)
                if self.quiet:
                    exit_code = richprint.live_lines(output,padding=(0,0,0,2))
                richprint.print(f"Removing Containers: Done")
            except DockerException as e:
                richprint.exit(f"{status_text}: Failed")

    def remove(self) -> bool:
        """
        The `remove` function removes containers and then recursively  site directories.
        """
        # TODO handle low leverl error like read only, write only etc
        if self.composefile.exists():
            status_text = 'Removing Containers'
            richprint.change_head(status_text)
            try:
                output = self.docker.compose.down(remove_orphans=True,volumes=True,timeout=2,stream=self.quiet)
                if self.quiet:
                    exit_code = richprint.live_lines(output,padding=(0,0,0,2))
                richprint.print(f"Removing Containers: Done")
            except DockerException as e:
                richprint.exit(f"{status_text}: Failed")
        richprint.change_head(f"Removing Dirs")
        try:
            shutil.rmtree(self.path)
        except Exception as e:
            richprint.error(e)
            richprint.exit(f'Please remove {self.path} manually')
        richprint.change_head(f"Removing Dirs: Done")

    def shell(self,container:str, user:str | None = None):
        """
        The `shell` function spawns a shell for a specified container and user.
        
        :param container: The `container` parameter is a string that specifies the name of the container in
        which the shell command will be executed
        :type container: str
        :param user: The `user` parameter is an optional argument that specifies the user under which the
        shell command should be executed. If a user is provided, the shell command will be executed as that
        user. If no user is provided, the shell command will be executed as the default user
        :type user: str | None
        """
        # TODO check user exists
        richprint.stop()
        non_bash_supported = ['redis-cache','redis-cache','redis-socketio','redis-queue']
        try:
            if not container in non_bash_supported:
                if container == 'frappe':
                    shell_path = '/usr/bin/zsh'
                else:
                    shell_path = '/bin/bash'
                if user:
                    self.docker.compose.exec(container,user=user,command=shell_path)
                else:
                    self.docker.compose.exec(container,command=shell_path)
            else:
                if user:
                    self.docker.compose.exec(container,user=user,command='sh')
                else:
                    self.docker.compose.exec(container,command='sh')
        except DockerException as e:
             richprint.exit(f"Shell exited with error code: {e.return_code}")

    def get_site_installed_apps(self):
        """
        The function executes a command to list the installed apps for a specific site and prints the
        output.
        """
        command = f'/opt/.pyenv/shims/bench --site {self.name} list-apps'
        # command = f'which bench'
        output = self.docker.compose.exec('frappe',user='frappe',workdir='/workspace/frappe-bench',command=command,stream=True)
        for source,line in output:
            line = line.decode()
            pass
