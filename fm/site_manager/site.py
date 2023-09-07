from python_on_whales import DockerClient, DockerException
import importlib
import shutil
import re
from typing import List, Type
from pathlib import Path

from fm.site_manager.SiteCompose import SiteCompose
from fm.site_manager.Richprint import richprint

def handle_DockerException():
    pass

def delete_dir(path: Path):
    for sub in path.iterdir():
        if sub.is_dir():
            delete_dir(sub)
        else:
            sub.unlink()
    path.rmdir()

# TODO handle all the dockers errors here

class Site:
    def __init__(self,path: Path , name:str):
        self.path= path
        self.name= name
        self.init()

    def init(self):
        self.composefile = SiteCompose(self.path / 'docker-compose.yml')
        self.docker = DockerClient(compose_files=[str(self.composefile.compose_path)])

        if not self.is_docker_daemon_running():
            richprint.exit("Docker daemon not running. Please start docker service.")

    def exists(self):
        return self.path.exists()

    def is_docker_daemon_running(self) -> bool:
        # TODO don't check if docker is not required for specific commands
        docker_info= self.docker.info()
        if docker_info.id:
            return True
        return False

    def validate_sitename(self) -> bool:
        sitename = self.name
        match = re.search(r'^[A-Za-z0-9](?:[A-Za-z0-9\-]{0,61}[A-Za-z0-9])?',sitename)
        if len(sitename) != match.span()[-1]:
            return False
            # console.print(f"[bold red][ERROR] : [/bold red][bold cyan]Not a valid sitename.[/bold cyan]")
            # exit(2)
        return True

    def get_frappe_container_hex(self) -> None | str:
        containers = self.docker.ps()
        container_name = [ x.name for x in containers ]
        for name in container_name:
            frappe_container = re.search('-frappe',name)
            if not frappe_container == None:
                return frappe_container.string.encode().hex()
        return None

    def migrate_site(self) -> None:
        if self.composefile.exists():
            richprint.change_head("Checking Envrionment Version")
            compose_version = self.composefile.get_version()
            fm_version = importlib.metadata.version('fm')
            if not compose_version == fm_version:
                richprint.change_head("Migrating Environment")
                self.composefile.migrate_compose(fm_version)
                richprint.print("Migrated Environment")

    def generate_compose(self,inputs:dict) -> None:
        self.composefile.set_envs('frappe',inputs['frappe_env'])
        self.composefile.set_envs('nginx',inputs['nginx_env'])
        self.composefile.set_extrahosts('frappe',inputs['extra_hosts'])
        fm_version = importlib.metadata.version('fm')
        self.composefile.set_version(fm_version)
        self.composefile.write_to_file()

    def create_dirs(self) -> bool:
        # create site dir
        self.path.mkdir(parents=True, exist_ok=True)
        # create compose bind dirs -> workspace
        workspace_path = self.path / 'workspace'
        workspace_path.mkdir(parents=True, exist_ok=True)
        certs_path = self.path / 'certs'
        certs_path.mkdir(parents=True, exist_ok=True)

    def start(self) -> bool:
        try:
            self.docker.compose.up(detach=True)
        except DockerException as e:
            richprint.error(f"{e.stdout}{e.stderr}")
    def pull(self):
        try:
            self.docker.compose.pull(ignore_pull_failures=True)
        except DockerException as e:
            richprint.error(f"{e.stdout}{e.stderr}")

    def logs(self,service:str,follow:bool=False):
        for t , c in self.docker.compose.logs(services=[service],no_log_prefix=True,follow=follow,stream=True):
                line = c.decode()
                if "[==".lower() in line.lower():
                    print(line)
                else:
                    richprint.stdout.print(line,end='')

    def frappe_logs_till_start(self):
        from rich.padding import Padding
        try:
            for t , c in self.docker.compose.logs(services=['frappe'],no_log_prefix=True,follow=True,stream=True):
                line = c.decode()
                if "[==".lower() in line.lower():
                    print(line)
                else:
                    richprint.stdout.print(line,end='')
                if "INFO spawned: 'bench-dev' with pid".lower() in line.lower():
                    break
        except DockerException as e:
            richprint.error(f"{e.stdout}{e.stderr}")

    def stop(self) -> bool:
        try:
            self.docker.compose.stop(timeout=10)
        except DockerException as e:
            richprint.exit(f"{e.stdout}{e.stderr}")

    def status(self) -> str:
        try:
            ps_output = self.docker.compose.ps()
            for container in ps_output:
                print(container.state.status)
        except DockerException as e:
            richprint.error(f"{e.stdout}{e.stderr}")

    def running(self) -> bool:
        try:
            ls_output = self.docker.compose.ls()
            if ls_output:
                for composeproject in ls_output:
                    if composeproject.config_files[0] == self.composefile.compose_path.absolute() and composeproject.running >= len(self.composefile.get_services_list()):
                        return True
            return False
        except DockerException as e:
            richprint.exit(f"{e.stdout}{e.stderr}")

    def down(self) -> bool:
        if self.composefile.exists():
            try:
                richprint.change_head(f"Removing Containers")
                self.docker.compose.down(remove_orphans=True,timeout=2)
                # TODO handle low leverl error like read only, write only etc
            except DockerException as e:
                richprint.exit(f"{e.stdout}{e.stderr}")

    def remove(self) -> bool:
        if self.composefile.exists():
            try:
                richprint.change_head(f"Removing Containers")
                self.docker.compose.down(remove_orphans=True,volumes=True,timeout=2)
                # TODO handle low leverl error like read only, write only etc
                richprint.change_head(f"Removing Dirs")
                shutil.rmtree(self.path)
            except DockerException as e:
                richprint.exit(f"{e.stdout}{e.stderr}")

    def shell(self,container:str, user:str | None = None):
        # TODO check user exists
        non_bash_supported = ['redis-cache','redis-cache','redis-socketio','redis-queue']
        try:
            if not container in non_bash_supported:
                if container == 'frappe':
                    shell_path = '/usr/bin/zsh'
                else:
                    shell_path = '/bin/bash'
                if user:
                    self.docker.compose.execute(container,tty=True,user=user,command=[shell_path])
                else:
                    self.docker.compose.execute(container,tty=True,command=[shell_path])
            else:
                if user:
                    self.docker.compose.execute(container,tty=True,user=user,command=['sh'])
                else:
                    self.docker.compose.execute(container,tty=True,command=['sh'])
        except DockerException as e:
            richprint.error(f"{e.stdout}{e.stderr}")
