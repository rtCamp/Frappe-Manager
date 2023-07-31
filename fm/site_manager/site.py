from python_on_whales import DockerClient
import shutil
from typing import List
from pathlib import Path
import jinja2
import pkgutil
import re

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
        self.docker = DockerClient(compose_files=[str(self.get_compose_path())])

    def exists(self) -> bool:
        return self.path.exists()

    def get_compose_path(self) -> Path:
        return(self.path / "docker-compose.yml")

    def has_compose(self):
        compose_path = self.get_compose_path()
        return compose_path.exists()

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

    def __get_template(self,file_name: str):
        file_name = f"templates/{file_name}"
        data = pkgutil.get_data(__name__,file_name)
        return data.decode()

    def generate_compose(self,inputs:dict) -> None:
        # input var is send to template
        compose_template = self.jinja.from_string(self.__get_template('docker-compose.tmpl'))
        site_compose_content = compose_template.render(inputs=inputs)

        # saving the docker compose to the directory
        with open(self.get_compose_path(),'w') as f:
            f.write(site_compose_content)

    def create(self) -> bool:
        # create site dir
        self.path.mkdir(parents=True, exist_ok=True)
        # create compose bind dirs -> workspace
        workspace_path = self.path / 'workspace'
        workspace_path.mkdir(parents=True, exist_ok=True)
        certs_path = self.path / 'certs'
        certs_path.mkdir(parents=True, exist_ok=True)

    def start(self) -> bool:
        self.docker.compose.up(pull='always',detach=True)
        pass

    def stop(self) -> bool:
        self.docker.compose.down(remove_orphans=True)

    def status(self) -> str:
        ps_output =self.docker.compose.ps()
        for container in ps_output:
            print(container.state.status)
        #print(ps_output)

    def remove(self) -> bool:
        self.docker.compose.down(remove_orphans=True,volumes=True,timeout=30)
        # TODO handle low leverl error like read only, write only etc
        shutil.rmtree(self.path)
        #delete_dir(self.path)
