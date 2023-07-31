from python_on_whales import DockerClient
import shutil
from typing import List
from pathlib import Path
import jinja2
import re
from fm.site_manager.SiteCompose import SiteCompose
import rich


def delete_dir(path: Path):
    for sub in path.iterdir():
        if sub.is_dir():
            delete_dir(sub)
        else:
            sub.unlink()
    path.rmdir()

# TODO handle all the dockers errors here

class Site:
    jinja = jinja2.Environment()

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
        self.docker.compose.up(pull='always',detach=True)

    def logs(self):
        console = rich.console.Console()
        for t,c in self.docker.compose.logs(services=['frappe'],stream=True):
            console.print(c.decode())

    def stop(self) -> bool:
        self.docker.compose.down(remove_orphans=True)

    def status(self) -> str:
        ps_output =self.docker.compose.ps()
        for container in ps_output:
            print(container.state.status)
        #print(ps_output)

    def running(self) -> bool:
        ls_output = self.docker.compose.ls()
        if ls_output:
            for composeproject in ls_output:
                if composeproject.config_files[0] == self.composefile.compose_path.absolute() and composeproject.running >= 9:
                    return True
        return False


    def remove(self) -> bool:
        if self.composefile.exists:
            self.docker.compose.down(remove_orphans=True,volumes=True,timeout=30)
        # TODO handle low leverl error like read only, write only etc
        shutil.rmtree(self.path)
        #delete_dir(self.path)
        #
    def exec(self) -> None:
        self.docker.compose.execute()
