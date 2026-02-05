import typer

from frappe_manager import CLI_BENCHES_DIRECTORY
from frappe_manager.output_manager import get_global_output_handler
from frappe_manager.utils.callbacks import sitename_callback


def compose(
    ctx: typer.Context,
    benchname: str = typer.Argument(..., help="Name of the bench"),
):
    """
    Run docker compose commands with auto-detected compose files.

    Automatically finds and includes all docker-compose*.yml files in the bench directory.
    """
    bench_name = sitename_callback(benchname)
    bench_path = CLI_BENCHES_DIRECTORY / bench_name
    output = get_global_output_handler()

    compose_files = sorted(bench_path.glob("docker-compose*.yml"))

    if not compose_files:
        output.display_error(f"No docker-compose files found in {bench_path}")
        raise typer.Exit(1)

    compose_cmd = ["docker", "compose"]

    for compose_file in compose_files:
        compose_cmd.extend(["-f", compose_file.name])

    if ctx.args:
        compose_cmd.extend(ctx.args)

    output.change_head(f"Running docker compose {' '.join(ctx.args or [])}")

    import os

    os.chdir(bench_path)
    os.execvp(compose_cmd[0], compose_cmd)
