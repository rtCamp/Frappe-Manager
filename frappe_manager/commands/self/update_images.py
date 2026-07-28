import typer
from typer_examples import example

from frappe_manager.output_manager import get_global_output_handler, spinner
from frappe_manager.utils.site import pull_docker_images


@example(
    "Update all Frappe docker images to latest versions",
    "",
    detail="Pulls the latest Docker images used by FM to keep runtime images up to date.",
)
def update_images(ctx: typer.Context):
    """Pull latest FM stack docker images."""
    with spinner(get_global_output_handler(), "Pulling latest Docker images"):
        pull_docker_images()
