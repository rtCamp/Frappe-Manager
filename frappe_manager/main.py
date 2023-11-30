import typer
import importlib
import os
import requests
import sys
import shutil
import atexit
from typing import Annotated, List, Optional, Set
from frappe_manager.site_manager.manager import SiteManager
from frappe_manager.site_manager.Richprint import richprint
from frappe_manager import CLI_DIR
from frappe_manager.logger import log

app = typer.Typer(no_args_is_help=True,rich_markup_mode='rich')

# TODO configure this using config
#sites_dir = Path().home() / __name__.split(".")[0]

def exit_cleanup():
    """
    This function is used to perform cleanup at the exit.
    """
    richprint.stop()

def cli_entrypoint():
    # logging
    logger = log.get_logger()
    logger.info('')
    logger.info(f"{':'*20}FM Invoked{':'*20}")
    logger.info('')

    # logging command provided by user
    logger.info(f"RUNNING COMMAND: {' '.join(sys.argv[1:])}")
    logger.info('-'*20)
    try:
        app()
    except Exception as e:
        logger.exception(f"Exception:  : {e}")
        raise e
    finally:
        atexit.register(exit_cleanup)

# this will be initiated later in the app_callback
sites: Optional[SiteManager] = None

def version_callback(version: Optional[bool] = None):
    if version:
        fm_version = importlib.metadata.version('frappe_manager')
        richprint.print(fm_version,emoji_code='')
        raise typer.Exit()

@app.callback()
def app_callback(
        ctx: typer.Context,
        verbose: Annotated[Optional[bool], typer.Option('--verbose','-v',help="Enable verbose output.")] = None,
        version: Annotated[
            Optional[bool], typer.Option("--version",help="Show Version.",callback=version_callback)
        ] = None,
):
    """
    FrappeManager for creating frappe development envrionments.
    """
    richprint.start(f"Working")

    # Checks for cli directory
    if not CLI_DIR.exists():
        # creating the sites dir
        # TODO check if it's writeable and readable -> by writing a file to it and catching exception
        CLI_DIR.mkdir(parents=True, exist_ok=True)
        richprint.print(f"fm directory doesn't exists! Created at -> {str(CLI_DIR)}")
    else:
        if not CLI_DIR.is_dir():
            richprint.exit("Sites directory is not a directory! Aborting!")

    # migration for directory change from CLI_DIR to CLI_DIR/sites
    # TODO remove when not required, introduced in 0.8.4
    sitesdir = CLI_DIR / 'sites'
    if not sitesdir.exists():
        richprint.change_head("Site directory migration")
        move_directory_list = []
        for site_dir in CLI_DIR.iterdir():
            if site_dir.is_dir():
                docker_compose_path = site_dir / "docker-compose.yml"
                if docker_compose_path.exists():
                    move_directory_list.append(site_dir)

        # stop all the sites
        sitesdir.mkdir(parents=True, exist_ok=True)
        sites_mananger = SiteManager(CLI_DIR)
        sites_mananger.stop_sites()
        # move all the directories
        for site in move_directory_list:
            site_name = site.parts[-1]
            new_path = sitesdir / site_name
            try:
                shutil.move(site,new_path)
                richprint.print(f"Directory migrated: {site_name}")
            except:
                logger.debug(f'Site Directory migration failed: {site}')
                richprint.warning(f"Unable to site directory migration for {site}\nPlease manually move it to {new_path}")
        richprint.print("Site directory migration: Done")

    global sites
    sites = SiteManager(sitesdir)

    sites.set_typer_context(ctx)

    if verbose:
        sites.set_verbose()


default_extension = [
    "dbaeumer.vscode-eslint",
    "esbenp.prettier-vscode",
    "ms-python.python",
    "ms-python.black-formatter",
    "ms-python.flake8",
    "visualstudioexptteam.vscodeintellicode",
    "VisualStudioExptTeam.intellicode-api-usage-examples"
]


def check_frappe_app_exists(appname: str, branchname: str | None = None):
    # check appname
    try:
        app_url = f"https://github.com/frappe/{appname}"
        app = requests.get(app_url).status_code

        if branchname:
            branch_url = f"https://github.com/frappe/{appname}/tree/{branchname}"
            # check branch
            branch = requests.get(branch_url).status_code
            return {
                "app": True if app == 200 else False,
                "branch": True if branch == 200 else False,
            }
        return {"app": True if app == 200 else False}
    except Exception:
        richprint.exit("Not able to connect to github.com.")


