from ruamel.yaml import serialize
import typer
import os
import requests
import sys
import shutil

from typing import Annotated, List, Optional, Set
from frappe_manager.services_manager.services_exceptions import ServicesNotCreated
from frappe_manager.site_manager.SiteManager import SiteManager
from frappe_manager.display_manager.DisplayManager import richprint
from frappe_manager import CLI_DIR, default_extension, SiteServicesEnum, services_manager
from frappe_manager.logger import log
from frappe_manager.docker_wrapper import DockerClient
from frappe_manager.services_manager.services import ServicesManager
from frappe_manager.migration_manager.migration_executor import MigrationExecutor
from frappe_manager.site_manager.site_exceptions import SiteException
from frappe_manager.utils.callbacks import apps_list_validation_callback, frappe_branch_validation_callback, version_callback
from frappe_manager.utils.helpers import get_container_name_prefix, is_cli_help_called, get_current_fm_version
from frappe_manager.services_manager.commands import services_app

app = typer.Typer(no_args_is_help=True,rich_markup_mode='rich')
app.add_typer(services_app, name="services", help="Handle global services.")

# this will be initiated later in the app_callback
sites: Optional[SiteManager] = None

@app.callback()
def app_callback(
        ctx: typer.Context,
        verbose: Annotated[Optional[bool], typer.Option('--verbose','-v',help="Enable verbose output.")] = None,
        version: Annotated[
            Optional[bool], typer.Option("--version",help="Show Version.",callback=version_callback)
        ] = None,
):
    """
    Frappe-Manager for creating frappe development envrionments.
    """

    ctx.obj = {}

    help_called = is_cli_help_called(ctx)
    ctx.obj["is_help_called"] = help_called

    if not help_called:

        sitesdir = CLI_DIR / 'sites'
        first_time_install = False

        richprint.start(f"Working")

        if not CLI_DIR.exists():
            # creating the sites dir
            # TODO check if it's writeable and readable -> by writing a file to it and catching exception
            CLI_DIR.mkdir(parents=True, exist_ok=True)
            sitesdir.mkdir(parents=True, exist_ok=True)
            richprint.print(f"fm directory doesn't exists! Created at -> {str(CLI_DIR)}")
            first_time_install = True
        else:
            if not CLI_DIR.is_dir():
                richprint.exit("Sites directory is not a directory! Aborting!")

        # logging
        global logger
        logger = log.get_logger()
        logger.info('')
        logger.info(f"{':'*20}FM Invoked{':'*20}")
        logger.info('')

        # logging command provided by user
        logger.info(f"RUNNING COMMAND: {' '.join(sys.argv[1:])}")
        logger.info('-'*20)


        # check docker daemon service
        if not DockerClient().server_running():
            richprint.exit("Docker daemon not running. Please start docker service.")

        if first_time_install :
            from frappe_manager.metadata_manager import MetadataManager
            from frappe_manager.migration_manager.version import Version
            metadata_manager = MetadataManager()
            current_version = Version(get_current_fm_version())
            metadata_manager.set_version(current_version)
            metadata_manager.save()

        migrations = MigrationExecutor()
        migration_status = migrations.execute()
        if not migration_status:
            richprint.exit(f"Rollbacked to previous version of fm {migrations.prev_version}.")

        global services_manager
        services_manager = ServicesManager(verbose=verbose)
        services_manager.init()
        try:
            services_manager.entrypoint_checks()
        except ServicesNotCreated as e:
            services_manager.remove_itself()
            richprint.exit(f"Not able to create services. {e}")

        if not services_manager.running():
            services_manager.start()

        global sites
        sites = SiteManager(sitesdir, services=services_manager)

        sites.set_typer_context(ctx)

        if verbose:
            sites.set_verbose()

        ctx.obj["sites"] = sites
        ctx.obj["logger"] = logger
        ctx.obj["services"] = services_manager



