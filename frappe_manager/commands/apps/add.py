"""Add apps to a bench command."""

from typing import Annotated, cast

import typer
from typer_examples import example

from frappe_manager.commands import check_bench_migration_required
from frappe_manager.commands.arguments import BenchSiteAllArgument
from frappe_manager.output_manager import get_global_output_handler, spinner
from frappe_manager.site_manager.bench_config import AppConfig, BenchRuntime, WorkersConfig
from frappe_manager.site_manager.exceptions import BenchNotRunning
from frappe_manager.site_manager.modules.deploy_orchestrator import DeployOrchestrator, DrainUnavailable
from frappe_manager.site_manager.site import Bench
from frappe_manager.utils.callbacks import RESERVED_BENCH_NAME, apps_list_validation_callback

# Rich help panel for the drain flag, matching `fm restart`'s naming for the same concern.
_PANEL_CARE = "Care (what happens to in-flight work)"


@example(
    "Fetch an app's code onto the bench",
    "{benchname} erpnext:version-15",
    detail="Records the app on the bench and installs it into nothing; any site created afterwards picks it up. Install it into an existing site with 'fm apps add {benchname}/SITE ...' or 'fm apps add {benchname}/all ...'.",
    benchname="mybench",
)
@example(
    "Install into one site",
    "{benchname}/SITE erpnext:version-15",
    detail="Fetches the code, installs it into SITE, then runs bench migrate and restarts.",
    benchname="mybench",
)
@example(
    "Install into every site the bench serves",
    "{benchname}/all hrms:version-15",
    detail="A site that fails to install or migrate is reported and the rest still run; the command exits non-zero if any site failed.",
    benchname="mybench",
)
@example(
    "Install without draining in-flight RQ jobs",
    "{benchname}/all erpnext:version-15 --no-drain",
    detail="Interrupted jobs land in the failed-jobs registry (SIGUSR1, force-stop after [workers].kill_timeout).",
    benchname="mybench",
)
def add_apps(
    ctx: typer.Context,
    address: BenchSiteAllArgument = None,
    apps: Annotated[
        list[str],
        typer.Argument(
            metavar="APP:REF...",
            help="Apps to fetch and install (repeatable; appname:ref or org/repo:ref, or org/repo:ref#subdir for a monorepo app). Replaced code is stashed, never deleted.",
            callback=apps_list_validation_callback,
            show_default=False,
        ),
    ] = [],
    drain: Annotated[
        bool,
        typer.Option(
            "--drain/--no-drain",
            help="Suspend RQ workers and wait for in-flight jobs before migrating; --no-drain interrupts them instead.",
            show_default=True,
            rich_help_panel=_PANEL_CARE,
        ),
    ] = True,
):
    """
    Fetch app code onto a bench and install it into its site(s).

    Fetching an app's code and installing it into a site's database are different things. A plain fm apps add BENCH fetches the code and records the app on the bench, so any site created afterwards gets it, and installs it into nothing. fm apps add BENCH/SITE installs and migrates that one site, and fm apps add BENCH/all does every site the bench serves, reporting failures per site and exiting non-zero without stopping at the first.

    An image-runtime bench ships app changes by baking a new image: fm bake BENCH --apps APP:REF, then fm switch.
    """

    services_manager = ctx.obj["services"]
    output = get_global_output_handler()
    check_bench_migration_required(address)

    # The site half of the address. `all` stays the literal reserved word: the body expands it
    # against `bench_config.site_names`, the only place that knows what "all" means right now.
    apps_site = ctx.obj.get("site") if ctx.obj else None

    bench = Bench.get_object(address, services_manager, output_handler=output)

    if bench.bench_config.runtime == BenchRuntime.image:
        output.display_error(
            f"{bench.name} is image runtime; apps ship by baking them in -- "
            f"'fm bake {bench.name} --apps APP:REF', then 'fm switch {bench.name} <image>'.",
        )
        raise typer.Exit(1)

    if not bench.running:
        raise BenchNotRunning(bench_name=bench.name)

    apps_overrides = cast("list[AppConfig]", apps)

    targets: list[str] = []
    if apps_site == RESERVED_BENCH_NAME:
        targets = list(bench.bench_config.site_names)
    elif apps_site:
        targets = [apps_site]

    orchestrator = DeployOrchestrator(bench, output_handler=output)

    def _drain_gate() -> bool:
        """Drain is a GATE: suspend workers and wait for in-flight jobs; on timeout resume the
        workers and abort before any mutation runs. Returns True when the workers really were
        suspended, i.e. when the caller owes them a resume."""
        try:
            drained = orchestrator.drain_workers()
        except DrainUnavailable as e:
            output.warning(f"{e} Continuing without a drain: in-flight jobs may be interrupted.")
            return False
        if drained:
            return True
        orchestrator.resume_workers()
        output.display_error(
            f"Drain timed out after {orchestrator.workers_config.drain_timeout}s: workers still busy. "
            "Nothing was changed. Raise \\[workers].drain_timeout or re-run with --no-drain to "
            "interrupt in-flight jobs."
        )
        raise typer.Exit(1)

    drained = False
    if drain:
        drained = _drain_gate()
    else:
        kill_timeout = (bench.bench_config.workers or WorkersConfig()).kill_timeout
        output.warning(
            f"Installing apps WITHOUT draining: in-flight jobs are interrupted "
            f"(SIGUSR1, force-stop after {kill_timeout}s)"
        )

    try:
        with spinner(output, f"Adding apps to {bench.name}"):
            added, apps_stash = bench.app_manager.graft_apps(apps_overrides, stash=True, use_run=False)
            if apps_stash:
                output.warning(f"Replaced app code moved to {apps_stash} -- review and delete it.")

            failed: list[str] = []
            for site in targets:
                try:
                    for app_name in added:
                        output.change_head(f"Installing {app_name} into {site}")
                        bench.app_manager.install_app_to_site(app_name, site_name=site)
                    output.change_head(f"Running bench migrate on {site}")
                    migrate_cmd = " ".join(bench.app_manager.bench_cli_cmd + ["--site", site, "migrate"])
                    bench.app_manager._container_run(migrate_cmd)
                except Exception as e:
                    # Report and continue, like `fm ssl renew all`: stopping here would leave the
                    # sites already migrated on the new code and the rest on the old, with no
                    # record of which is which.
                    output.warning(f"{site}: {e}")
                    failed.append(site)

            # Bare-BENCH fetches code only, installs it into nothing: nothing new is running, so
            # nothing needs a restart. A restart here used to be unconditional.
            if targets:
                output.change_head("Restarting services to load grafted apps")
                bench.restart_web_containers_services(use_container_restart=False)
                bench.restart_workers_containers_services(use_container_restart=False)
    finally:
        if drained:
            orchestrator.resume_workers()

    if failed:
        output.display_error(f"Apps grafted, but these sites failed: {', '.join(failed)}")
        raise typer.Exit(1)

    if targets:
        output.print(f"Grafted apps applied to {', '.join(targets)}: {', '.join(a.name for a in apps_overrides)}")
    else:
        output.print(
            f"Grafted app code into the bench: {', '.join(a.name for a in apps_overrides)}. "
            f"Install it with 'fm apps add {bench.name}/all ...' or name one site."
        )
