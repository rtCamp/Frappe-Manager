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


def _print_plan(output, plan: TeardownPlan, *, keep_backups: bool, keep_images: bool) -> None:
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
        output.print(f"Networks : {len(plan.networks)}", emoji_code="")
        for name in plan.networks:
            output.print(f"network rm  {name}", emoji_code="", prefix="  ")
    if plan.volumes:
        output.print(f"Volumes  : {len(plan.volumes)}  (data inside them is gone for good)", emoji_code="")
        for name in plan.volumes:
            output.print(f"volume rm   {name}", emoji_code="", prefix="  ")
    if plan.paths:
        output.print(f"Files    : {format_size(plan.total_size)}", emoji_code="")
        for entry in plan.paths:
            output.print(f"delete      {entry.path}  ({format_size(entry.size)})", emoji_code="", prefix="  ")
    if plan.images:
        output.print(f"Images   : {len(plan.images)}", emoji_code="")
        for image in plan.images:
            output.print(f"rmi         {image}", emoji_code="", prefix="  ")
    elif not keep_images:
        output.print("Images   : none of fm's images are present", emoji_code="")
    if plan.trust:
        output.print("Trust    : fm's dev CA is removed from", emoji_code="")
        for entry in plan.trust:
            suffix = "  (needs sudo)" if entry.privileged else ""
            output.print(f"untrust     {entry.store}: {entry.location}{suffix}", emoji_code="", prefix="  ")

    if keep_backups:
        # CLI_DIR, not a literal: FRAPPE_MANAGER_HOME moves fm's home, and every other line in
        # this plan prints a resolved path. A hardcoded ~/frappe would name a directory that
        # does not exist on such a host, in the one line that claims something SURVIVES.
        output.print(f"Kept     : {CLI_DIR / 'backups'}", emoji_code="")
    if keep_images:
        output.print("Kept     : fm's docker images (--keep-images)", emoji_code="")
    # An image another project's container runs is not fm's to delete, and `rmi -f` would untag
    # it out from under a stopped container. Name the holder: that is what makes it checkable.
    for image, container in plan.images_in_use:
        output.print(f"Kept     : {image}  (container '{container}' still uses it)", emoji_code="")


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
    detail="The plan names every bench, container, network, volume, path, image and trust store, with sizes. Read it before running the real thing.",
)
@example(
    "Remove every trace of fm from this host",
    "",
    detail="Prints the same plan, then asks for the word 'uninstall' typed back.",
)
@example(
    "Start over with a clean slate, keep the host set up",
    "--only benches",
    detail="Destroys every bench and its databases; the shared services, fm's config and its images stay, so the next 'fm create' does not re-pull or re-provision anything.",
)
@example(
    "Remove fm but keep the images for a reinstall",
    "--keep-images --keep-backups",
    detail="Keeps fm's images and the stock mariadb/postgres/redis/proxy set, so reinstalling costs no pull, and the backups directory survives to be restored into the new install.",
)
@example(
    "Untrust the dev CA on a host where ~/frappe was already deleted by hand",
    "--only trust",
    detail="A root CA outlives the directory its key lived in: deleting ~/frappe leaves your browsers and keychain trusting a CA nobody controls any more. Needs neither docker nor a readable fm config.",
)
def uninstall(
    only: Annotated[
        list[Scope] | None,
        typer.Option(
            "--only",
            help="Act on these tiers only (repeatable): benches, services, host, trust. Default: all four.",
            show_default=False,
        ),
    ] = None,
    keep_images: Annotated[
        bool,
        typer.Option("--keep-images", help="Leave every docker image fm pulled or built on disk (fm's own and the stock mariadb/postgres/redis/nginx-proxy/mailpit/adminer set)."),
    ] = False,
    keep_backups: Annotated[
        bool,
        typer.Option("--keep-backups", help="Leave the backups directory in fm's home (~/frappe/backups by default) on disk."),
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
    Remove everything fm put on this host: benches, shared services, its own files, and its dev CA.

    This destroys every bench and every database in fm's own mariadb and postgres, with no undo and no backup taken. A schema on a database server fm does not own is never touched, and neither is any data outside the four tiers below.

    What each --only tier owns, by where the state actually lives (paths below assume fm's default home; FRAPPE_MANAGER_HOME moves all of it, and the plan always prints resolved paths):
    - benches   ~/frappe/sites/<bench>, its containers, network and volumes
    - services  ~/frappe/services, the shared db/proxy containers and volumes
    - host      the rest of ~/frappe, ~/.cache/fm, and every image fm pulled or built
    - trust     fm's dev root CA, in the OS and browser trust stores

    So 'host' is fm's own files (fm_config.toml, logs, locks, backups) plus the cache directory outside ~/frappe that a hand-rolled cleanup always misses, and its images, which belong to the machine rather than to any one bench. And 'trust' is the tier that outlives 'rm -rf ~/frappe': a root CA whose private key sat in a deleted directory stays trusted by your browser forever. That tier is also why this command needs neither docker nor a config fm can still parse.

    Plan-first: every object in scope is listed with its size before anything happens, then one typed confirmation covers all of it, and --dry-run stops after the plan. Everything fm made is included by default, its images with it; --keep-images and --keep-backups opt back out. The image set is read from fm's own compose templates and from the containers being removed, not guessed from a name, so mariadb, redis, nginx-proxy, mailpit, adminer and postgres go too -- except any image some other container on this host still runs, which is kept and named in the plan.

    fm's own package is not uninstalled here: the command to do that is printed at the end, because a running process cannot reliably delete the environment it is executing from.
    """
    output = get_global_output_handler()
    scopes = set(only) if only else set(Scope)

    docker = DockerClient()
    if not docker.server_running():
        # Files and trust stores are still removable, and a host whose docker is already gone is
        # exactly where the leftovers would otherwise be permanent.
        output.warning("Docker is not running: containers, networks, volumes and images will be left as they are.")
        docker = None

    with spinner(output, "Reading what fm has on this host"):
        plan = plan_teardown(docker, scopes, keep_backups=keep_backups, include_images=not keep_images)

    if plan.is_empty():
        output.print("Nothing of fm's is on this host.", emoji_code="")
        return

    output.warning("This removes the following, permanently:")
    _print_plan(output, plan, keep_backups=keep_backups, keep_images=keep_images)

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
                output.change_head(f"Removing container {name}")
                try:
                    docker.rm(name, force=True, volumes=True, stream=False)
                except Exception as e:
                    failures.append(f"container {name}: {e}")
        output.print(f"Removed {len(plan.containers)} container(s)")

    # After the containers, never before: docker refuses to remove a volume or a network that is
    # still referenced, and `docker rm -v` above only took the anonymous ones.
    if docker is not None and plan.volumes:
        with spinner(output, "Removing volumes"):
            for name in plan.volumes:
                output.change_head(f"Removing volume {name}")
                if not docker.volume_rm(name):
                    failures.append(f"volume {name}: could not be removed")
        output.print(f"Removed {len(plan.volumes)} volume(s)")

    if docker is not None and plan.networks:
        with spinner(output, "Removing networks"):
            for network in plan.networks:
                output.change_head(f"Removing network {network}")
                if not docker.network_rm(network):
                    failures.append(f"network {network}: could not be removed")
        output.print(f"Removed {len(plan.networks)} network(s)")

    if plan.paths:
        with spinner(output, "Removing files"):
            for entry in plan.paths:
                output.change_head(f"Removing {entry.path} ({format_size(entry.size)})")
                _remove_path(entry.path, failures)
        output.print(f"Removed {format_size(plan.total_size)} of files")

    # CLI_DIR itself only goes when nothing was asked to be kept inside it; --only and
    # --keep-backups both leave a populated tree that must survive.
    if CLI_DIR.exists() and scopes == set(Scope) and not keep_backups and not any(CLI_DIR.iterdir()):
        _remove_path(CLI_DIR, failures)

    if docker is not None and plan.images:
        with spinner(output, "Removing images"):
            for image in plan.images:
                output.change_head(f"Removing image {image}")
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
