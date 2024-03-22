import typer
import requests
import importlib
import json
from rich.prompt import Prompt
from pathlib import Path
from frappe_manager.compose_manager.ComposeFile import ComposeFile
from frappe_manager.docker_wrapper.DockerClient import DockerClient
from frappe_manager.docker_wrapper.DockerException import DockerException
from frappe_manager.display_manager.DisplayManager import richprint
from frappe_manager.utils.helpers import install_package

update_app = typer.Typer(rich_markup_mode="rich")

@update_app.callback(invoke_without_command=True)
def update_callback(
        ctx: typer.Context,
    ):
    if ctx.invoked_subcommand == None:
        url = "https://pypi.org/pypi/frappe-manager/json"
        try:
            update_info = requests.get(url, timeout=0.1)
            update_info = json.loads(update_info.text)
            fm_version = importlib.metadata.version("frappe-manager")
            latest_version = update_info["info"]["version"]
            if not fm_version == latest_version:
                update_msg = (
                    f":arrows_counterclockwise: New update available [blue][bold]v{latest_version}[/bold][/blue]"
                    "\nDo you want to update ?"
                )
                richprint.stop()
                continue_update= Prompt.ask(update_msg, choices=["yes", "no"])

                if continue_update == 'yes':
                    install_package("frappe-manager", latest_version)
        except Exception as e:
            richprint.exit(f"Error occured while updating the app : {e}")


@update_app.command()
def images(
        ctx: typer.Context,
    ):
    services = ctx.obj['services']
    composefile = ComposeFile(loadfile=Path('docker-compose.yml'))
    images_list = []
    docker = DockerClient()
    if composefile.is_template_loaded:
        images = composefile.get_all_images()
        images.update(services.composefile.get_all_images())

        for service ,image_info in images.items():
            image = f"{image_info['name']}:{image_info['tag']}"
            images_list.append(image)

        # remove duplicates
        images_list = list(dict.fromkeys(images_list))

        for image in images_list:
            status = f"[blue]Pulling image[/blue] [bold][yellow]{image}[/yellow][/bold]"
            richprint.change_head(status,style=None)
            try:
                output = docker.pull(container_name=image , stream=True)
                richprint.live_lines(output, padding=(0, 0, 0, 2))
                richprint.print(f"{status} : Done")
            except DockerException as e:
                richprint.error(f"{status} : Failed")
                richprint.error(f"[red][bold]Error :[/bold][/red] {e}")
                continue