def apps_validation(value: List[str] | None):
    # don't allow frappe the be included throw error
    if value:
        for app in value:
            appx = app.split(":")
            if appx == "frappe":
                raise typer.BadParameter("Frappe should not be included here.")
            if len(appx) == 1:
                exists = check_frappe_app_exists(appx[0])
                if not exists["app"]:
                    raise typer.BadParameter(f"{app} is not a valid FrappeVerse app!")
            if len(appx) == 2:
                exists = check_frappe_app_exists(appx[0], appx[1])
                if not exists["app"]:
                    raise typer.BadParameter(f"{app} is not a valid FrappeVerse app!")
                if not exists["branch"]:
                    raise typer.BadParameter(
                        f"{appx[1]} is not a valid branch of {appx[0]}!"
                    )
            if len(appx) > 2:
                raise typer.BadParameter(
                    f"App should be specified in format <appname>:<branch> or <appname> "
                )
    return value


def frappe_branch_validation_callback(value: str):
    if value:
        exists = check_frappe_app_exists("frappe", value)
        if exists['branch']:
            return value
        else:
            raise typer.BadParameter(f"Frappe branch -> {value} is not valid!! ")

@app.command(no_args_is_help=True)
def create(
    sitename: Annotated[str, typer.Argument(help="Name of the site")],
    apps: Annotated[
        Optional[List[str]],
        typer.Option(
            "--apps", "-a", help="FrappeVerse apps to install. App should be specified in format <appname>:<branch> or <appname>.", callback=apps_validation,
            show_default=False
        ),
    ] = None,
    developer_mode: Annotated[bool, typer.Option(help="Enable developer mode")] = True,
    frappe_branch: Annotated[
        str, typer.Option(help="Specify the branch name for frappe app",callback=frappe_branch_validation_callback)
    ] = "version-15",
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

    Frappe\[version-14] will be installed by default.

    [bold white on black]Examples:[/bold white on black]

    [bold]# Install frappe\[version-14][/bold]
    $ [blue]fm create example[/blue]

    [bold]# Install frappe\[version-15-beta][/bold]
    $ [blue]fm create example --frappe-branch version-15-beta[/blue]

    [bold]# Install frappe\[version-14], erpnext\[version-14] and hrms\[version-14][/bold]
    $ [blue]fm create example --apps erpnext:version-14 --apps hrms:version-14[/blue]

    [bold]# Install frappe\[version-15-beta], erpnext\[version-15-beta] and hrms\[version-15-beta][/bold]
    $ [blue]fm create example --frappe-branch version-15-beta --apps erpnext:version-15-beta --apps hrms:version-15-beta[/blue]
    """

    sites.init(sitename)

    uid: int = os.getuid()
    gid: int = os.getgid()

    frappe_env: dict = {
        "USERID": uid,
        "USERGROUP": gid,
        "APPS_LIST": ",".join(apps) if apps else None,
        "FRAPPE_BRANCH": frappe_branch,
        "DEVELOPER_MODE": developer_mode,
        "ADMIN_PASS": admin_pass,
        "DB_NAME": sites.site.name.replace(".", "-"),
        "SITENAME": sites.site.name,
    }

    nginx_env: dict = {
        "ENABLE_SSL": enable_ssl,
        "SITENAME": sites.site.name,
    }

    # fix for macos
    extra_hosts: List[str] = [f"{sites.site.name}:127.0.0.1"]

    template_inputs: dict = {
        "frappe_env": frappe_env,
        "nginx_env": nginx_env,
        "extra_hosts": extra_hosts,
    }
    # turn off all previous
    # start the docker compose
    sites.create_site(template_inputs)


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
    service: Annotated[str, typer.Option(help="Specify Service")] = "frappe",
    follow: Annotated[bool, typer.Option(help="Follow logs.")] = False,
):
    """Show logs for the given site. """
    sites.init(sitename)
    sites.logs(service,follow)


@app.command(no_args_is_help=True)
def shell(
    sitename: Annotated[str, typer.Argument(help="Name of the site.")],
    user: Annotated[str, typer.Option(help="Connect as this user.")] = None,
    service: Annotated[str, typer.Option(help="Specify Service")] = "frappe",
):
    """Open shell for the give site. """
    sites.init(sitename)
    sites.shell(service, user)

@app.command(no_args_is_help=True)
def info(
    sitename: Annotated[str, typer.Argument(help="Name of the site.")],
):
    """Shows information about given site."""
    sites.init(sitename)
    sites.info()

# @app.command()
# def doctor():
#     # Runs the doctor script in the container. or commands defined in py file
#     pass

# def db_import():
#     pass
# def db_export():
#     pass
# def site_export():
#     # backup export ()
#     pass
# def site_import():
#     # backup import ()
#     pass
# def config():
#     pass

