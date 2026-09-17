from pathlib import Path
from typing import Annotated

import typer
from typer_examples import example

from frappe_manager import EnableDisableOptionsEnum
from frappe_manager.commands import check_bench_migration_required
from frappe_manager.commands.arguments import BenchSiteArgument
from frappe_manager.commands.update_plan import UpdatePlan, plan_update, report_plan
from frappe_manager.output_manager import get_global_output_handler, spinner
from frappe_manager.site_manager.bench_config import (
    AppConfig,
    BenchRuntime,
    FMBenchEnvType,
    RestartPolicyEnum,
)
from frappe_manager.site_manager.modules import db_tls
from frappe_manager.site_manager.modules.deploy_orchestrator import DeployOrchestrator
from frappe_manager.site_manager.modules.worker_drain import drain_gate
from frappe_manager.site_manager.site import Bench
from frappe_manager.utils.process_lock import bench_lock
from frappe_manager.utils.site import host_bench_dir

# Rich help panels for `fm update --help`, titled by the segment of the `BENCH/SITE` address each
# flag acts on. Same rule as `fm create`: scope is where the value LANDS, not how the help reads.
#
# Rich renders panels in order of first appearance in the signature, so the bench-scoped parameters
# are all declared before the first site-scoped one.
_PANEL_BENCH = "Bench Options"
_PANEL_RUNTIME = "Bench Options: Runtime"
_PANEL_MOUNT = "Bench Options: Workspace (mount runtime only)"
_PANEL_SITE = "Site Options (BENCH alone means its primary site)"


def _demote_to_mount(bench: Bench, demotion_image: str, output) -> None:
    """image -> mount: extract an editable workspace from the CURRENTLY DEPLOYED image.

    Not a deploy: code on disk already equals the running code, so there is nothing to migrate
    and no ``DeployOrchestrator`` run -- just a workspace materialize and a container recreate
    on the mount compose shape.

    The image is resolved and refused during planning, so reaching here means a recorded
    deployment exists.
    """

    from frappe_manager.site_manager.modules.transport import fetch_image
    from frappe_manager.site_manager.modules.workspace_seed import (
        materialize_workspace_from_image,
        stash_conflicting_seed_paths,
    )

    output.change_head(f"Materializing editable workspace from {demotion_image}")
    fetch_image(bench.docker_client, demotion_image, output=output)
    frappe_bench_dir = host_bench_dir(bench.path)
    # Leftover code trees from an earlier mount life are STALE vs the deployed image; keeping
    # them would break "code on disk == running code". Stash them aside (never delete) and
    # extract fresh.
    stash = stash_conflicting_seed_paths(frappe_bench_dir, output=output)
    if stash:
        output.warning(
            f"Existing workspace code was stale vs {demotion_image}; moved to {stash} -- review and delete it.",
        )
    extracted = materialize_workspace_from_image(bench.docker_client, demotion_image, frappe_bench_dir, output=output)
    output.print(f"Extracted from image: {', '.join(extracted) if extracted else 'nothing (already present)'}")

    bench.bench_config.runtime = BenchRuntime.mount

    compose_inputs = bench.bench_config.export_to_compose_inputs()
    compose_inputs.setdefault("environment", {}).setdefault("frappe", {})
    compose_inputs["environment"]["frappe"]["FRAPPE_ENV"] = bench.bench_config.environment_type.value
    bench.generate_compose(compose_inputs)
    if bench.workers.compose_file_manager.compose_path.exists():
        bench.workers.generate_compose()

    output.print("Recreating containers on the mount runtime..")
    bench.docker_client.compose.up(detach=True, force_recreate=True, pull="never")
    bench.workers.docker_client.compose.up(services=[], detach=True, pull="never", stream=False)

    output.print(f"Switched runtime to mount (workspace from {demotion_image})")
    # Persisted the moment the demotion completes: the workspace is extracted and the
    # containers already run it, so deferring this write would let a later failure in this
    # command leave bench_config.toml claiming image runtime for a bench now running on mount.
    bench.save_bench_config()


