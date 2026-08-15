import typer
from typer_examples import example

from frappe_manager import CLI_BENCHES_DIRECTORY
from frappe_manager.output_manager import get_global_output_handler
from frappe_manager.utils.callbacks import sitename_callback

# docker merges later -f files over earlier ones, so the base must come first and the user's
# .override.yml last, matching DockerComposeWrapper.
_COMPOSE_ORDER = {"docker-compose.yml": 0, "docker-compose.workers.yml": 1, "docker-compose.admin-tools.yml": 2}


@example(
    "Show running containers for a bench",
    "{benchname} ps",
    detail="Runs 'docker compose ps' for the bench using all discovered compose files.",
    benchname="mybench",
)
@example(
    "Start containers in detached mode",
    "{benchname} up -d",
    detail="Starts containers in detached mode using the bench's compose files.",
    benchname="mybench",
)
@example(
    "Follow logs for frappe service",
    "{benchname} logs -f frappe",
    detail="Runs 'docker compose logs -f frappe' to stream logs for the frappe service.",
    benchname="mybench",
)
@example(
    "Execute bash in frappe container",
    "{benchname} exec frappe bash",
    detail="Executes an interactive bash shell in the frappe container.",
    benchname="mybench",
)
@example(
    "Restart specific service",
    "{benchname} restart frappe",
    detail="Restarts a single service using docker compose for targeted debugging.",
    benchname="mybench",
)
@example(
    "View container resource usage",
    "{benchname} stats",
    detail="Runs 'docker compose stats' to view resource usage for bench containers.",
    benchname="mybench",
)
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

    # Order matters: docker merges later -f files over earlier ones. Glob-sorted order puts
    # docker-compose.yml LAST, so the base would override docker-compose.override.yml -- the
    # inverse of DockerComposeWrapper's contract ("appended after the base so the override
    # wins"). Base first, the fm-generated extras next, the user's override last.
    compose_files = sorted(
        bench_path.glob("docker-compose*.yml"),
        key=lambda p: (4 if p.name == "docker-compose.override.yml" else _COMPOSE_ORDER.get(p.name, 3), p.name),
    )

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
