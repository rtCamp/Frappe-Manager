import fire
from python_on_whales import docker
from pathlib import Path
import pkgutil
import jinja2
from rich.console import Console
import re

class handle_docker:
    def __init__(self,sitespath: Path):
        self.sitespath = sitespath

    def stop_all_other_sites(self,sitename: str):
        pass
    def _stop_a_site(self,sitename: str):
        docker_compose = self.sitespath / sitename / 'docker-compose.yml'
        compose = docker.compose

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
        self.sites_docker = handle_docker(self.sites_dir)

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
            self.console.print(f"Site {sitename} already exists! -> [bold cyan] {sitepath}[/bold cyan]")
            exit(1)
        sitepath.mkdir(parents=True,exist_ok=True)

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


    def delete(self,sitename: str):
        pass

    def list(self,sitename: str):
        pass

    def start(self,sitename: str):
        self.vscode = False
        pass

    def config(self):
        pass

def run():
  fire.Fire(CLI)