@example(
    "Switch to the production environment",
    "{benchname} -e prod",
    benchname="mybench",
)
@example(
    "Turn on developer mode",
    "{benchname} --developer-mode enable",
    benchname="mybench",
)
@example(
    "Bump the Python version",
    "{benchname} --python 3.11",
    benchname="mybench",
)
@example(
    "Raise the upload size limit",
    "{benchname} --upload-limit 500M",
    benchname="mybench",
)
@example(
    "Demote an image bench to an editable workspace",
    "{benchname} --runtime mount",
    detail="Extracts the workspace from the currently deployed image; converting back to image runtime runs through fm switch instead.",
    benchname="mybench",
)
@bench_lock(param="address", operation="update")
def update(
    ctx: typer.Context,
    address: BenchSiteArgument = None,
    environment: Annotated[
        FMBenchEnvType | None,
        typer.Option(
            "--environment",
            "-e",
            help="Switch the bench between dev and prod serving (FRAPPE_ENV), recreating the frappe container. Admin tools and developer mode are left as they are; use 'fm tools enable'/'fm tools disable' or --developer-mode to change those.",
            show_default=False,
            rich_help_panel=_PANEL_BENCH,
        ),
    ] = None,
    runtime: Annotated[
        BenchRuntime | None,
        typer.Option(
            "--runtime",
            help="Convert the bench's runtime: 'mount' demotes an image bench to an editable workspace extracted from the currently deployed image (no migrate -- code on disk already equals what is running). 'image' is a no-op confirmation on an already-image bench; converting mount -> image runs through 'fm switch' instead, since that migrates the site onto a baked image.",
            show_default=False,
            rich_help_panel=_PANEL_RUNTIME,
        ),
    ] = None,
    developer_mode: Annotated[
        EnableDisableOptionsEnum | None,
        typer.Option(
            help="Toggle frappe developer mode, so DocType edits write to app files.",
            show_default=False,
            rich_help_panel=_PANEL_MOUNT,
        ),
    ] = None,
    upload_limit: Annotated[
        str | None,
        typer.Option(
            "--upload-limit",
            help="Set the maximum file upload size, e.g. 100M or 1G.",
            show_default=False,
            rich_help_panel=_PANEL_BENCH,
        ),
    ] = None,
    restart_policy: Annotated[
        RestartPolicyEnum | None,
        typer.Option(
            "--restart-policy",
            help="Update Docker restart policy for all bench services.",
            show_default=False,
            rich_help_panel=_PANEL_BENCH,
        ),
    ] = None,
    python_version: Annotated[
        str | None,
        typer.Option(
            "--python",
            help="Update the Python version (e.g. '3.11', '>=3.11,<3.14'); recreates the venv and reinstalls apps.",
            show_default=False,
            rich_help_panel=_PANEL_MOUNT,
        ),
    ] = None,
    node_version: Annotated[
        str | None,
        typer.Option(
            "--node",
            help="Update the Node version (e.g. '20', '>=18') and set it as the bench default.",
            show_default=False,
            rich_help_panel=_PANEL_MOUNT,
        ),
    ] = None,
    skip_version_check: Annotated[
        bool,
        typer.Option(
            "--skip-version-check",
            help="Accept a Python/Node version that does not satisfy frappe's requirement.",
            show_default=False,
            rich_help_panel=_PANEL_MOUNT,
        ),
    ] = False,
    recreate_python_env: Annotated[
        bool | None,
        typer.Option(
            "--recreate-python-env/--no-recreate-python-env",
            help="Rebuild the venv. Alongside --python it is the default (the new interpreter needs a fresh venv); --no-recreate-python-env installs the new Python and leaves the existing venv in place. On its own, with no version change, it rebuilds the venv at the recorded Python/Node and reinstalls the apps.",
            show_default=False,
            rich_help_panel=_PANEL_MOUNT,
        ),
    ] = None,
    drain: Annotated[
        bool,
        typer.Option(
            "--drain/--no-drain",
            help="Suspend RQ workers and wait for in-flight jobs before restarting or recreating them, and abort the update if they outlast \\[workers].drain_timeout; --no-drain interrupts them instead.",
            show_default=True,
            rich_help_panel=_PANEL_BENCH,
        ),
    ] = True,
    db_ca: Annotated[
        Path | None,
        typer.Option(
            "--db-ca",
            help="Reinstall the external database CA after a rotation: the site PEM, the bench ca-bundle.pem the dumps use, and the recorded path are refreshed together.",
            show_default=False,
            # Same reason as create's --db-ca: click's implicit readable=True would fail with its
            # own wording before db_tls.install_site_ca's PermissionError check ever gets a turn.
            readable=False,
            rich_help_panel=_PANEL_SITE,
        ),
    ] = None,
    dry_run: Annotated[
        bool,
        typer.Option(
            "--dry-run",
            help="Print what would change and exit without touching the bench.",
            show_default=False,
        ),
    ] = False,
):
    """
    Change a bench's settings.

    Not bench update: app code ships with fm bake then fm switch. Apps are managed with fm apps add, alias domains with fm domain, admin tools with fm tools, APM with fm telemetry. --runtime mount demotes an image bench to an editable workspace, extracted from the currently deployed image; converting the other direction runs through fm switch instead.

    Most options change the whole bench. --db-ca is the one Site Option below, and a plain fm update BENCH applies it to the bench's primary site; name the site with fm update BENCH/SITE when the bench serves more than one.

    The whole update is decided before any of it is applied, so an invalid flag changes nothing and a value that already matches is reported instead of reapplied. --dry-run prints that plan and exits without touching the bench.
    """
    services_manager = ctx.obj["services"]

    output = get_global_output_handler()
    check_bench_migration_required(address)

    bench = Bench.get_object(address, services_manager, output_handler=output)

    # Decide everything first. Every refusal lives in here and nothing in it touches the bench, so
    # an invalid invocation cannot leave half an update applied -- the defect this split fixes.
    plan = plan_update(
        bench,
        output,
        runtime=runtime,
        environment=environment,
        developer_mode=developer_mode,
        upload_limit=upload_limit,
        restart_policy=restart_policy,
        python_version=python_version,
        node_version=node_version,
        skip_version_check=skip_version_check,
        recreate_python_env=recreate_python_env,
        db_ca=db_ca,
    )

    orchestrator = DeployOrchestrator(bench, output_handler=output)
    report_plan(
        output,
        plan,
        dry_run=dry_run,
        drain=drain,
        drain_timeout=orchestrator.workers_config.drain_timeout,
    )

    if dry_run or plan.is_empty:
        return

    with spinner(output, "Updating bench configuration"):
        apply_update(bench, plan, output, orchestrator=orchestrator, drain=drain)


