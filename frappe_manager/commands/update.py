from pathlib import Path
from typing import Annotated

import typer
from typer_examples import example

from frappe_manager import EnableDisableOptionsEnum
from frappe_manager.commands import check_bench_migration_required
from frappe_manager.commands.arguments import BenchSiteArgument
from frappe_manager.output_manager import get_global_output_handler, spinner
from frappe_manager.site_manager.bench_config import (
    AppConfig,
    BenchRuntime,
    FMBenchEnvType,
    RestartPolicyEnum,
    WorkersConfig,
    extract_node_version_requirement,
    extract_python_version_requirement,
    parse_node_version_for_runtime,
    parse_python_version_for_runtime,
    requests_immutable_runtime_inputs,
    validate_node_version_compatibility,
    validate_python_version_compatibility,
)
from frappe_manager.site_manager.exceptions import BenchNotRunning
from frappe_manager.site_manager.modules import db_tls
from frappe_manager.site_manager.site import Bench
from frappe_manager.utils.site import host_bench_dir
from frappe_manager.utils.process_lock import bench_lock

# Rich help panels for `fm update --help`, titled by the segment of the `BENCH/SITE` address each
# flag acts on. Same rule as `fm create`: scope is where the value LANDS, not how the help reads.
#
# Rich renders panels in order of first appearance in the signature, so the bench-scoped parameters
# are all declared before the first site-scoped one.
_PANEL_BENCH = "Bench Options"
_PANEL_RUNTIME = "Bench Options: Runtime"
_PANEL_MOUNT = "Bench Options: Workspace (mount runtime only)"
_PANEL_SITE = "Site Options (BENCH alone means its primary site)"


def is_immutable_update_request(
    python_version: str | None,
    node_version: str | None,
    developer_mode: EnableDisableOptionsEnum | None = None,
) -> bool:
    """True when an update requests changes that are immutable in image runtime.

    Thin adapter over ``requests_immutable_runtime_inputs``, which holds the rule beside the
    schema so ``fm create`` enforces the same one. This maps update's tri-state
    ``--developer-mode`` onto the predicate's boolean. Apps no longer reach this command -- they
    moved to ``fm apps add``, which answers the same image-runtime redirect on its own -- so this
    adapter, unlike the shared predicate, carries no ``apps`` parameter.
    """
    return requests_immutable_runtime_inputs(
        python_version=python_version,
        node_version=node_version,
        developer_mode_enable=developer_mode == EnableDisableOptionsEnum.enable,
    )


