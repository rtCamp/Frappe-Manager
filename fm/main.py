import typer
from typing import Annotated, List, Optional, Set
from pathlib import Path
from fm.site_manager.manager import SiteManager
import os

app = typer.Typer()

# TODO configure this using config
sites_dir = Path() / __name__.split(".")[0]
# self.sites_dir= Path.home() / __name__.split('.')[0]
sites = SiteManager(sites_dir)
default_extension = [
    "ms-python.python",
    "ms-python.black-formatter",
    "esbenp.prettier-vscode",
    "visualstudioexptteam.vscodeintellicode",
]


def apps_validation(value: List[str] | None):
    if value:
        typer.Exit("Wrong Apps List")
    return value


@app.command()
def create(
    sitename: Annotated[str, typer.Argument(help="Name of the site")],
    apps: Annotated[
        Optional[List[str]], typer.Option("--apps", "-a", help="Frappe apps to install")
    ] = None,
    developer_mode: Annotated[bool, typer.Option(help="Enable developer mode")] = True,
    frappe_branch: Annotated[
        str, typer.Option(help="Specify the branch name for frappe app")
    ] = "version-14",
    admin_pass: Annotated[
        str,
        typer.Option(
            help="Default Password for the standard 'Administrator' User. This will be used as the password for the Administrator User for all new sites"
        ),
    ] = "admin",
    enable_ssl: Annotated[bool, typer.Option(help="Enable https")] = False,
):
    """Create a new site."""

    sites.init(sitename)

    uid: int = os.getuid()
    gid: int = os.getgid()

    # apps list as appname:version-14
    # TODO add validation for branch name

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

    print(frappe_env)
    nginx_env: dict = {
        "ENABLE_SSL": enable_ssl,
        "SITENAME": sites.site.name,
    }

    extra_hosts: List[str] = [f"{sitename}:127.0.0.1"]

    template_inputs: dict = {
        "frappe_env": frappe_env,
        "nginx_env": nginx_env,
        "extra_hosts": extra_hosts,
    }
    # turn off all previous
    # start the docker compose
    sites.create_site(template_inputs)


@app.command()
def delete(sitename: Annotated[str, typer.Argument(help="Name of the site")]):
    """Delete a site."""
    sites.init(sitename)
    # turn off the site
    sites.remove_site()


@app.command()
def list():
    """Lists all of the available sites."""
    sites.list_sites()
    # for site in sites.keys():
    # console.print(f"[bold green] {site} [/bold green] -> [bold cyan] {sites[site]}[/bold cyan]")


@app.command()
def start(sitename: Annotated[str, typer.Argument(help="Name of the site")]):
    """Start a site."""
    sites.init(sitename)
    sites.start_site()


@app.command()
def stop(sitename: Annotated[str, typer.Argument(help="Name of the site")]):
    """Stop a site."""
    sites.init(sitename)
    sites.stop_site()


def code_callback(extensions: List[str]) -> List[str]:
    extx = extensions + default_extension
    unique_ext: Set = set(extx)
    unique_ext_list: List[str] = [x for x in unique_ext]
    return unique_ext_list


@app.command()
def code(
    sitename: Annotated[str, typer.Argument(help="Name of the site.")],
    user: Annotated[str, typer.Option(help="Connect as this user.")] = "frappe",
    extensions: Annotated[
        Optional[List[str]],
        typer.Option(
            "--extension",
            "-e",
            help="List of extensions to install in vscode at startup.",
            callback=code_callback,
        ),
    ] = default_extension,
):
    """Open site in vscode."""
    # check if vscode is installed
    # Attach to container
    # cmd: -> code --folder-uri=vscode-remote://attached-container+(contianer name hex)+/workspace
    # LABELS can be added to container to support extensions and remote user
    # check if configuration can be given
    # print(extensions)
    sites.init(sitename)
    sites.attach_to_site(user, extensions)
    # sites.attach_to_site()


def config():
    pass