def apply_update(bench: Bench, plan: UpdatePlan, output, *, orchestrator=None, drain: bool = False) -> None:
    """Carry out a built plan. No refusals live here: every check ran during planning.

    Order is load-bearing, twice over.

    The worker drain is a GATE and runs FIRST, before any write: a drain that times out aborts an
    update that has changed literally nothing, which is the same promise `fm apps add` makes
    ("Nothing was changed") and which only became true here once deciding was split from doing.

    Then the config is written and SAVED before any container is touched, so a failure in the
    container half leaves bench_config.toml ahead of the containers rather than behind them --
    fm's own regeneration paths (`republish_site_map`) push config onto containers, so "config
    ahead" self-heals while "containers ahead" silently flips serving later. Compose is rendered
    once, and the accumulated container set is acted on once.
    """
    drained = False
    if plan.touches_workers and orchestrator is not None:
        if drain:
            drained = drain_gate(orchestrator, output, action="update")
        else:
            output.warning(
                f"Restarting workers WITHOUT draining: in-flight jobs are interrupted "
                f"(SIGUSR1, force-stop after {plan.kill_timeout}s)",
            )

    try:
        _apply_plan(bench, plan, output)
    finally:
        # The RQ suspend flag lives in redis, so it outlives this command: workers left suspended
        # by an aborted apply would stay idle until something resumed them.
        if drained and orchestrator is not None:
            orchestrator.resume_workers()