def _demote_to_mount(bench: Bench, output) -> None:
    """image -> mount: extract an editable workspace from the CURRENTLY DEPLOYED image.

    Not a deploy: code on disk already equals the running code, so there is nothing to migrate
    and no ``DeployOrchestrator`` run -- just a workspace materialize and a container recreate
    on the mount compose shape.
    """
    deploy_state = bench.bench_config.deploy_state
    demotion_image = deploy_state.current_image if deploy_state else None
    if not demotion_image:
        output.display_error("No deployed image recorded; cannot materialize the workspace.")
        raise typer.Exit(1)

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
        bool,
        typer.Option(
            "--recreate-python-env/--no-recreate-python-env",
            help="Recreate the venv when --python changes the interpreter; --no-recreate-python-env installs the new Python and leaves the existing venv in place.",
            show_default=True,
            rich_help_panel=_PANEL_MOUNT,
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
):
    """
    Change a bench's settings.

    Not bench update: app code ships with fm bake then fm switch. Apps are managed with fm apps add, alias domains with fm domain, admin tools with fm tools, APM with fm telemetry. --runtime mount demotes an image bench to an editable workspace, extracted from the currently deployed image; converting the other direction runs through fm switch instead.

    Most options change the whole bench. --db-ca is the one Site Option below, and a plain fm update BENCH applies it to the bench's primary site; name the site with fm update BENCH/SITE when the bench serves more than one.
    """

    services_manager = ctx.obj["services"]

    output = get_global_output_handler()
    check_bench_migration_required(address)

    bench = Bench.get_object(address, services_manager, output_handler=output)

    if bench.bench_config.runtime == BenchRuntime.image and is_immutable_update_request(
        python_version=python_version, node_version=node_version, developer_mode=developer_mode
    ):
        if runtime == BenchRuntime.mount:
            output.display_error(
                "--runtime mount cannot combine with Python/Node/developer-mode changes in the same run: "
                f"demote first with 'fm update {bench.name} --runtime mount', then re-run with the workspace flags.",
            )
        else:
            output.display_error(
                f"{bench.name} is image runtime; code, apps, Python/Node and developer mode are immutable -- "
                "ship changes with 'fm bake' then 'fm switch', install apps with 'fm apps add', or demote to "
                f"an editable workspace first with 'fm update {bench.name} --runtime mount'. "
                "'fm update' on an image bench still changes environment, restart policy and the database CA, "
                "and APM is 'fm telemetry enable'.",
            )
        raise typer.Exit(1)

    if runtime == BenchRuntime.image and bench.bench_config.runtime == BenchRuntime.mount:
        output.display_error(
            "mount -> image conversion runs through the deploy pipeline (it must migrate the site onto the "
            f"baked image) -- run 'fm switch {bench.name} REPO:TAG'.",
        )
        raise typer.Exit(1)

    # Every refusal below runs BEFORE the mutating blocks: a refusal that lands mid-table has
    # already re-rendered compose files and force-recreated containers by the time it fires, and it
    # exits before the terminal save_bench_config(), leaving bench_config.toml and the running
    # containers permanently disagreeing. A refused `fm update` must change nothing.
    database_config = None
    if db_ca is not None:
        database_config = bench.bench_config.get_database_config()
        if database_config is None:
            output.display_error(
                f"{bench.site_name} has no \\[database] entry in bench_config.toml: the bench uses the fm-managed "
                "'mariadb' container, whose TLS material fm owns, so there is no external CA to refresh.",
            )
            raise typer.Exit(1)

    bench_config_save = False

    if not bench.running:
        raise BenchNotRunning(bench_name=bench.name)

    # Refused before any mutation runs, and BOTH requested versions are validated before EITHER is
    # written: a node refusal used to run after python_version had been assigned to the in-memory
    # bench_config and announced as updated, then exited before save_bench_config() -- so the
    # accepted half of the request was reported as done and silently discarded. Inlined here rather
    # than deferred behind a closure: that indirection existed only so a same-invocation runtime
    # demotion could materialize the workspace this reads from before the check ran, and the
    # combination refusal above already keeps --runtime mount from reaching this validation
    # alongside these mount-only flags in the same invocation.
    current_versions: dict = {}
    frappe_python_req: str | None = None
    frappe_node_req: str | None = None
    if python_version or node_version:
        frappe_app_path = host_bench_dir(bench.path) / "apps" / "frappe"
        current_versions = bench.app_manager.get_current_runtime_versions(use_run=True)

        if frappe_app_path.exists():
            if python_version:
                frappe_python_req = extract_python_version_requirement(frappe_app_path)
            if node_version:
                frappe_node_req = extract_node_version_requirement(frappe_app_path)

        if python_version and frappe_python_req and not skip_version_check:
            is_compatible, error_msg = validate_python_version_compatibility(python_version, frappe_python_req)
            if not is_compatible:
                output.change_head("Python version validation failed")
                output.print(f"Python: {current_versions.get('python') or 'not set'} -> {python_version}")
                output.print(f"Frappe requires: {frappe_python_req}")
                output.display_error(f"{error_msg}", emoji_code=":cross_mark:")
                suggested = parse_python_version_for_runtime(frappe_python_req)
                if suggested:
                    output.print(f"Hint: Try --python {suggested}", emoji_code=":light_bulb:")
                output.print("Use --skip-version-check to bypass this validation (not recommended)")
                raise typer.Exit(code=1)

        if node_version and frappe_node_req and not skip_version_check:
            is_compatible, error_msg = validate_node_version_compatibility(node_version, frappe_node_req)
            if not is_compatible:
                output.change_head("Node version validation failed")
                output.print(f"Node: {current_versions.get('node') or 'not set'} -> {node_version}")
                output.print(f"Frappe requires: {frappe_node_req}")
                output.display_error(f"{error_msg}", emoji_code=":cross_mark:")
                suggested = parse_node_version_for_runtime(frappe_node_req)
                if suggested:
                    output.print(f"Hint: Try --node {suggested}", emoji_code=":light_bulb:")
                output.print("Use --skip-version-check to bypass this validation (not recommended)")
                raise typer.Exit(code=1)

    with spinner(output, "Updating bench configuration"):
        if runtime == BenchRuntime.mount:
            if bench.bench_config.runtime == BenchRuntime.mount:
                output.print(f"Bench runtime is already '{BenchRuntime.mount.value}'")
            else:
                _demote_to_mount(bench, output)
        elif runtime == BenchRuntime.image:
            # Reaching here means the bench is already image runtime: a mount bench asking for
            # --runtime image was refused above, before any mutation ran.
            output.print(f"Bench runtime is already '{BenchRuntime.image.value}'")

        if db_ca is not None:
            output.change_head("Refreshing the external database CA")
            # Captured BEFORE the rewrite below: [database.<site>].ca is what put db_ssl_ca into
            # sites/<site>/site_config.json at create time, and nothing rewrites that file on update.
            # Without that key Frappe keeps connecting with TLS off, whatever CA is installed here.
            had_ca = bool(database_config.ca)
            try:
                # install_site_ca performs the first two writes together on purpose: config/tls/<site>/db-ca.pem
                # for the site AND config/tls/ca-bundle.pem for the worker and schedule containers, which take
                # dumps fm never wraps. Refreshing only the per-site file is the trap this flag exists to
                # prevent: the site connects again while the bundle still carries the expired certificate, so
                # dumps and restores stay broken until someone notices. The [database.<site>].ca rewrite below
                # is the third write, keeping the recorded host path equal to what was installed.
                container_ca_path = db_tls.install_site_ca(bench.path, bench.site_name, db_ca)
            except (OSError, ValueError) as error:
                output.display_error(str(error))
                raise typer.Exit(1) from error

            # Absolute, but deliberately not resolved: a rotation is usually driven through a stable symlink
            # (certbot's live/ layout is the idiom), and following it would record the versioned file instead.
            database_config.ca = str(db_ca.absolute())
            output.print(f"Installed CA at {container_ca_path} and rebuilt the bench ca-bundle.pem")
            # No restart notice here: config/ is bind-mounted as a directory into frappe, socketio, schedule and
            # the workers (./workspace on the mount runtime, ./workspace/frappe-bench/config on the image
            # runtime), and the copy rewrites the same file in place, so running containers already see it.
            if had_ca:
                output.print("Running containers read the new CA on their next database connection; no restart needed.")
            else:
                output.warning(
                    f"{bench.site_name} was configured without database TLS: sites/{bench.site_name}/site_config.json "
                    "carries no db_ssl_ca, so Frappe keeps connecting WITHOUT TLS -- this CA only serves the "
                    "dumps fm takes (my.cnf and ca-bundle.pem). Add db_ssl_ca to that site config to turn "
                    "TLS on for the site itself.",
                )
            bench_config_save = True

        if developer_mode:
            if developer_mode == EnableDisableOptionsEnum.enable:
                bench.bench_config.developer_mode = True
                output.change_head("Enabling frappe developer mode")
                bench.set_common_bench_config({"developer_mode": bench.bench_config.developer_mode})
                output.print("Enabled frappe developer mode")
            elif developer_mode == EnableDisableOptionsEnum.disable:
                bench.bench_config.developer_mode = False
                output.change_head("Disabling frappe developer mode")
                bench.set_common_bench_config({"developer_mode": bench.bench_config.developer_mode})
                output.print("Disabled frappe developer mode")

            bench_config_save = True

        if environment:
            output.change_head(f"Switching bench environment to {environment.value}")
            bench.bench_config.environment_type = environment

            compose_inputs = bench.bench_config.export_to_compose_inputs()
            compose_inputs.setdefault("environment", {}).setdefault("frappe", {})
            compose_inputs["environment"]["frappe"]["FRAPPE_ENV"] = environment.value

            bench.generate_compose(compose_inputs)

            output.print("Recreating frappe container to apply environment change..")
            bench.docker_client.compose.up(services=["frappe"], detach=True, force_recreate=True)

            output.print(f"Switched bench environment to {environment.value}")
            bench_config_save = True

        if restart_policy:
            old_policy = bench.bench_config.restart_policy.value
            if restart_policy != bench.bench_config.restart_policy:
                output.change_head(f"Updating restart policy from '{old_policy}' to '{restart_policy.value}'")

                if restart_policy == RestartPolicyEnum.no and bench.bench_config.environment_type == FMBenchEnvType.prod:
                    output.warning("Setting restart policy to 'no' on production bench")
                    output.warning("Containers will not auto-recover from failures or system reboots")

                bench.bench_config.restart_policy = restart_policy
                bench.generate_compose(bench.bench_config.export_to_compose_inputs())

                if bench.workers.compose_file_manager.compose_path.exists():
                    bench.workers.generate_compose()

                if bench.admin_tools.compose_file_manager.compose_path.exists():
                    bench.admin_tools.generate_compose()

                output.print("Restarting containers to apply restart policy..")
                bench.docker_client.compose.up(detach=True, force_recreate=True)

                # The workers and admin-tools containers are separate compose projects with their own
                # DockerClient; recreating the bench project alone leaves them running under the OLD
                # restart policy while the rendered compose files claim the new one.
                if bench.workers.compose_file_manager.compose_path.exists():
                    bench.workers.docker_client.compose.up(services=[], detach=True, force_recreate=True, pull="never")

                if bench.admin_tools.compose_file_manager.compose_path.exists():
                    bench.admin_tools.enable(force_recreate_container=True)

                output.print(f"Updated restart policy to '{restart_policy.value}'")
                bench_config_save = True
            else:
                output.print(f"Restart policy is already set to '{restart_policy.value}'")

        # Handle upload limit update
        if upload_limit:
            output.change_head(f"Updating upload size limit to {upload_limit}")
            bench.update_upload_limit(upload_limit)

        if python_version or node_version:
            if python_version:
                old_python = current_versions.get("python") or "not set"

                bench.bench_config.python_version = python_version
                output.change_head("Updating Python version")
                output.print(f"Python: {old_python} -> {python_version}")
                if frappe_python_req:
                    output.print(f"Frappe requires: {frappe_python_req}")
                    if skip_version_check:
                        new_compatible, _ = validate_python_version_compatibility(python_version, frappe_python_req)
                        if not new_compatible:
                            output.warning(f" Python {python_version} is incompatible with Frappe requirement")
                            suggested = parse_python_version_for_runtime(frappe_python_req)
                            if suggested:
                                output.warning(f" Consider using --python {suggested} instead")

                bench_config_save = True

            if node_version:
                old_node = current_versions.get("node") or "not set"

                bench.bench_config.node_version = node_version
                output.change_head("Updating Node version")
                output.print(f"Node: {old_node} -> {node_version}")
                if frappe_node_req:
                    output.print(f"Frappe requires: {frappe_node_req}")
                    if skip_version_check:
                        new_compatible, _ = validate_node_version_compatibility(node_version, frappe_node_req)
                        if not new_compatible:
                            output.warning(f" Node {node_version} is incompatible with Frappe requirement")
                            suggested = parse_node_version_for_runtime(frappe_node_req)
                            if suggested:
                                output.warning(f" Consider using --node {suggested} instead")

                bench_config_save = True

            # Persisted here, ahead of the venv rebuild below: a crash mid-rebuild must not leave
            # the new Python/Node recorded on disk without the environment to match, or leave an
            # accepted version change unsaved because the rebuild it also triggered failed.
            bench.save_bench_config()
            bench_config_save = False

            output.change_head("Setting up new runtime environment")
            venv_recreated = bench.app_manager.setup_python_and_node_environments(
                use_run=True, recreate_python_env=recreate_python_env
            )
            output.print("Runtime versions updated successfully")

            if venv_recreated:
                apps_txt_path = host_bench_dir(bench.path) / "sites" / "apps.txt"
                if apps_txt_path.exists():
                    installed_apps = [line.strip() for line in apps_txt_path.read_text().splitlines() if line.strip()]
                    apps_list_dicts = [{"app": app_name, "branch": None} for app_name in installed_apps]
                    apps_list = [
                        AppConfig.from_dict(d, github_token=bench.bench_config.github_token) for d in apps_list_dicts
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

            output.change_head("Restarting services to apply new runtime versions")
            output.print("Restarting web services (frappe, socketio)..")
            bench.restart_web_containers_services(use_container_restart=False)
            output.print("Restarting worker services (schedule, workers)..")
            kill_timeout = (bench.bench_config.workers or WorkersConfig()).kill_timeout
            output.warning(
                f"Restarting workers WITHOUT draining: in-flight jobs are interrupted "
                f"(SIGUSR1, force-stop after {kill_timeout}s)"
            )
            bench.restart_workers_containers_services(use_container_restart=False)
            output.print("All services restarted successfully")

    if bench_config_save:
        bench.save_bench_config()
