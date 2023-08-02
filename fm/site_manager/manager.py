from python_on_whales import DockerClient
from python_on_whales import docker as Docker
from typing import List
from pathlib import Path
from fm.site_manager.site import Site
import subprocess
import json
import shlex
import typer

from fm.site_manager.richprint import console

from rich.columns import Columns
from rich.panel import Panel


class SiteManager:
    def __init__(self, sitesdir: Path):
        self.sitesdir = sitesdir
        self.site: SiteManager = None
        self.sitepath = None

    def init(self, sitename: str| None = None,createdir: bool = False):
        # check if the site name is correct
        if not self.sitesdir.exists():
            # creating the sites dir
            # TODO check if it's writeable and readable
            if createdir:
                self.sitesdir.mkdir(parents=True, exist_ok=True)
                print(f"Sites directory doesn't exists! Created at -> {str(self.sitesdir)}")
            else:
                print(f"Sites directory doesn't exists!")
                raise typer.Exit(1)

        if not self.sitesdir.is_dir():
            print("Sites directory is not a directory! Aborting!")
            raise typer.Exit(1)

        if sitename:
            sitename = sitename + ".localhost"
            sitepath: Path = self.sitesdir / sitename
            self.site: Site = Site(sitepath, sitename)
        # check if ports -> 9000,80.443 available
        # TODO flag which can force stop if any continer is using this ports
        # docker = DockerClient()
        # print(docker.compose.ls())

    def __get_all_sites_path(self, exclude: List[str] = []):
        sites_path = []
        for dir in self.sitesdir.iterdir():
            if dir.is_dir():
                if not dir.parts[-1] in exclude:
                    dir = dir / "docker-compose.yml"
                    if dir.exists():
                        sites_path.append(str(dir.absolute()))
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
        # this will override all
        # get list of all sub directories in the dir
        exclude = [self.site.name]
        site_compose: list = self.__get_all_sites_path(exclude)
        if site_compose:
            docker = DockerClient(compose_files=site_compose)
            docker.compose.down()

    def create_site(self, template_inputs: dict):
        if self.site.exists:
            console.print(
                f"Site {self.site.name} already exists! Aborting! -> [bold cyan] {self.site.path}[/bold cyan]"
            )
            exit(1)
        self.site.create_dirs()
        self.site.generate_compose(template_inputs)
        self.stop_sites()
        self.site.start()

    def remove_site(self):
        # TODO maybe the site is running and folder has been delted and all the containers are there. We need to clean it.
        # check if it exits
        if not self.site.exists:
            console.print(
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
            console.print("No available sites!!")
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
            console.print(panel)

            # print in live panel with define input
            #panels = [Panel() for name,]
            #running_coloumn = Columns(['sdfdsf','wow','no'],align="center")
            #running_panel = Panel(title='Running')
            # pass

        if stale:
            columns_data = [ f"[b]{x['name']}[/b]\n[dim]{x['path']}[/dim]" for x in stale ]
            panel = Panel(Columns(columns_data),title='Stale',title_align='left',style='dark_turquoise')
            console.print(panel)

            # for i in col:
            #     c.print(i)
        # docker = DockerClient(compose_files=[site])
        # for site in sites_list:

    def stop_site(self):
        if not self.site.exists:
            console.print(
                f"Site {self.site.name} doesn't exists! Aborting!"
            )
            raise typer.Exit(1)
        self.stop_sites()
        self.site.stop()

    def start_site(self):
        if not self.site.exists:
            console.print(
                f"Site {self.site.name} doesn't exists! Aborting!"
            )
            raise typer.Exit(1)
        # stop all sites
        self.stop_sites()
        # start the provided site
        self.site.start()
        #self.site.logs()

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

        # check if the extension are the same if they are different then only update
        labels_pre = self.site.composefile.get_labels('frappe')
        extensions_pre = json.loads(labels_pre['devcontainer.metadata'])
        extensions_pre = extensions_pre[0]['customizations']['vscode']['extensions']
        extensions_pre.sort()

        if self.site.running():
            if extensions_pre:
                if not extensions_pre == extensions:
                    self.site.composefile.set_labels('frappe',labels)
                    self.site.composefile.write_to_file()
                    self.site.start()
            # TODO check if vscode exists
            subprocess.run(vscode_cmd,shell=True)
        else:
            print(f"Site: {self.site.name} is not running!!")

    def logs(self,service:str):
        if not self.site.exists:
            console.print(
                f"Site {self.site.name} doesn't exists! Aborting!"
            )
            raise typer.Exit(1)
        if self.site.running():
            self.site.logs(service)
        else:
            console.print(
                f"Site {self.site.name} not running!"
            )

    def shell(self,container:str, user:str | None):
        if not self.site.exists:
            console.print(
                f"Site {self.site.name} doesn't exists! Aborting!"
            )
            raise typer.Exit(1)
        if self.site.running():
            if container == 'frappe':
                if not user:
                    user = 'frappe'
            self.site.shell(container,user)
        else:
            console.print(
                f"Site {self.site.name} not running!"
            )
