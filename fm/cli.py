import fire
from typing import List
from pathlib import Path
from rich.console import Console
import re
from fm.sites.sites import Sites

console = Console()

# TODO configure this using config
sites_dir= Path() / __name__.split('.')[0]
#self.sites_dir= Path.home() / __name__.split('.')[0]
sites= Sites(sites_dir)

def create(sitename: str ,apps: str = '' , developer_mode: bool = True, frappe_branch: str = 'version-14',admin_pass: str = 'admin', mariadb_root_pass: str = 'root', enable_ssl: bool = False):
    sites.init(sitename)

    uid: int = fire.core.shlex.os.getuid()
    gid: int = fire.core.shlex.os.getgid()

    frappe_env: List[dict] = [
        {"name": "USERID","value": uid},
        {"name": "USERGROUP","value": gid},
        {"name": "APPS_LIST","value": apps},
        {"name": "FRAPPE_BRANCH","value": frappe_branch},
        {"name": "MARIADB_ROOT_PASS","value": mariadb_root_pass},
        {"name": "DEVELOPER_MODE","value": developer_mode},
        {"name": "ADMIN_PASS","value": admin_pass},
        {"name": "SITENAME","value": sitename}
    ]

    nginx_env: List[dict] = [
        {"name": "ENABLE_SSL","value": enable_ssl},
        {"name": "SITENAME","value": sitename}
    ]

    extra_hosts: List[dict] = [
        {"name": sitename,"ip": "127.0.0.1"}
    ]

    template_inputs: dict = {
        'frappe_env': frappe_env,
        'nginx_env' : nginx_env,
        'extra_hosts': extra_hosts
    }

    # turn off all previous
    # start the docker compose
    sites.create_site(template_inputs)

def delete(sitename: str):
    sites.init(sitename)
    # turn off the site
    sites.remove_site()


def list_sites():
    """Lists all of the available sites."""
    sites.list_sites()
    # for site in sites.keys():
    # console.print(f"[bold green] {site} [/bold green] -> [bold cyan] {sites[site]}[/bold cyan]")

def start(sitename: str):
    sites.init(sitename)
    sites.start_site()

def stop(sitename: str):
    sites.init(sitename)
    sites.stop_site()

def vscode(sitename: str):
    # check if vscode is installed
    # Attach to container
    # check if configuration can be given
    sites.init(sitename)
    sites.attach_to_site()

def config():
    pass

def run():
    fire.Fire(
        {
            'create': create,
            'list': list_sites,
            'delete': delete,
            'stop': stop,
            'start': start,
            'vscode': vscode
        }
    )
