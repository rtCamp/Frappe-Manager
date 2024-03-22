import subprocess
import json
import shlex
from rich.prompt import Prompt
import typer
import shutil

from typing import List, Optional
from pathlib import Path
from datetime import datetime
from frappe_manager.site_manager import VSCODE_LAUNCH_JSON, VSCODE_TASKS_JSON, VSCODE_SETTINGS_JSON
from frappe_manager.site_manager.site import Site
from frappe_manager.display_manager.DisplayManager import richprint
from frappe_manager.docker_wrapper.DockerClient import DockerClient
from frappe_manager.docker_wrapper.DockerException import DockerException
from frappe_manager import CLI_DIR
from rich.table import Table
from frappe_manager.utils.helpers import get_sitename_from_current_path
from frappe_manager.utils.site import generate_services_table, domain_level

from frappe_manager.utils.site import generate_services_table


class SiteManager:
    def __init__(self, sitesdir: Path, services=None):
        self.sitesdir = sitesdir
        self.site = None
        self.sitepath = None
        self.verbose = False
        self.services = services
        self.typer_context: Optional[typer.Context] = None

    def init(self, sitename: str | None = None):
        """
        Initializes the SiteManager object.

        Args:
            sitename (str | None): The name of the site. If None, the default site will be used.
        """
        if sitename:
            if domain_level(sitename) == 0:
                sitename = sitename + ".localhost"
            sitepath: Path = self.sitesdir / sitename

            site_directory_exits_check_for_commands = ["create"]

            if self.typer_context:
                if self.typer_context.invoked_subcommand in site_directory_exits_check_for_commands:
                    if sitepath.exists():
                        richprint.exit(f"The site '{sitename}' already exists at {sitepath}. Aborting operation.")
                else:
                    if not sitepath.exists():
                        richprint.exit(f"The site '{sitename}' does not exist. Aborting operation.")

            self.site: Optional[Site] = Site(
                sitepath,
                sitename,
                verbose=self.verbose,
                services=self.services,
            )

    def set_verbose(self):
        """
        The function sets the "verbose" attribute of an object to True.
        """
        self.verbose = True

    def set_typer_context(self, ctx: typer.Context):
        """
        Sets the Typer context for the SiteManager.

        Parameters:
        - ctx (typer.Context): The Typer context to be set.
        """
        self.typer_context = ctx

    def get_all_sites(self, exclude: List[str] = []):
        sites = {}
        for dir in self.sitesdir.iterdir():
            if dir.is_dir() and dir.parts[-1] not in exclude:
                name = dir.parts[-1]
                dir = dir / "docker-compose.yml"
                if dir.exists():
                    sites[name] = dir
        return sites

    def stop_sites(self):
        """
        Stops all the sites except the current site.
        """
        status_text = "Halting other sites"
        richprint.change_head(status_text)

        exclude = []

        if self.site:
            exclude = [self.site.name]

        site_compose: list = list(self.get_all_sites(exclude).values())

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

    def create_site(self, template_inputs: dict, template_site: bool = False):
        """
        Creates a new site using the provided template inputs.

        Args:
            template_inputs (dict): A dictionary containing the template inputs.

        Returns:
            None
        """
        try:
            self.site.validate_sitename()

            richprint.change_head(f"Creating Site Directory")
            self.site.create_site_dir()

            richprint.change_head(f"Generating Compose")
            self.site.generate_compose(template_inputs)
            self.site.create_compose_dirs()

            if template_site:
                self.site.remove_secrets()
                richprint.exit(f"Created template site: {self.site.name}", emoji_code=":white_check_mark:")

            richprint.change_head(f"Starting Site")
            self.site.start(force=True)
            self.site.frappe_logs_till_start()
            self.site.sync_workers_compose()
            richprint.update_live()

            richprint.change_head(f"Checking site")

            # check if site is created
            if not self.site.is_site_created():
                raise Exception("Site not starting.")

            richprint.print(f"Creating Site: Done")
            self.site.remove_secrets()
            self.typer_context.obj["logger"].info(f"SITE_STATUS {self.site.name}: WORKING")
            richprint.print(f"Started site")
            self.info()
            if not ".localhost" in self.site.name:
                richprint.print(f"Please note that You will have to add a host entry to your system's hosts file to access the site locally.")

        except Exception as e:
            self.typer_context.obj["logger"].error(f"{self.site.name}: NOT WORKING\n Exception: {e}")
            richprint.stop()
            error_message = "There has been some error creating/starting the site.\n" "Please check the logs at {}"
            log_path = CLI_DIR / "logs" / "fm.log"

            richprint.error(error_message.format(log_path))

            remove_status = self.remove_site()
            if not remove_status:
                self.info()

    def remove_site(self) -> bool:
        """
        Removes the site.
        """
        richprint.stop()
        continue_remove = Prompt.ask(
            f"🤔 Do you want to remove [bold][green]'{self.site.name}'[/bold][/green]",
            choices=["yes", "no"],
            default="no",
        )
        if continue_remove == "no":
            return False

        richprint.start("Removing Site")
        self.site.remove_database_and_user()
        self.site.remove()
        return True

    def list_sites(self):
        """
        Lists all the sites and their status.
        """
        richprint.change_head("Generating site list")

        sites_list = self.get_all_sites()

        if not sites_list:
            richprint.exit("Seems like you haven't created any sites yet. To create a site, use the command: 'fm create <sitename>'.")

        list_table = Table(show_lines=True, show_header=True, highlight=True)
        list_table.add_column("Site")
        list_table.add_column("Status", vertical="middle")
        list_table.add_column("Path")

        for site_name in sites_list.keys():
            site_path = self.sitesdir / site_name
            temp_site = Site(site_path, site_name)

            row_data = f"[link=http://{temp_site.name}]{temp_site.name}[/link]"
            path_data = f"[link=file://{temp_site.path}]{temp_site.path}[/link]"

            status_color = "white"
            status_msg = "Inactive"

            if temp_site.running():
                status_color = "green"
                status_msg = "Active"

            status_data = f"[{status_color}]{status_msg}[/{status_color}]"

            list_table.add_row(row_data, status_data, path_data, style=f"{status_color}")
            richprint.update_live(list_table, padding=(0, 0, 0, 0))

        richprint.stop()
        richprint.stdout.print(list_table)

    def stop_site(self):
        """
        Stops the site.
        """
        richprint.change_head(f"Stopping site")
        self.site.stop()
        richprint.print(f"Stopped site")

    def start_site(self, force: bool = False):
        """
        Starts the site.
        """
        self.site.sync_site_common_site_config()
        self.site.start(force=force)
        self.site.frappe_logs_till_start(status_msg="Starting Site")
        self.site.sync_workers_compose()

    def attach_to_site(self, user: str, extensions: List[str], workdir: str, debugger: bool = False):
        """
        Attaches to a running site's container using Visual Studio Code Remote Containers extension.

        Args:
            user (str): The username to be used in the container.
            extensions (List[str]): List of extensions to be installed in the container.
        """

        if not self.site.running():
            richprint.exit(f"Site: {self.site.name} is not running")

        # check if vscode is installed
        vscode_path = shutil.which("code")

        if not vscode_path:
            richprint.exit("Visual Studio Code binary i.e 'code' is not accessible via cli.")

        container_hex = self.site.get_frappe_container_hex()

        vscode_cmd = shlex.join(
            [
                vscode_path,
                f"--folder-uri=vscode-remote://attached-container+{container_hex}+{workdir}",
            ]
        )
        extensions.sort()

        vscode_config_json = [
            {
                "remoteUser": user,
                "remoteEnv": {"SHELL": "/bin/zsh"},
                "customizations": {
                    "vscode": {
                        "settings": VSCODE_SETTINGS_JSON,
                        "extensions": extensions,
                    }
                },
            }
        ]

        labels = {"devcontainer.metadata": json.dumps(vscode_config_json)}

        labels_previous = self.site.composefile.get_labels("frappe")

        # check if the extension are the same if they are different then only update
        # check if customizations key available
        try:
            extensions_previous = json.loads(labels_previous["devcontainer.metadata"])
            extensions_previous = extensions_previous[0]["customizations"]["vscode"]["extensions"]

        except KeyError:
            extensions_previous = []

        extensions_previous.sort()

        if not extensions_previous == extensions:
            richprint.print(f"Extensions are changed, Recreating containers..")
            self.site.composefile.set_labels("frappe", labels)
            self.site.composefile.write_to_file()
            self.site.start()
            richprint.print(f"Recreating Containers : Done")

        # sync debugger files
        if debugger:
            richprint.change_head("Sync vscode debugger configuration")
            dot_vscode_dir = self.site.path / "workspace" / "frappe-bench" / ".vscode"
            tasks_json_path = dot_vscode_dir / "tasks"
            launch_json_path = dot_vscode_dir / "launch"
            setting_json_path = dot_vscode_dir / "settings"

            dot_vscode_config = {
                tasks_json_path: VSCODE_TASKS_JSON,
                launch_json_path: VSCODE_LAUNCH_JSON,
                setting_json_path: VSCODE_SETTINGS_JSON,
            }

            if not dot_vscode_dir.exists():
                dot_vscode_dir.mkdir(exist_ok=True, parents=True)

            for file_path in [launch_json_path, tasks_json_path, setting_json_path]:
                file_name = f"{file_path.name}.json"
                real_file_path = file_path.parent / file_name
                if real_file_path.exists():
                    backup_tasks_path = file_path.parent / f"{file_path.name}.{datetime.now().strftime('%d-%b-%y--%H-%M-%S')}.json"
                    shutil.copy2(real_file_path, backup_tasks_path)
                    richprint.print(f"Backup previous '{file_name}' : {backup_tasks_path}")

                with open(real_file_path, "w+") as f:
                    f.write(json.dumps(dot_vscode_config[file_path]))

            # install black in env
            try:
                self.site.docker.compose.exec(service="frappe", command="/workspace/frappe-bench/env/bin/pip install black", stream=True, stream_only_exit_code=True)
            except DockerException as e:
                self.typer_context.obj["logger"].error(f"black installation exception: {e}")
                richprint.warning("Not able to install black in env.")

            richprint.print("Sync vscode debugger configuration: Done")

        richprint.change_head("Attaching to Container")
        output = subprocess.run(vscode_cmd, shell=True)

        if output.returncode != 0:
            richprint.exit(f"Attaching to Container : Failed")

        richprint.print(f"Attaching to Container : Done")

    def logs(self, follow, service: Optional[str] = None):
        """
        Display logs for the site or a specific service.

        Args:
            follow (bool): Whether to continuously follow the logs or not.
            service (str, optional): The name of the service to display logs for. If not provided, logs for the entire site will be displayed.
        """
        richprint.change_head(f"Showing logs")
        try:
            if not service:
                return self.site.bench_dev_server_logs(follow)

            if not self.site.is_service_running(service):
                richprint.exit(f"Cannot show logs. [blue]{self.site.name}[/blue]'s compose service '{service}' not running!")

            self.site.logs(service, follow)

        except KeyboardInterrupt:
            richprint.stdout.print("Detected CTRL+C. Exiting.")

    def shell(self, service: str, user: str | None):
        """
        Spawns a shell for the specified service and user.

        Args:
            service (str): The name of the service.
            user (str | None): The name of the user. If None, defaults to "frappe".

        """
        richprint.change_head(f"Spawning shell")

        if service == "frappe" and not user:
            user = "frappe"

        if not self.site.is_service_running(service):
            richprint.exit(f"Cannot spawn shell. [blue]{self.site.name}[/blue]'s compose service '{service}' not running!")

        self.site.shell(service, user)

    def info(self):
        """
        Retrieves and displays information about the site.

        This method retrieves various information about the site, such as site URL, site root, database details,
        Frappe username and password, root database user and password, and more. It then formats and displays
        this information using the richprint library.
        """

        richprint.change_head(f"Getting site info")
        site_config_file = self.site.path / "workspace" / "frappe-bench" / "sites" / self.site.name / "site_config.json"

        db_user = None
        db_pass = None

        if site_config_file.exists():
            with open(site_config_file, "r") as f:
                site_config = json.load(f)
                db_user = site_config["db_name"]
                db_pass = site_config["db_password"]

        frappe_password = self.site.composefile.get_envs("frappe")["ADMIN_PASS"]
        services_db_info = self.services.get_database_info()
        root_db_password = services_db_info["password"]
        root_db_host = services_db_info["host"]
        root_db_user = services_db_info["user"]

        site_info_table = Table(show_lines=True, show_header=False, highlight=True)

        data = {
            "Site Url": f"http://{self.site.name}",
            "Site Root": f"[link=file://{self.site.path.absolute()}]{self.site.path.absolute()}[/link]",
            "Mailhog Url": f"http://{self.site.name}/mailhog",
            "Adminer Url": f"http://{self.site.name}/adminer",
            "Frappe Username": "administrator",
            "Frappe Password": frappe_password,
            "Root DB User": root_db_user,
            "Root DB Password": root_db_password,
            "Root DB Host": root_db_host,
            "DB Name": db_user,
            "DB User": db_user,
            "DB Password": db_pass,
        }

        site_info_table.add_column(no_wrap=True)
        site_info_table.add_column(no_wrap=True)

        for key in data.keys():
            site_info_table.add_row(key, data[key])

        # get bench apps data
        apps_json = self.site.get_bench_installed_apps_list()

        if apps_json:
            bench_apps_list_table = Table(show_lines=True, show_edge=False, pad_edge=False, expand=True)

            bench_apps_list_table.add_column("App")
            bench_apps_list_table.add_column("Version")

            for app in apps_json.keys():
                bench_apps_list_table.add_row(app, apps_json[app]["version"])

            site_info_table.add_row("Bench Apps", bench_apps_list_table)

        running_site_services = self.site.get_services_running_status()
        running_site_workers = self.site.workers.get_services_running_status()

        if running_site_services:
            site_services_table = generate_services_table(running_site_services)
            site_info_table.add_row("Site Services", site_services_table)

        if running_site_workers:
            site_workers_table = generate_services_table(running_site_workers)
            site_info_table.add_row("Site Workers", site_workers_table)

        richprint.stdout.print(site_info_table)
