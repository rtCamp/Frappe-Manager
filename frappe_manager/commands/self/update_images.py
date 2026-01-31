import typer
from frappe_manager.display_manager.DisplayManager import richprint
from frappe_manager.output_manager import spinner
from frappe_manager.utils.site import pull_docker_images


def update_images(ctx: typer.Context):
    """Pull latest FM stack docker images."""
    with spinner(richprint, "Pulling latest Docker images"):
        pull_docker_images()
