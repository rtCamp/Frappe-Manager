from python_on_whales import DockerClient, DockerException
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
from rich.table import Table
from rich import box

class SiteManager:
    def __init__(self, sitesdir: Path):
        self.sitesdir = sitesdir
        self.site = None
        self.sitepath = None

    def init(self, sitename: str| None = None,createdir: bool = False):
        richprint.start(f"Working")
        # check if the site name is correct
        if not self.sitesdir.exists():
            # creating the sites dir
            # TODO check if it's writeable and readable
            if createdir:
                self.sitesdir.mkdir(parents=True, exist_ok=True)
                richprint.print(f"Sites directory doesn't exists! Created at -> {str(self.sitesdir)}")
            else:
                richprint.exit(f"Sites directory doesn't exists!")

        if not self.sitesdir.is_dir():
            richprint.exit("Sites directory is not a directory! Aborting!")

        if sitename:
            sitename = sitename + ".localhost"
            sitepath: Path = self.sitesdir / sitename
            self.site: Site = Site(sitepath, sitename)
            self.migrate_site()

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
        richprint.change_head("Stopping all other sites !")
        exclude = [self.site.name]
        site_compose: list = self.__get_all_sites_path(exclude)
        if site_compose:
            for site_compose_path in site_compose:
                docker = DockerClient(compose_files=[site_compose_path])
                try:
                    docker.compose.stop(timeout=2)
                except DockerException as e:
                    richprint.error(f"{e.stdout}{e.stderr}")
        richprint.print("Stopped all sites !")

    def create_site(self, template_inputs: dict):
        if self.site.exists():
            richprint.exit(
                f"Site {self.site.name} already exists! Aborting! -> [bold cyan] {self.site.path}[/bold cyan]"
            )
        self.stop_sites()
        # check if ports are available
        self.check_ports()
        richprint.change_head(f"Creating Site Directory")
        self.site.create_dirs()
        richprint.change_head(f"Generating Compose")
        self.site.generate_compose(template_inputs)
        richprint.change_head(f"Pulling Docker Images")
        self.site.pull()
        richprint.change_head(f"Starting Site")
        self.site.start()
        self.site.frappe_logs_till_start()
        richprint.change_head(f"Started site")
        self.info()

    def remove_site(self):
        # TODO maybe the site is running and folder has been delted and all the containers are there. We need to clean it.
        # check if it exits
        if not self.site.exists():
            richprint.exit(
                f"Site {self.site.name} doesn't exists! Aborting! -> [bold cyan] {self.site.path}[/bold cyan]"
            )

        richprint.change_head(f"Removing Site")
        # check if running -> stop it
        # remove dir
        self.site.remove()
        richprint.stop()

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
        richprint.stop()
        if running:
            columns_data = [ f"[b]{x['name']}[/b]\n[dim]{x['path']}[/dim]" for x in running ]
            panel = Panel(Columns(columns_data),title='Running',title_align='left',style='green')
            richprint.stdout.print(panel)

        if stale:
            columns_data = [ f"[b]{x['name']}[/b]\n[dim]{x['path']}[/dim]" for x in stale ]
            panel = Panel(Columns(columns_data),title='Stale',title_align='left',style='dark_turquoise')
            richprint.stdout.print(panel)

    def stop_site(self):
        if not self.site.exists():
            richprint.exit(
                f"Site {self.site.name} doesn't exists! Aborting!"
            )
        self.stop_sites()
        self.site.stop()

    def start_site(self):
        if not self.site.exists():
            richprint.exit(
                f"Site {self.site.name} doesn't exists! Aborting!"
            )
        # stop all sites
        self.stop_sites()
        if not self.site.running():
            self.check_ports()
        # start the provided site
        richprint.change_head(f"Pulling Docker Images")
        self.site.pull()
        richprint.change_head(f"Starting site")
        self.site.start()
        self.site.frappe_logs_till_start()
        richprint.change_head(f"Started site")
        richprint.stop()

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
            richprint.change_head("Attaching to Container")
            richprint.stop()
            subprocess.run(vscode_cmd,shell=True)
        else:
            print(f"Site: {self.site.name} is not running!!")

    def logs(self,service:str,follow):
        if not self.site.exists():
            richprint.exit(
                f"Site {self.site.name} doesn't exists! Aborting!"
            )
        richprint.change_head(f"Showing logs")
        if self.site.running():
            self.site.logs(service,follow)
        else:
            richprint.error(
                f"Site {self.site.name} not running!"
            )
        richprint.stop()

    def check_ports(self):
        richprint.update_head("Checking Ports")
        to_check = [9000,80,443]
        already_binded = []

        for port in to_check:
        # check port using lsof
            cmd = f"lsof -iTCP:{port} -sTCP:LISTEN -P -n"
            try:
                output = subprocess.run(cmd,check=True,shell=True,capture_output=True)
                if output.returncode == 0:
                    already_binded.append(port)
            except subprocess.CalledProcessError as e:
                pass

        if already_binded:
            # TODO handle if ports are open using docker
            # show warning and exit
            #richprint.error(f"{' '.join([str(x) for x in already_binded])} ports { 'are' if len(already_binded) > 1 else 'is' } already in use. Please free these ports.")
            richprint.exit(f" Whoa there! Looks like the {' '.join([ str(x) for x in already_binded ])} { 'ports are' if len(already_binded) > 1 else 'port is' } having a party already! Can you do us a solid and free up those ports? They're in high demand and ready to mingle!")
        richprint.change_head("Checking Ports")

    def shell(self,container:str, user:str | None):
        if not self.site.exists():
            richprint.exit(
                f"Site {self.site.name} doesn't exists! Aborting!"
            )
        if self.site.running():
            if container == 'frappe':
                if not user:
                    user = 'frappe'
            richprint.change_head(f"Executing into shell")
            richprint.stop()
            self.site.shell(container,user)
        else:
            richprint.exit(
                f"Site {self.site.name} not running!"
            )
        richprint.change_head(f"Started site")
        richprint.stop()

    def info(self):
        if not self.site.exists():
            richprint.exit(
                f"Site {self.site.name} doesn't exists! Aborting!"
            )
        richprint.change_head(f"Getting site info")
        site_config_file = self.site.path / 'workspace' / 'frappe-bench' / 'sites' / self.site.name / 'site_config.json'
        db_user = None
        db_pass = None
        if site_config_file.exists():
            with open(site_config_file,'r') as f:
                site_config = json.load(f)
                db_user = site_config['db_name']
                db_pass= site_config['db_password']

        frappe_password = self.site.composefile.get_envs('frappe')['ADMIN_PASS']
        site_info_table = Table(box=box.ASCII2,show_lines=True,show_header=False)
        data = {
            "Site Url":f"http://{self.site.name}",
            "Site Root":f"{self.site.path.absolute()}",
            "Mailhog Url":f"http://{self.site.name}/mailhog",
            "Adminer Url":f"http://{self.site.name}/adminer",
            "Frappe Username" : "administrator",
            "Frappe Password" : frappe_password,
            "DB Host" : f"mariadb",
            "DB Name" : db_user,
            "DB User" : db_user,
            "DB Password" : db_pass,
            }
        site_info_table.add_column()
        site_info_table.add_column()
        for key in data.keys():
            site_info_table.add_row(key,data[key])
        richprint.stdout.print(site_info_table)
        # bench apps list
        richprint.stdout.print('')
        bench_apps_list_table=Table(title="Bench Apps",box=box.ASCII2,show_lines=True)
        bench_apps_list_table.add_column("App")
        bench_apps_list_table.add_column("Version")
        apps_json_file = self.site.path / 'workspace' / 'frappe-bench' / 'sites' / 'apps.json'
        if apps_json_file.exists():
            with open(apps_json_file,'r') as f:
                apps_json = json.load(f)
                for app in apps_json.keys():
                    bench_apps_list_table.add_row(app,apps_json[app]['version'])
                richprint.stdout.print(bench_apps_list_table)
        richprint.stop()

    def migrate_site(self):
        if not self.site.composefile.is_services_name_same_as_template():
            self.site.down()
        self.site.migrate_site()
        if self.site.running():
            self.site.start()