def _apply_plan(bench: Bench, plan: UpdatePlan, output) -> None:
    """The writes themselves, in the order the docstring above fixes."""
    if plan.demote_to_mount:
        assert plan.demotion_image is not None
        _demote_to_mount(bench, plan.demotion_image, output)

    if plan.db_ca is not None:
        _install_db_ca(bench, plan, output)

    if plan.developer_mode is not None:
        bench.bench_config.developer_mode = plan.developer_mode
        output.change_head(f"{'Enabling' if plan.developer_mode else 'Disabling'} frappe developer mode")
        bench.set_common_bench_config({"developer_mode": plan.developer_mode})

    if plan.environment is not None:
        bench.bench_config.environment_type = plan.environment

    if plan.restart_policy is not None:
        bench.bench_config.restart_policy = plan.restart_policy

    if plan.python_version is not None:
        bench.bench_config.python_version = plan.python_version

    if plan.node_version is not None:
        bench.bench_config.node_version = plan.node_version

    # Saved only when something above actually assigned a field. The two paths that own their own
    # write are deliberately excluded: `_demote_to_mount` persists the moment the workspace exists
    # (a later failure must not leave the file claiming image runtime for a bench now on mount),
    # and `update_upload_limit` saves before writing the confs it renders FROM that saved config.
    # Saving unconditionally here double-wrote in both cases.
    if plan.writes_bench_config:
        bench.save_bench_config()

    # `update_upload_limit` owns its own save plus the three writes that actually enforce the limit
    # (proxy vhost.d, the bench custom conf, site_config), and reloads nginx once if anything
    # changed.
    if plan.upload_limit is not None:
        output.change_head(f"Updating upload size limit to {plan.upload_limit}")
        bench.update_upload_limit(plan.upload_limit)

    try:
        _apply_container_work(bench, plan, output)
    except Exception:
        # The config is already saved, deliberately (see this function's docstring). Say so:
        # without this the user is left with a failed command and no idea that the recorded
        # settings are the NEW ones, and that a retry is safe and cheap.
        if plan.writes_bench_config or plan.upload_limit is not None:
            output.warning(
                f"{bench.name}'s settings are already recorded in bench_config.toml; only the container "
                f"half failed. Re-run this command, or 'fm restart {bench.name}', to bring the containers "
                "in line -- fm renders containers FROM the recorded config, so nothing is lost.",
            )
        raise


def _apply_container_work(bench: Bench, plan: UpdatePlan, output) -> None:
    """Render compose and act on the accumulated container set, once each."""
    if plan.regenerate_compose:
        compose_inputs = bench.bench_config.export_to_compose_inputs()
        # FRAPPE_ENV reaches no compose input of its own (`export_to_compose_inputs` omits it), so
        # every caller that renders this file has to re-inject it or the running environment
        # silently reverts to whatever the file already said.
        compose_inputs.setdefault("environment", {}).setdefault("frappe", {})
        compose_inputs["environment"]["frappe"]["FRAPPE_ENV"] = bench.bench_config.environment_type.value
        bench.generate_compose(compose_inputs)

        if plan.recreate_everything:
            if bench.workers.compose_file_manager.compose_path.exists():
                bench.workers.generate_compose()
            if bench.admin_tools.compose_file_manager.compose_path.exists():
                bench.admin_tools.generate_compose()

    if plan.recreate_everything:
        output.change_head("Recreating containers")
        bench.docker_client.compose.up(detach=True, force_recreate=True)
        # The workers and admin-tools containers are separate compose projects with their own
        # DockerClient; recreating the bench project alone leaves them running under the OLD
        # restart policy while the rendered compose files claim the new one.
        if bench.workers.compose_file_manager.compose_path.exists():
            bench.workers.docker_client.compose.up(services=[], detach=True, force_recreate=True, pull="never")
        if bench.admin_tools.compose_file_manager.compose_path.exists():
            bench.admin_tools.enable(force_recreate_container=True)
    elif plan.recreate_services:
        # One call for the whole accumulated set. Three separate arms used to each force-recreate
        # on their own, so a multi-flag run destroyed and recreated `frappe` up to three times.
        services = sorted(plan.recreate_services)
        output.change_head(f"Recreating {', '.join(services)}")
        bench.docker_client.compose.up(services=services, detach=True, force_recreate=True)

    if plan.run_runtime_setup:
        _setup_runtime(bench, plan, output)


