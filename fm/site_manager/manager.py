from python_on_whales import DockerClient
from python_on_whales import docker as Docker
from typing import List
from pathlib import Path
from fm.site_manager.site import Site
from rich.console import Console
import subprocess
import json
import shlex
import typer

console = Console()

class SiteManager:
    def __init__(self, sitesdir: Path):
        self.sitesdir = sitesdir
        self.site = None
        self.sitepath = None

    def init(self, sitename: str):
        # check if the site name is correct
        if not self.sitesdir.exists():
            # creating the sites dir
            # TODO check if it's writeable and readable
            self.sitesdir.mkdir(parents=True, exist_ok=True)
            print(f"Sites directory doesn't exists! Created at -> {str(self.sitesdir)}")

        if not self.sitesdir.is_dir():
            print("Sites directory is not a directory! Aborting!")
            exit(1)
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
            exit(1)
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
            pass
        if stale:
            pass
        # docker = DockerClient(compose_files=[site])
        # for site in sites_list:

    def stop_site(self):
        self.stop_sites()
        self.site.stop()

    def start_site(self):
        # stop all sites
        self.stop_sites()
        # start the provided site
        self.site.start()
        self.site.logs()

    def attach_to_site(self, user: str, extensions: List[str] | None):
        container_hex = self.site.get_frappe_container_hex()
        vscode_cmd = shlex.join(
            [
                "code",
                f"--folder-uri=vscode-remote://attached-container+{container_hex}+/workspace",
            ]
        )

        labels = {
            "devcontainer.metadata": json.dumps([
                {
                    "remoteUser": user,
                    "customizations": {"vscode": {"extensions": extensions}},
                }
            ])
        }
        # set user
        # set extensions
        self.site.composefile.set_labels('frappe',labels)
        self.site.composefile.write_to_file()
        if self.site.running():
            self.site.start()
            # TODO check if vscode exists
            subprocess.run(vscode_cmd,shell=True)
        else:
            print(f"Site: {self.site.name} is not running!!")

    def exec_site(self,user:str):
        pass
