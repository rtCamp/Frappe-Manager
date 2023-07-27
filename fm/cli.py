import fire
from typing import List
from python_on_whales import DockerClient
from pathlib import Path
import pkgutil
import jinja2
from rich.console import Console
import re

def delete_dir(path: Path):
    for sub in path.iterdir():
        if sub.is_dir():
            delete_dir(sub)
        else:
            sub.unlink()
    path.rmdir()


class handle_sites:
    def __init__(self,sitespath: Path):
        self.sitespath = sitespath

    def _get_all_sites_compose_path(self, exclude: List[str] = [] ):
        temp = []
        for dir in self.sitespath.iterdir():
            if dir.is_dir():
                if not dir.parts[-1] in exclude:
                    dir = dir / "docker-compose.yml"
                    if dir.exists():
                        temp.append(str(dir.absolute()))
        return temp
    def get_all_sites(self):
        temp = {}
        for dir in self.sitespath.iterdir():
            if dir.is_dir():
                name = dir.parts[-1]
                dir = dir / "docker-compose.yml"
                if dir.exists():
                    temp[name] = str(dir.absolute())
        return temp

    def _get_site_compose_path(self,sitename: str):
        compose_path = self.sitespath/ sitename / 'docker-compose.yml'
        return str(compose_path.absolute())

    def stop_sites(self,sitename: str):
        # get list of all sub directories in the dir
        exclude=[sitename]
        site_compose:list = self._get_all_sites_compose_path(exclude)
        docker: DockerClient = DockerClient(compose_files=site_compose)
        docker.compose.down()

    def remove_site(self,sitename: str):
        # TODO maybe the site is running and folder has been delted and all the containers are there. We need to clean it.
        compose = self._get_site_compose_path(sitename)
        docker = DockerClient(compose_files=[compose])
        docker.compose.down(remove_orphans=True,volumes=True)

    def stop_site(self,sitename: str):
        compose = self._get_site_compose_path(sitename)
        docker = DockerClient(compose_files=[compose])
        docker.compose.down()

    def start_site(self,sitename: str):
        self.stop_sites(sitename)
        compose = self._get_site_compose_path(sitename)
        docker = DockerClient(compose_files=[compose])
        docker.compose.start()

class CLI:
    def __init__(self,apps: str = '' ,developer_mode: bool = True, frappe_branch: str = 'version-14',admin_pass: str = 'admin', mariadb_root_pass: str = 'root', enable_ssl: bool = False):
        self.apps = apps
        self.developer_mode = developer_mode
        self.frappe_branch= frappe_branch
        self.admin_pass= admin_pass
        self.mariadb_root_pass= mariadb_root_pass
        self.enable_ssl = enable_ssl
        self.jinja = jinja2.Environment()
        self.console = Console()

        # TODO configure this using config
        self.sites_dir= Path() / __name__.split('.')[0]
        #self.sites_dir= Path.home() / __name__.split('.')[0]

        self.sites_docker = handle_sites(self.sites_dir)

    def _template_get(self,file_name: str):
        file_name = f"templates/{file_name}"
        data = pkgutil.get_data(__name__,file_name)
        return data.decode()

    def _validate_sitename(self,sitename: str):
        sitename = str(sitename)
        match = re.search(r'^[A-Za-z0-9](?:[A-Za-z0-9\-]{0,61}[A-Za-z0-9])?',sitename)
        if len(sitename) != match.span()[-1]:
            self.console.print(f"[bold red][ERROR] : [/bold red][bold cyan]Not a valid sitename.[/bold cyan]")
            exit(2)

    def create(self,sitename: str):
        self._validate_sitename(sitename)
        sitename = sitename + ".localhost"
        sitepath = self.sites_dir / sitename
        if sitepath.exists():
            self.console.print(f"Site {sitename} already exists! Aborting! -> [bold cyan] {sitepath}[/bold cyan]")
            exit(1)

        sitepath.mkdir(parents=True,exist_ok=True)
        workspace_path = sitepath / 'workspace'
        workspace_path.mkdir(parents=True,exist_ok=True)

        uid: int = fire.core.shlex.os.getuid()
        gid: int = fire.core.shlex.os.getgid()

        frappe_env: list = [
            {"name": "USERID","value": uid},
            {"name": "USERGROUP","value": gid},
            {"name": "APPS_LIST","value": self.apps},
            {"name": "FRAPPE_BRANCH","value": self.frappe_branch},
            {"name": "MARIADB_ROOT_PASS","value": self.mariadb_root_pass},
            {"name": "DEVELOPER_MODE","value": self.developer_mode},
            {"name": "ADMIN_PASS","value": self.admin_pass},
            {"name": "SITENAME","value": sitename}
        ]
        nginx_env: list  = [
            {"name": "ENABLE_SSL","value": self.enable_ssl}
            {"name": "SITENAME","value": sitename}
        ]

        extra_hosts: list = [
            {"name": sitename,"ip": "127.0.0.1"}
        ]

        compose_template = self.jinja.from_string(self._template_get('docker-compose.tmpl'))
        site_compose_content = compose_template.render(frappe_env=frappe_env,nginx_env=nginx_env,extra_hosts=extra_hosts)
        site_compose_path = sitepath / 'docker-compose.yml'

        # saving the docker compose to the directory
        with open(site_compose_path,'w') as f:
            f.write(site_compose_content)

        # turn off all previous
        # start the docker compose
        self.sites_docker.start_site(sitename)

    def delete(self,sitename: str):
        self._validate_sitename(sitename)
        sitename = sitename + ".localhost"
        sitepath = self.sites_dir / sitename
        if not sitepath.exists():
            self.console.print(f"Site {sitename} doesn't exists! Aborting! -> [bold cyan] {sitepath}[/bold cyan]")
            exit(1)

        # turn off the site
        self.sites_docker.remove_site(sitename)

        # remove the site folder
        delete_dir(sitepath)



    def list(self,sitename: str):
        self._validate_sitename(sitename)
        sitename = sitename + ".localhost"

        sites = self.sites_docker.get_all_sites()
        for site in sites.keys():
            self.console.print(f"[bold green] {site} [/bold green] -> [bold cyan] {sites[site]}[/bold cyan]")

    def start(self,sitename: str):
        self.vscode = False
        pass

    def config(self):
        pass

def run():
  fire.Fire(CLI)