def _install_db_ca(bench: Bench, plan: UpdatePlan, output) -> None:
    """Reinstall the external database CA: site PEM, bench ca-bundle, and the recorded path."""
    assert plan.db_ca is not None
    output.change_head("Refreshing the external database CA")
    try:
        # install_site_ca performs the first two writes together on purpose: config/tls/<site>/db-ca.pem
        # for the site AND config/tls/ca-bundle.pem for the worker and schedule containers, which take
        # dumps fm never wraps. Refreshing only the per-site file is the trap this flag exists to
        # prevent: the site connects again while the bundle still carries the expired certificate, so
        # dumps and restores stay broken until someone notices.
        container_ca_path = db_tls.install_site_ca(bench.path, bench.site_name, plan.db_ca)
    except (OSError, ValueError) as error:
        output.display_error(str(error))
        raise typer.Exit(1) from error

    database_config = bench.bench_config.get_database_config()
    assert database_config is not None
    # Absolute, but deliberately not resolved: a rotation is usually driven through a stable symlink
    # (certbot's live/ layout is the idiom), and following it would record the versioned file instead.
    database_config.ca = str(plan.db_ca.absolute())
    output.print(f"Installed CA at {container_ca_path} and rebuilt the bench ca-bundle.pem")
    # No restart notice: config/ is bind-mounted as a directory into frappe, socketio, schedule and
    # the workers, and the copy rewrites the same file in place, so running containers already see it.
    if plan.db_ca_had_previous:
        output.print("Running containers read the new CA on their next database connection; no restart needed.")
    else:
        output.warning(
            f"{bench.site_name} was configured without database TLS: sites/{bench.site_name}/site_config.json "
            "carries no db_ssl_ca, so Frappe keeps connecting WITHOUT TLS -- this CA only serves the "
            "dumps fm takes (my.cnf and ca-bundle.pem). Add db_ssl_ca to that site config to turn "
            "TLS on for the site itself.",
        )


def _setup_runtime(bench: Bench, plan: UpdatePlan, output) -> None:
    """Rebuild the Python/Node environment and bring the processes back on it.

    Only reached when a version actually CHANGED: an identical `--python`/`--node` request is a
    no-op in the plan, because this path costs ~2 minutes and interrupts in-flight jobs to arrive
    where the bench already was.
    """
    output.change_head("Setting up new runtime environment")
    venv_recreated = bench.app_manager.setup_python_and_node_environments(
        use_run=True, recreate_python_env=plan.recreate_python_env
    )
    output.print("Runtime versions updated successfully")

    if venv_recreated:
        apps_txt_path = host_bench_dir(bench.path) / "sites" / "apps.txt"
        if apps_txt_path.exists():
            installed_apps = [line.strip() for line in apps_txt_path.read_text().splitlines() if line.strip()]
            apps_list = [
                AppConfig.from_dict({"app": name, "branch": None}, github_token=bench.bench_config.github_token)
                for name in installed_apps
            ]
            output.change_head("Reinstalling apps into new virtual environment")
            output.print(f"Found {len(apps_list)} installed apps: {', '.join([a.name for a in apps_list])}")
            bench.app_manager.install_apps(
                apps_list=apps_list,
                github_token=bench.bench_config.github_token,
                use_uv=bench.bench_config.use_uv,
                skip_clone=True,
                use_run=True,
            )
            output.print("All apps reinstalled successfully")
        else:
            output.warning("No apps.txt found, skipping app reinstallation")

    if plan.restart_web:
        output.change_head("Restarting web services (frappe, socketio)")
        bench.restart_web_containers_services(use_container_restart=False)

    if plan.restart_workers:
        output.change_head("Restarting worker services (schedule, workers)")
        bench.restart_workers_containers_services(use_container_restart=False)
