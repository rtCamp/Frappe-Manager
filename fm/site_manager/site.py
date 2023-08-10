from python_on_whales import DockerClient, DockerException
import rich
import shutil
import re
from typing import List, Type
from pathlib import Path
from fm.site_manager.SiteCompose import SiteCompose
from fm.site_manager.Richprint import richprint

from time import sleep
from rich.live import Live
from rich.table import Table,Row
from rich.progress import Progress

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
        self.exists = self.path.exists()
        self.init()

    def init(self):
        self.composefile = SiteCompose(self.path / 'docker-compose.yml')
        self.docker = DockerClient(compose_files=[str(self.composefile.compose_path)])

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


    def generate_compose(self,inputs:dict) -> None:
        self.composefile.set_envs('frappe',inputs['frappe_env'])
        self.composefile.set_envs('nginx',inputs['nginx_env'])
        self.composefile.set_extrahosts('frappe',inputs['extra_hosts'])
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

    def logs(self,service:str):
        for t , c in self.docker.compose.logs(services=[service],no_log_prefix=True,follow=True,stream=True):
                line = c.decode()
                if "Updating DocTypes".lower() in line.lower():
                    print(line)
                else:
                    richprint.stdout.print(line,end='')

    def frappe_logs_till_start(self):
        from rich.padding import Padding
        try:
            for t , c in self.docker.compose.logs(services=['frappe'],no_log_prefix=True,follow=True,stream=True):
                line = c.decode()
                if "Updating DocTypes".lower() in line.lower():
                    print(line)
                else:
                    richprint.stdout.print(line,end='')
                if "INFO spawned: 'bench-dev' with pid".lower() in line.lower():
                    break
        except DockerException as e:
            richprint.error(f"{e.stdout}{e.stderr}")

    def stop(self) -> bool:
        try:
            self.docker.compose.down(remove_orphans=True)
        except DockerException as e:
            richprint.error(f"{e.stdout}{e.stderr}")

    def status(self) -> str:
        try:
            ps_output =self.docker.compose.ps()
            for container in ps_output:
                print(container.state.status)
        except DockerException as e:
            richprint.error(f"{e.stdout}{e.stderr}")


    def running(self) -> bool:
        try:
            ls_output = self.docker.compose.ls()
            if ls_output:
                for composeproject in ls_output:
                    if composeproject.config_files[0] == self.composefile.compose_path.absolute() and composeproject.running >= 9:
                        return True
            return False
        except DockerException as e:
            richprint.error(f"{e.stdout}{e.stderr}")


    def remove(self) -> bool:
        if self.composefile.exists:
            try:
                self.docker.compose.down(remove_orphans=True,volumes=True,timeout=2)
                # TODO handle low leverl error like read only, write only etc
                shutil.rmtree(self.path)
            except DockerException as e:
                richprint.error(f"{e.stdout}{e.stderr}")

    def shell(self,container:str, user:str | None = None):
        # TODO check user exists
        non_bash_supported = ['redis-cache','redis-cache','redis-socketio','redis-queue']
        try:
            if not container in non_bash_supported:
                if user:
                    self.docker.compose.execute(container,tty=True,user=user,command=['/bin/bash'])
                else:
                    self.docker.compose.execute(container,tty=True,command=['/bin/bash'])
            else:
                if user:
                    self.docker.compose.execute(container,tty=True,user=user,command=['sh'])
                else:
                    self.docker.compose.execute(container,tty=True,command=['sh'])
        except DockerException as e:
            richprint.error(f"{e.stdout}{e.stderr}")
