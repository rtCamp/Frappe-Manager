"""`fm self uninstall`: remove everything fm put on this host."""

import shutil
from pathlib import Path
from typing import Annotated

import typer
from typer_examples import example

from frappe_manager import CLI_DIR
from frappe_manager.docker import DockerClient
from frappe_manager.output_manager import get_global_output_handler, spinner
from frappe_manager.ssl_manager.trust_store_manager import TrustStoreManager
from frappe_manager.utils.prune import format_size
from frappe_manager.utils.uninstall import Scope, TeardownPlan, plan_teardown


def _print_plan(output, plan: TeardownPlan, *, keep_backups: bool, include_images: bool) -> None:
    if plan.benches:
        output.print(f"Benches  : {len(plan.benches)}  {', '.join(plan.benches)}", emoji_code="")
    if plan.incomplete:
        output.print(
            f"Partial  : {len(plan.incomplete)}  {', '.join(plan.incomplete)}  (never finished creating)",
            emoji_code="",
        )
    if plan.containers:
        output.print(f"Container: {len(plan.containers)}", emoji_code="")
        for name in plan.containers:
            output.print(f"rm          {name}", emoji_code="", prefix="  ")
    if plan.networks:
        output.print(f"Networks : {', '.join(plan.networks)}", emoji_code="")
    if plan.paths:
        output.print(f"Files    : {format_size(plan.total_size)}", emoji_code="")
        for entry in plan.paths:
            output.print(f"delete      {entry.path}  ({format_size(entry.size)})", emoji_code="", prefix="  ")
    if plan.images:
        output.print(f"Images   : {len(plan.images)}", emoji_code="")
        for image in plan.images:
            output.print(f"rmi         {image}", emoji_code="", prefix="  ")
    elif include_images:
        output.print("Images   : none of fm's own images are present", emoji_code="")
    if plan.trust:
        output.print("Trust    : fm's dev CA is removed from", emoji_code="")
        for entry in plan.trust:
            suffix = "  (needs sudo)" if entry.privileged else ""
            output.print(f"untrust     {entry.store}: {entry.location}{suffix}", emoji_code="", prefix="  ")

    if keep_backups:
        output.print("Kept     : ~/frappe/backups", emoji_code="")
    if not include_images:
        output.print("Kept     : docker images (pass --images to remove fm's own)", emoji_code="")
    # Third-party images are never in the plan; say so, because "uninstall" implies otherwise.
    output.print("Kept     : mariadb, redis, nginx-proxy, mailpit and adminer images", emoji_code="")


def _remove_path(path: Path, failures: list[str]) -> None:
    try:
        if path.is_dir():
            shutil.rmtree(path)
        else:
            path.unlink(missing_ok=True)
    except OSError as e:
        # mariadb's data directory is a bind mount the container created as root, so a plain
        # rmtree hits EACCES. Naming the sudo command beats a stack trace: fm refuses to run as
        # root itself (main.py), so it cannot simply retry with privilege.
        failures.append(f"{path}: {e}. Remove it with: sudo rm -rf {path}")


