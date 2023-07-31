import typer
from typing import Annotated, List, Optional, Set
from pathlib import Path
from fm.site_manager.manager import SiteManager
import re

app = typer.Typer()

# TODO configure this using config
sites_dir = Path() / __name__.split(".")[0]
# self.sites_dir= Path.home() / __name__.split('.')[0]
sites = SiteManager(sites_dir)


@app.command()
def create(
    sitename: Annotated[str, typer.Argument(help="Name of the site")],
    apps: Annotated[str, typer.Option(..., help="Frappe apps to install")],
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

    uid: int = fire.core.shlex.os.getuid()
    gid: int = fire.core.shlex.os.getgid()

    frappe_env: List[dict] = [
        {"name": "USERID", "value": uid},
        {"name": "USERGROUP", "value": gid},
        {"name": "APPS_LIST", "value": apps},
        {"name": "FRAPPE_BRANCH", "value": frappe_branch},
        {"name": "DEVELOPER_MODE", "value": developer_mode},
        {"name": "ADMIN_PASS", "value": admin_pass},
        {"name": "DB_NAME", "value": sites.site.name.replace(".", "-")},
        {"name": "SITENAME", "value": sites.site.name},
    ]

    nginx_env: List[dict] = [
        {"name": "ENABLE_SSL", "value": enable_ssl},
        {"name": "SITENAME", "value": sites.site.name},
    ]

    extra_hosts: List[dict] = [{"name": sitename, "ip": "127.0.0.1"}]

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

default_extension = ['ms-python.python','ms-vscode.live-server','mtxr.sqltools','visualstudioexptteam.vscodeintellicode']
def code_callback(extensions: List[str]) -> List[str]:
    extx = extensions + default_extension
    unique_ext:Set = set(extx)
    unique_ext_list:List[str] = list(unique_ext)
    return unique_ext_list

@app.command()
def code(
    sitename: Annotated[str, typer.Argument(help="Name of the site.")],
    user: Annotated[str, typer.Option(help="Connect as this user.")] = 'frappe',
    extensions: Annotated[
        Optional[List[str]],
        typer.Option(
            "--extension",
            "-e",
            help="List of extensions to install in vscode at startup.",
        )] = default_extension,
):
    """Open site in vscode."""
    # check if vscode is installed
    # Attach to container
    # cmd: -> code --folder-uri=vscode-remote://attached-container+(contianer name hex)+/workspace
    # LABELS can be added to container to support extensions and remote user
    # check if configuration can be given
    #print(extensions)
    sites.init(sitename)
    sites.attach_to_site(user,extensions)
    # sites.attach_to_site()


def config():
    pass