@app.command(no_args_is_help=True)
def create(
    sitename: Annotated[str, typer.Argument(help="Name of the site")],
    apps: Annotated[
        Optional[List[str]],
        typer.Option(
            "--apps", "-a", help="FrappeVerse apps to install. App should be specified in format <appname>:<branch> or <appname>.", callback=apps_list_validation_callback,
            show_default=False
        ),
    ] = None,
    developer_mode: Annotated[bool, typer.Option(help="Enable developer mode")] = True,
    frappe_branch: Annotated[
        str, typer.Option(help="Specify the branch name for frappe app",callback=frappe_branch_validation_callback)
    ] = "version-15",
    template: Annotated[bool, typer.Option(help="Create template site.")] = False,
    admin_pass: Annotated[
        str,
        typer.Option(
            help="Default Password for the standard 'Administrator' User. This will be used as the password for the Administrator User for all new sites"
        ),
    ] = "admin",
    enable_ssl: Annotated[bool, typer.Option(help="Enable https")] = False,
):
    # TODO Create markdown table for the below help
    """
    Create a new site.

    Frappe\[version-15] will be installed by default.

    [bold white on black]Examples:[/bold white on black]

    [bold]# Install frappe\[version-15][/bold]
    $ [blue]fm create example[/blue]

    [bold]# Install frappe\[develop][/bold]
    $ [blue]fm create example --frappe-branch develop[/blue]

    [bold]# Install frappe\[version-15], erpnext\[version-15] and hrms\[version-15][/bold]
    $ [blue]fm create example --apps erpnext:version-15 --apps hrms:version-15[/blue]

    [bold]# Install frappe\[version-15], erpnext\[version-14] and hrms\[version-14][/bold]
    $ [blue]fm create example --frappe-branch version-14 --apps erpnext:version-14 --apps hrms:version-14[/blue]
    """

    sites.init(sitename)

    uid: int = os.getuid()
    gid: int = os.getgid()

    environment = {
        "frappe": {
            "USERID": uid,
            "USERGROUP": gid,
            "APPS_LIST": ",".join(apps) if apps else None,
            "FRAPPE_BRANCH": frappe_branch,
            "DEVELOPER_MODE": developer_mode,
            "ADMIN_PASS": admin_pass,
            "DB_NAME": sites.site.name.replace(".", "-"),
            "SITENAME": sites.site.name,
            "MARIADB_HOST" : 'global-db',
            "MARIADB_ROOT_PASS": '/run/secrets/db_root_password',
            "CONTAINER_NAME_PREFIX": get_container_name_prefix(sites.site.name),
            "ENVIRONMENT": "dev",
        },
        "nginx": {
            "ENABLE_SSL": enable_ssl,
            "SITENAME": sites.site.name,
            "VIRTUAL_HOST": sites.site.name,
            "VIRTUAL_PORT": 80,
        },
        "worker": {
            "USERID": uid,
            "USERGROUP": gid,
        },
        "schedule": {
            "USERID": uid,
            "USERGROUP": gid,
        },
        "socketio": {
            "USERID": uid,
            "USERGROUP": gid,
        },
    }

    users: dict = {"nginx": {"uid": uid, "gid": gid}}

    template_inputs: dict = {
        "environment": environment,
        # "extra_hosts": extra_hosts,
        "user": users,
    }
    # turn off all previous
    # start the docker compose

    sites.create_site(template_inputs,template_site=template)


@app.command(no_args_is_help=True)
def delete(sitename: Annotated[str, typer.Argument(help="Name of the site")]):
    """Delete a site. """
    sites.init(sitename)
    # turn off the site
    sites.remove_site()


@app.command()
def list():
    """Lists all of the available sites. """
    sites.init()
    sites.list_sites()


@app.command(no_args_is_help=True)
def start(sitename: Annotated[str, typer.Argument(help="Name of the site")]):
    """Start a site. """
    sites.init(sitename)
    sites.start_site()


@app.command(no_args_is_help=True)
def stop(sitename: Annotated[str, typer.Argument(help="Name of the site")]):
    """Stop a site. """
    sites.init(sitename)
    sites.stop_site()


def code_command_callback(extensions: List[str]) -> List[str]:
    extx = extensions + default_extension
    unique_ext: Set = set(extx)
    unique_ext_list: List[str] = [x for x in unique_ext]
    return unique_ext_list


@app.command(no_args_is_help=True)
def code(
    sitename: Annotated[str, typer.Argument(help="Name of the site.")],
    user: Annotated[str, typer.Option(help="Connect as this user.")] = "frappe",
    extensions: Annotated[
        Optional[List[str]],
        typer.Option(
            "--extension",
            "-e",
            help="List of extensions to install in vscode at startup.Provide extension id eg: ms-python.python",
            callback=code_command_callback,
        ),
    ] = default_extension,
    force_start: Annotated[bool , typer.Option('--force-start','-f',help="Force start the site before attaching to container.")] = False
):
    """Open site in vscode. """
    sites.init(sitename)
    if force_start:
        sites.start_site()
    sites.attach_to_site(user, extensions)


@app.command(no_args_is_help=True)
def logs(
    sitename: Annotated[str, typer.Argument(help="Name of the site.")],
    service: Annotated[Optional[SiteServicesEnum], typer.Option(help="Specify service name to show container logs.")] = None,
    follow: Annotated[bool, typer.Option('--follow','-f',help="Follow logs.")] = False,
):
    """Show frappe dev server logs or container logs for a given site. """
    sites.init(sitename)
    if service:
        sites.logs(service=SiteServicesEnum(service).value,follow=follow)
    else:
        sites.logs(follow=follow)


@app.command(no_args_is_help=True)
def shell(
    sitename: Annotated[str, typer.Argument(help="Name of the site.")],
    user: Annotated[str, typer.Option(help="Connect as this user.")] = None,
    service: Annotated[SiteServicesEnum, typer.Option(help="Specify Service")] = 'frappe',
):
    """Open shell for the give site. """
    sites.init(sitename)
    if service:
        sites.shell(service=SiteServicesEnum(service).value,user=user)
    else:
        sites.shell(user=user)

@app.command(no_args_is_help=True)
def info(
    sitename: Annotated[str, typer.Argument(help="Name of the site.")],
):
    """Shows information about given site."""
    sites.init(sitename)
    sites.info()