@example(
    "See everything that would be removed, change nothing",
    "--dry-run",
)
@example(
    "Remove every trace of fm from this host",
    "",
    detail="Prints the full plan, then asks for the word 'uninstall' typed back.",
)
@example(
    "Also remove fm's own docker images",
    "--images",
)
@example(
    "Reset the benches only, keep the shared services and fm's config",
    "--only benches",
)
def uninstall(
    only: Annotated[
        list[Scope] | None,
        typer.Option(
            "--only",
            help="Act on this tier only (repeatable): benches, services, host, trust. Default: all four.",
            show_default=False,
        ),
    ] = None,
    images: Annotated[
        bool,
        typer.Option("--images", help="Also remove fm's own docker images (ghcr.io/rtcamp/frappe-manager-*)."),
    ] = False,
    keep_backups: Annotated[
        bool,
        typer.Option("--keep-backups", help="Leave ~/frappe/backups on disk."),
    ] = False,
    yes: Annotated[
        bool,
        typer.Option("--yes", "-y", help="Uninstall without asking, including the typed confirmation."),
    ] = False,
    dry_run: Annotated[
        bool,
        typer.Option("--dry-run", help="Print the plan and exit without removing anything; never prompts."),
    ] = False,
):
    """
    Remove everything fm put on this host: benches, shared services, its directory, and its dev CA.

    This destroys every bench and every database in fm's own mariadb, with no undo and no backup taken. A schema on a database server fm does not own is never touched. Narrow the blast radius with --only: 'benches' wipes the benches and leaves the shared services and fm's config, which is the closest thing to a fresh start that keeps the host set up.

    Plan-first: every container, path, image and trust store is listed before anything happens, then one confirmation covers it; --dry-run stops after the plan. Public base images (mariadb, redis, nginx-proxy, mailpit, adminer) are never removed, because this host may be using them for something else; --images removes fm's own.

    fm's own package is not uninstalled here: the command to do that is printed at the end, because a running process cannot reliably delete the environment it is executing from.
    """
    output = get_global_output_handler()
    scopes = set(only) if only else set(Scope)

    docker = DockerClient()
    if not docker.server_running():
        # Files and trust stores are still removable, and a host whose docker is already gone is
        # exactly where the leftovers would otherwise be permanent.
        output.warning("Docker is not running: containers, networks and images will be left as they are.")
        docker = None

    with spinner(output, "Reading what fm has on this host"):
        plan = plan_teardown(docker, scopes, keep_backups=keep_backups, include_images=images)

    if plan.is_empty():
        output.print("Nothing of fm's is on this host.", emoji_code="")
        return

    output.warning("This removes the following, permanently:")
    _print_plan(output, plan, keep_backups=keep_backups, include_images=images)

    if dry_run:
        output.print("Nothing was changed.", emoji_code="")
        return

    if not yes:
        # A y/N cannot catch the wrong-terminal accident, and this is wider than `fm delete`,
        # which already demands a typed name for a single bench.
        typed = output.prompt_ask(
            prompt="Type 'uninstall' to confirm (anything else aborts)",
            required_flag="--yes or -y",
        )
        if typed.strip() != "uninstall":
            output.print("Aborted; nothing was removed.", emoji_code=":x:")
            raise typer.Exit(1)

    failures: list[str] = []

    if docker is not None and plan.containers:
        with spinner(output, "Removing containers"):
            for name in plan.containers:
                try:
                    docker.rm(name, force=True, volumes=True, stream=False)
                except Exception as e:
                    failures.append(f"container {name}: {e}")
        output.print(f"Removed {len(plan.containers)} container(s) and their volumes")

    if docker is not None and plan.networks:
        for network in plan.networks:
            result = docker.network_rm(network)
            if not result:
                failures.append(f"network {network}: could not be removed")
        output.print(f"Removed {len(plan.networks)} network(s)")

    for entry in plan.paths:
        _remove_path(entry.path, failures)
    if plan.paths:
        output.print(f"Removed {format_size(plan.total_size)} of files")

    # CLI_DIR itself only goes when nothing was asked to be kept inside it; --only and
    # --keep-backups both leave a populated tree that must survive.
    if CLI_DIR.exists() and scopes == set(Scope) and not keep_backups and not any(CLI_DIR.iterdir()):
        _remove_path(CLI_DIR, failures)

    if docker is not None and plan.images:
        with spinner(output, "Removing images"):
            for image in plan.images:
                try:
                    docker.rmi(image, force=True, stream=False)
                except Exception as e:
                    failures.append(f"image {image}: {e}")
        output.print(f"Removed {len(plan.images)} image(s)")

    if plan.trust:
        removed, trust_failures = TrustStoreManager(output).uninstall()
        failures += trust_failures
        if removed:
            output.print(f"Removed fm's dev CA from {len(removed)} trust store(s)")

    if failures:
        for failure in failures:
            output.display_error(failure)
        output.display_error("Some things could not be removed; they are listed above.")
        raise typer.Exit(1)

    if scopes == set(Scope):
        output.print("fm's state is gone from this host.", emoji_code=":wastebasket:")
        output.print(_package_removal_hint(), emoji_code="", soft_wrap=True, highlight=False)


def _package_removal_hint() -> str:
    """The command that removes the fm package itself, for the installer actually in use."""
    from frappe_manager.utils.helpers import _running_from_uv_tool

    if _running_from_uv_tool():
        return "To remove fm itself:\n  uv tool uninstall frappe-manager"
    return "To remove fm itself:\n  pip uninstall frappe-manager"
