import typer
from typer_examples import example

from frappe_manager.output_manager import get_global_output_handler, spinner
from frappe_manager.utils.site import pull_docker_images


@example(
    "Pull the images fm's stack runs on",
    "",
)
def update_images(ctx: typer.Context):
    """Pull the docker images fm's stack runs on.

    Running containers keep the image they started with until they are recreated.
    """
    with spinner(get_global_output_handler(), "Pulling latest Docker images"):
        pull_docker_images()
