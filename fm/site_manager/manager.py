from python_on_whales import DockerClient, DockerException
from python_on_whales import docker as Docker
from typing import List, Type
from pathlib import Path
import subprocess
import json
import shlex
import typer

from fm.site_manager.site import Site
from fm.site_manager.Richprint import richprint

from rich.columns import Columns
from rich.panel import Panel

class SiteManager:
    def __init__(self, sitesdir: Path):
        self.sitesdir = sitesdir
        self.site = None
        self.sitepath = None

    def init(self, sitename: str| None = None,createdir: bool = False):
        # check if the site name is correct
        if not self.sitesdir.exists():
            # creating the sites dir
            # TODO check if it's writeable and readable
            if createdir:
                self.sitesdir.mkdir(parents=True, exist_ok=True)
                richprint.print(f"Sites directory doesn't exists! Created at -> {str(self.sitesdir)}")
            else:
                richprint.error(f"Sites directory doesn't exists!")
                raise typer.Exit(1)

        if not self.sitesdir.is_dir():
            richprint.error("Sites directory is not a directory! Aborting!")
            raise typer.Exit(1)

        if sitename:
            sitename = sitename + ".localhost"
            sitepath: Path = self.sitesdir / sitename
            self.site: Site = Site(sitepath, sitename)

    def __get_all_sites_path(self, exclude: List[str] = []):
        sites_path = []
        for d in self.sitesdir.iterdir():
            if d.is_dir():
                if not d.parts[-1] in exclude:
                    d = d / "docker-compose.yml"
                    if d.exists():
                        sites_path.append(str(d.absolute()))
        return sites_path

    def get_all_sites(self):
        sites = {}
        for dir in self.sitesdir.iterdir():
            if dir.is_dir():
                name = dir.parts[-1]
                dir = dir / "docker-compose.yml"
                if dir.exists():
                    sites[name] = str(dir.absolute())
        return sites

    def stop_sites(self):
        """ Stop all sites except the current site."""
        # this will override all
        # get list of all sub directories in the dir
        exclude = [self.site.name]
        site_compose: list = self.__get_all_sites_path(exclude)
        if site_compose:
            docker = DockerClient(compose_files=site_compose)
            try:
                docker.compose.down(timeout=2)
            except DockerException as e:
                richprint.error(f"{e.stdout}{e.stderr}")

    def create_site(self, template_inputs: dict):
        if self.site.exists:
            richprint.error(
                f"Site {self.site.name} already exists! Aborting! -> [bold cyan] {self.site.path}[/bold cyan]"
            )
            exit(1)
        self.stop_sites()
        # check if ports are available
        self.check_ports()
        self.site.create_dirs()
        self.site.generate_compose(template_inputs)
        self.site.pull()
        self.site.start()
        self.site.frappe_logs_till_start()

    def remove_site(self):
        # TODO maybe the site is running and folder has been delted and all the containers are there. We need to clean it.
        # check if it exits
        if not self.site.exists:
            richprint.error(
                f"Site {self.site.name} doesn't exists! Aborting! -> [bold cyan] {self.site.path}[/bold cyan]"
            )
            raise typer.Exit(1)
        # check if running -> stop it
        # remove dir
        self.site.remove()

    def list_sites(self):
        # format -> name , status [ 'stale', 'running' ]
        # sites_list = self.__get_all_sites_path()
        running = []
        stale = []
        sites_list = self.get_all_sites()
        if not sites_list:
            richprint.error("No available sites!!")
            typer.Exit(2)
        else:
            for name in sites_list.keys():
                temppath = self.sitesdir / name
                tempSite = Site(temppath,name)
                if tempSite.running():
                    running.append({'name': name,'path':temppath.absolute()})
                else:
                   stale.append({'name': name,'path':temppath.absolute()})
        if running:
            columns_data = [ f"[b]{x['name']}[/b]\n[dim]{x['path']}[/dim]" for x in running ]
            panel = Panel(Columns(columns_data),title='Running',title_align='left',style='green')
            richprint.stdout.print(panel)

        if stale:
            columns_data = [ f"[b]{x['name']}[/b]\n[dim]{x['path']}[/dim]" for x in stale ]
            panel = Panel(Columns(columns_data),title='Stale',title_align='left',style='dark_turquoise')
            richprint.stdout.print(panel)

    def stop_site(self):
        if not self.site.exists:
            richprint.error(
                f"Site {self.site.name} doesn't exists! Aborting!"
            )
            raise typer.Exit(1)
        self.stop_sites()
        self.site.stop()

    def start_site(self):
        if not self.site.exists:
            richprint.error(
                f"Site {self.site.name} doesn't exists! Aborting!"
            )
            raise typer.Exit(1)
        # stop all sites
        self.stop_sites()
        self.check_ports()
        # start the provided site
        self.site.pull()
        self.site.start()
        self.site.frappe_logs_till_start()

    def attach_to_site(self, user: str, extensions: List[str]):
        container_hex = self.site.get_frappe_container_hex()
        vscode_cmd = shlex.join(
            [
                "code",
                f"--folder-uri=vscode-remote://attached-container+{container_hex}+/workspace",
            ]
        )

        extensions.sort()

        labels = {
            "devcontainer.metadata": json.dumps([
                {
                    "remoteUser": user,
                    "customizations": {"vscode": {"extensions": extensions}},
                }
            ])
        }

        labels_previous = self.site.composefile.get_labels('frappe')

        # check if the extension are the same if they are different then only update
        # check if customizations key available
        try:
            extensions_previous = json.loads(labels_previous['devcontainer.metadata'])
            extensions_previous = extensions_previous[0]['customizations']['vscode']['extensions']
        except KeyError:
            extensions_previous = []

        extensions_previous.sort()

        if self.site.running():
            if not extensions_previous == extensions:
                self.site.composefile.set_labels('frappe',labels)
                self.site.composefile.write_to_file()
                self.site.start()
            # TODO check if vscode exists
            subprocess.run(vscode_cmd,shell=True)
        else:
            print(f"Site: {self.site.name} is not running!!")

    def logs(self,service:str):
        if not self.site.exists:
            richprint.error(
                f"Site {self.site.name} doesn't exists! Aborting!"
            )
            raise typer.Exit(1)
        if self.site.running():
            self.site.logs(service)
        else:
            richprint.error(
                f"Site {self.site.name} not running!"
            )

    def check_ports(self):
        to_check = [9000,80,443]
        already_binded = []

        import psutil
        for conn in psutil.net_connections('tcp4'):
            if conn.laddr.port in to_check:
                already_binded.append(conn.laddr.port)

        if already_binded:
            # show warning and exit
            #richprint.error(f"{' '.join([str(x) for x in already_binded])} ports { 'are' if len(already_binded) > 1 else 'is' } already in use. Please free these ports.")
            richprint.error(f" Whoa there! Looks like the {' '.join([ str(x) for x in already_binded ])} { 'ports are' if len(already_binded) > 1 else 'port is' } having a party already! Can you do us a solid and free up those ports? They're in high demand and ready to mingle!")
            raise typer.Exit()

    def shell(self,container:str, user:str | None):
        if not self.site.exists:
            richprint.error(
                f"Site {self.site.name} doesn't exists! Aborting!"
            )
            raise typer.Exit(1)
        if self.site.running():
            if container == 'frappe':
                if not user:
                    user = 'frappe'
            self.site.shell(container,user)
        else:
            richprint.error(
                f"Site {self.site.name} not running!"
            )
