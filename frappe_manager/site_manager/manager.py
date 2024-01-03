from frappe_manager import CLI_DIR
from frappe_manager.docker_wrapper import DockerClient, DockerException
from typing import List, Optional
from pathlib import Path
import subprocess
import json
import shlex
import typer
import shutil
import typer
from frappe_manager.global_services.GlobalServices import GlobalServices

from frappe_manager.site_manager.site import Site
from frappe_manager.utils import check_ports_with_msg as check_ports
from frappe_manager.console_manager.Richprint import richprint

from rich.columns import Columns
from rich.panel import Panel
from rich.table import Table
from rich import box


class SiteManager:
    def __init__(self, sitesdir: Path, global_service: GlobalServices):
        self.sitesdir = sitesdir
        self.site = None
        self.sitepath = None
        self.global_service = global_service
        self.verbose = False
        self.typer_context: Optional[typer.Context] = None

    def init(self, sitename: str | None = None):
        """
        The `init` function initializes a site by checking if the site directory exists, creating it if
        necessary, and setting the site name and path.

        :param sitename: The `sitename` parameter is a string that represents the name of the site. It is
        optional and can be set to `None`. If a value is provided, it will be used to create a site path by
        appending ".localhost" to the sitename
        :type sitename: str| None
        """

        if sitename:
            if not ".localhost" in sitename:
                sitename = sitename + ".localhost"
            sitepath: Path = self.sitesdir / sitename

            site_directory_exits_check_for_commands = ["create"]
            skip_check_for_commands = ["delete"]

            if (
                self.typer_context.invoked_subcommand
                in site_directory_exits_check_for_commands
            ):
                if sitepath.exists():
                    richprint.exit(
                        f"The site '{sitename}' already exists at {sitepath}. Aborting operation."
                    )
            else:
                if not self.typer_context.invoked_subcommand in skip_check_for_commands:
                    if not sitepath.exists():
                        richprint.exit(
                            f"The site '{sitename}' does not exist. Aborting operation."
                        )

            self.site: Site = Site(
                sitepath,
                sitename,
                global_service=self.global_service,
                verbose=self.verbose,
            )

    def set_verbose(self):
        """
        The function sets the "verbose" attribute of an object to True.
        """
        self.verbose = True

    def set_typer_context(self, ctx: typer.Context):
        """
        The function sets the typer context from the
        :param typer context
        :type ctx: typer.Context
        """
        self.typer_context = ctx

    def __get_all_sites_path(self, exclude: List[str] = []):
        """
        The function `__get_all_sites_path` returns a list of paths to all the `docker-compose.yml` files in
        the `sitesdir` directory, excluding any directories specified in the `exclude` list.

        :param exclude: The `exclude` parameter is a list of strings that contains the names of directories
        to be excluded from the list of sites paths
        :type exclude: List[str]
        :return: a list of paths to `docker-compose.yml` files within directories in `self.sitesdir`,
        excluding any directories specified in the `exclude` list.
        """
        sites_path = []
        for d in self.sitesdir.iterdir():
            if d.is_dir():
                if not d.parts[-1] in exclude:
                    d = d / "docker-compose.yml"
                    if d.exists():
                        sites_path.append(d)
        return sites_path

    def get_all_sites(self):
        """
        The function `get_all_sites` returns a dictionary of site names and their corresponding
        docker-compose.yml file paths from a given directory.
        :return: a dictionary where the keys are the names of directories within the `sitesdir` directory
        and the values are the paths to the corresponding `docker-compose.yml` files within those
        directories.
        """
        sites = {}
        for dir in self.sitesdir.iterdir():
            if dir.is_dir():
                name = dir.parts[-1]
                dir = dir / "docker-compose.yml"
                if dir.exists():
                    sites[name] = str(dir)
        return sites

    def stop_sites(self):
        """
        The `stop_sites` function stops all sites except the current site by halting their Docker
        containers.
        """
        status_text = "Halting other sites"
        richprint.change_head(status_text)
        if self.site:
            exclude = [self.site.name]
            site_compose: list = self.__get_all_sites_path(exclude)
        else:
            site_compose: list = self.__get_all_sites_path()
        if site_compose:
            for site_compose_path in site_compose:
                docker = DockerClient(compose_file_path=site_compose_path)
                try:
                    output = docker.compose.stop(timeout=10, stream=not self.verbose)
                    if not self.verbose:
                        richprint.live_lines(output, padding=(0, 0, 0, 2))
                except DockerException as e:
                    richprint.exit(f"{status_text}: Failed")
        richprint.print(f"{status_text}: Done")

    def create_site(self, template_inputs: dict, timeout=60):
        """
        The `create_site` function creates a new site directory, generates a compose file, pulls the
        necessary images, starts the site, and displays information about the site.

        :param template_inputs: The `template_inputs` parameter is a dictionary that contains the inputs or
        configuration values required to generate the compose file for the site. These inputs can be used to
        customize the site's configuration, such as database settings, domain name, etc
        :type template_inputs: dict
        """
        # check if provided sitename is valid and only one level subdom of localhost
        self.site.validate_sitename()
        # self.stop_sites()
        # check if ports are available
        richprint.change_head(f"Creating Site Directory")
        self.site.create_site_dir()
        richprint.print("Creating Site Directory: Done")
        richprint.change_head(f"Generating Compose")
        self.site.generate_compose(template_inputs)
        self.site.create_compose_dirs()
        self.site.pull()
        richprint.change_head(f"Starting Site")
        self.site.start()
        self.site.frappe_logs_till_start()
        self.site.sync_workers_compose()
        richprint.update_live()
        richprint.change_head(f"Checking Site")

        # check if site is created
        if self.site.is_site_created():
            richprint.print(f"Creating Site: Done")
            self.site.remove_secrets()
            self.typer_context.obj["logger"].info(
                f"SITE_STATUS {self.site.name}: WORKING"
            )
            richprint.print(f"Started site")
            self.info()
        else:
            self.typer_context.obj["logger"].error(f"{self.site.name}: NOT WORKING")
            richprint.stop()
            richprint.error(
                f"There has been some error creating/starting the site.\nPlease check the logs at {CLI_DIR/ 'logs'/'fm.log'}"
            )
            # prompt if site not working to delete the site
            if typer.confirm(f"Do you want to delete this site {self.site.name}?"):
                richprint.start("Removing Site")
                self.remove_site()
            else:
                self.info()

    def remove_site(self):
        """
        The `remove_site` function checks if a site exists, stops it if it is running, and then removes the
        site directory.
        """
        # TODO maybe the site is running and folder has been delted and all the containers are there. We need to clean it.
        richprint.change_head(f"Removing Site")
        # check if running -> stop it
        # remove dir
        self.site.remove()
        self.site.remove_database_and_user()

    def list_sites(self):
        """
        The `list_sites` function retrieves a list of sites, categorizes them as either running or stale,
        and displays them in separate panels using the Rich library.
        """
        # format -> name , status [ 'stale', 'running' ]
        # sites_list = self.__get_all_sites_path()

        running = []
        stale = []
        sites_list = self.get_all_sites()
        if not sites_list:
            richprint.error("No sites available !")
            typer.Exit(2)
        else:
            for name in sites_list.keys():
                temppath = self.sitesdir / name
                tempSite = Site(temppath, name, global_service=self.global_service)
                if tempSite.running():
                    running.append(
                        {
                            "name": name,
                            "path": temppath.absolute(),
                        }
                    )
                else:
                    stale.append(
                        {
                            "name": name,
                            "path": temppath.absolute(),
                        }
                    )
                # stale.append({'name': name,'path':temppath.absolute()})
        richprint.stop()

        if running:
            columns_data = [
                f"[b]{x['name']}[/b]\n[dim]{x['path']}[/dim]"
                for x in running
            ]
            panel = Panel(
                Columns(columns_data),
                title="Running",
                title_align="left",
                style="green",
            )
            richprint.stdout.print(panel)

        if stale:
            columns_data = [
                f"[b]{x['name']}[/b]\n[dim]{x['path']}[/dim]"
                for x in stale
            ]
            # columns_data = [ f"[b]{x['name']}[/b]\n[dim]{x['path']}[/dim]" for x in stale ]
            panel = Panel(
                Columns(columns_data),
                title="Stale",
                title_align="left",
                style="dark_turquoise",
            )
            richprint.stdout.print(panel)

    def stop_site(self):
        """
        The function `stop_site` checks if a site exists, stops it if it does, and prints a message
        indicating that the site has been stopped.
        """
        richprint.change_head(f"Stopping site")
        # self.stop_sites()
        self.site.stop()
        richprint.print(f"Stopped site")

    def start_site(self):
        """
        The function `start_site` checks if a site exists, stops all sites, checks ports, pulls the site,
        and starts it.
        """
        # stop all sites
        # self.stop_sites()
        self.migrate_site()
        self.site.pull()
        self.site.start()
        self.site.sync_workers_compose()
        self.site.workers.start()

    def attach_to_site(self, user: str, extensions: List[str]):
        """
        The `attach_to_site` function attaches to a running site and opens it in Visual Studio Code with
        specified extensions.

        :param user: The `user` parameter is a string that represents the username of the user who wants to
        attach to the site
        :type user: str
        :param extensions: The `extensions` parameter is a list of strings that represents the extensions to
        be installed in Visual Studio Code
        :type extensions: List[str]
        """
        if self.site.running():
            # check if vscode is installed
            vscode_path = shutil.which("code")

            if not vscode_path:
                richprint.exit("vscode(excutable code) not accessible via cli.")

            container_hex = self.site.get_frappe_container_hex()
            vscode_cmd = shlex.join(
                [
                    vscode_path,
                    f"--folder-uri=vscode-remote://attached-container+{container_hex}+/workspace",
                ]
            )
            extensions.sort()
            labels = {
                "devcontainer.metadata": json.dumps(
                    [
                        {
                            "remoteUser": user,
                            "customizations": {"vscode": {"extensions": extensions}},
                        }
                    ]
                )
            }

            labels_previous = self.site.composefile.get_labels("frappe")

            # check if the extension are the same if they are different then only update
            # check if customizations key available
            try:
                extensions_previous = json.loads(
                    labels_previous["devcontainer.metadata"]
                )
                extensions_previous = extensions_previous[0]["customizations"][
                    "vscode"
                ]["extensions"]
            except KeyError:
                extensions_previous = []

            extensions_previous.sort()

            if not extensions_previous == extensions:
                richprint.print(f"Extensions are changed, Recreating containers..")
                self.site.composefile.set_labels("frappe", labels)
                self.site.composefile.write_to_file()
                self.site.start()
                richprint.print(f"Recreating Containers : Done")
            # TODO check if vscode exists
            richprint.change_head("Attaching to Container")
            output = subprocess.run(vscode_cmd, shell=True)
            if output.returncode != 0:
                richprint.exit(f"Attaching to Container : Failed")
            richprint.print(f"Attaching to Container : Done")
        else:
            richprint.print(f"Site: {self.site.name} is not running")

    def logs(self, follow,service: str = None):
        """
        The `logs` function checks if a site exists, and if it does, it shows the logs for a specific
        service. If the site is not running, it displays an error message.

        :param service: The `service` parameter is a string that represents the specific service or
        component for which you want to view the logs. It could be the name of a specific container
        :type service: str
        :param follow: The "follow" parameter is a boolean value that determines whether to continuously
        follow the logs or not. If "follow" is set to True, the logs will be continuously displayed as they
        are generated. If "follow" is set to False, only the existing logs will be displayed
        """
        richprint.change_head(f"Showing logs")

        if self.site.running():
            if service:
                self.site.logs(service, follow)
            else:
                self.site.bench_dev_server_logs(follow)
        else:
            richprint.error(f"Site {self.site.name} not running!")

    def shell(self, container: str, user: str | None):
        """
        The `shell` function checks if a site exists and is running, and then executes a shell command on
        the specified container with the specified user.

        :param container: The "container" parameter is a string that specifies the name of the container.
        :type container: str
        :param user: The `user` parameter in the `shell` method is an optional parameter that specifies the
        user for which the shell command should be executed. If no user is provided, the default user is set
        to 'frappe'
        :type user: str | None
        """
        richprint.change_head(f"Spawning shell")
        if self.site.running():
            if container == "frappe":
                if not user:
                    user = "frappe"
            self.site.shell(container, user)
        else:
            richprint.exit(f"Site {self.site.name} not running!")

    def info(self):
        """
        The `info` function retrieves information about a site, including its URL, root path, database
        details, Frappe username and password, and a list of installed apps.
        """
        richprint.change_head(f"Getting site info")

        global_db_info = self.global_service.get_database_info()
        database_info = self.site.get_site_db_info()

        frappe_password = self.site.composefile.get_envs("frappe")["ADMIN_PASS"]
        site_info_table = Table(
            box=box.ROUNDED, show_lines=True, show_header=False, highlight=True
        )

        data = {
            "Site Url": f"http://{self.site.name}",
            "Site Root": f"{self.site.path.absolute()}",
            "Mailhog Url": f"http://{self.site.name}/mailhog",
            "Adminer Url": f"http://{self.site.name}/adminer",
            "Frappe Username": "administrator",
            "Frappe Password": frappe_password,
        }

        if database_info:
            data["DB Name"] = database_info["name"]
            data["DB User"] = database_info["user"]
            data["DB Password"] = database_info["password"]

        if global_db_info:
            data["DB Host"] = global_db_info["host"]
            data["Root DB User"] = global_db_info["user"]
            data["Root DB Password"] = global_db_info["password"]

        site_info_table.add_column()
        site_info_table.add_column()

        for key in data.keys():
            value = data[key]
            site_info_table.add_row(key, value)

        # bench apps list
        richprint.stdout.print("")
        bench_apps_list_table = Table(
            box=box.ROUNDED,
            show_lines=True,
            expand=True,
            show_edge=False,
            pad_edge=False,
        )
        bench_apps_list_table.add_column("App")
        bench_apps_list_table.add_column("Version")

        apps_json_file = (
            self.site.path / "workspace" / "frappe-bench" / "sites" / "apps.json"
        )
        if apps_json_file.exists():
            with open(apps_json_file, "r") as f:
                apps_json = json.load(f)
                for app in apps_json.keys():
                    bench_apps_list_table.add_row(app, apps_json[app]["version"])

            site_info_table.add_row("Bench Apps", bench_apps_list_table)
        richprint.stdout.print(site_info_table)

    def migrate_site(self):
        """
        The function `migrate_site` checks if the services name is the same as the template, if not, it
        brings down the site, migrates the site, and starts it.
        """
        richprint.change_head("Migrating Environment")
        if not self.site.composefile.is_services_name_same_as_template():
            self.site.down()
        self.site.migrate_site_compose()
