import sys
from typing import Annotated

import typer
from typer_examples import example

from frappe_manager import CLI_BENCHES_DIRECTORY
from frappe_manager.output_manager import get_global_output_handler
from frappe_manager.site_manager.exceptions import BenchNotFoundError
from frappe_manager.utils.callbacks import sitename_callback, sites_autocompletion_callback

# docker merges later -f files over earlier ones, so the base must come first and the user's
# .override.yml last, matching DockerComposeWrapper.
_COMPOSE_ORDER = {"docker-compose.yml": 0, "docker-compose.workers.yml": 1, "docker-compose.admin-tools.yml": 2}


def _bench_omitted_by_double_dash() -> bool:
    """`fm compose -- ARGS...`: the token right after the command is click's end-of-options
    marker, so whatever bound to BENCH is a docker compose argument, not a bench.

    Read from sys.argv because click CONSUMES the first `--` (it only terminates option
    parsing), so by the time the callback runs, `fm compose ps` and `fm compose -- ps` have
    bound the identical value to BENCH; argv is the only place the two still differ. Same
    precedent as the migration gate's `get_bench_arg_from_argv`. A direct (non-CLI) call has
    no `compose` token in argv and reads as the named form.
    """
    argv = sys.argv
    try:
        i = argv.index("compose")
    except ValueError:
        return False
    return len(argv) > i + 1 and argv[i + 1] == "--"


def _benchname_callback(benchname: str | None) -> str | None:
    """The canonical must-exist resolution for a NAMED bench, skipped for the `--` form.

    With `--` the bound value is a docker compose argument (see above): it is returned raw for
    the body to shift into the passthrough args, and the bench is resolved there instead. A
    named bench that does not exist keeps failing loudly -- a typo must never be silently
    handed to docker compose -- but the refusal now teaches the `--` form.
    """
    if _bench_omitted_by_double_dash():
        return benchname
    try:
        return sitename_callback(benchname)
    except BenchNotFoundError as e:
        raise BenchNotFoundError(
            e.bench_name,
            e.path,
            message=(
                "Bench not found at {}. If '"
                + str(benchname)
                + "' was meant for docker compose, omit the bench with '--' to pick one: fm compose -- "
                + str(benchname)
            ),
        ) from e


@example(
    "Show the bench's containers",
    "{benchname} ps",
    benchname="mybench",
)
@example(
    "Pick the bench interactively",
    "-- ps",
    detail="A bare '--' in the bench position means: pick from the benches you have (the current directory's bench wins) and pass everything after it to docker compose.",
)
@example(
    "Follow the frappe logs",
    "{benchname} logs -f frappe",
    benchname="mybench",
)
@example(
    "Open a shell in a container",
    "{benchname} exec frappe bash",
    benchname="mybench",
)
@example(
    "Restart one service",
    "{benchname} restart frappe",
    benchname="mybench",
)
def compose(
    ctx: typer.Context,
    benchname: Annotated[
        str | None,
        typer.Argument(
            metavar="BENCH",
            help="Bench to act on. Omit to pick from the benches you have; 'fm compose -- ARGS' also picks, passing ARGS to docker compose.",
            autocompletion=sites_autocompletion_callback,
            callback=_benchname_callback,
        ),
    ] = None,
):
    """
    Run docker compose against a bench with all of its compose files already wired up.

    Everything after the bench name is handed to docker compose untouched, so any subcommand and flag it accepts works here.

    Put '--' in the bench position to pick the bench interactively instead of naming it: fm compose -- ps. This is also the only way to hand docker compose a flag fm would otherwise claim for itself, such as --help.

    docker compose runs with the bench directory as its working directory, so a relative path in the arguments resolves there and not against the directory you called fm from.
    """
    args = list(ctx.args)
    if _bench_omitted_by_double_dash():
        # The bound value is the FIRST docker compose argument, not a bench (see the helpers
        # above); the bench comes from the same resolution every bench command uses when the
        # name is omitted: CWD fallback, then the interactive picker.
        if benchname is not None:
            args.insert(0, benchname)
        benchname = sitename_callback(None)

    bench_path = CLI_BENCHES_DIRECTORY / str(benchname)
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

    if args:
        compose_cmd.extend(args)

    output.change_head(f"Running docker compose {' '.join(args)} on {benchname}")

    import os

    os.chdir(bench_path)
    os.execvp(compose_cmd[0], compose_cmd)
