from pathlib import Path
from typing import Annotated

import typer
from typer_examples import example

from frappe_manager import CLI_BENCHES_DIRECTORY, EnableDisableOptionsEnum
from frappe_manager.commands import check_bench_migration_required
from frappe_manager.commands.arguments import BenchSiteAllArgument
from frappe_manager.metadata_manager import FMConfigManager
from frappe_manager.output_manager import get_global_output_handler, spinner
from frappe_manager.site_manager.bench_config import (
    AppConfig,
    BenchRuntime,
    FMBenchEnvType,
    MonitoringConfig,
    NewRelicConfig,
    RestartPolicyEnum,
    extract_node_version_requirement,
    extract_python_version_requirement,
    parse_node_version_for_runtime,
    parse_python_version_for_runtime,
    requests_immutable_runtime_inputs,
    validate_node_version_compatibility,
    validate_python_version_compatibility,
)
from frappe_manager.site_manager.domain_conflict import DomainConflictError, validate_domains_unique
from frappe_manager.site_manager.exceptions import BenchNotRunning
from frappe_manager.site_manager.modules import db_tls
from frappe_manager.site_manager.site import Bench
from frappe_manager.utils.callbacks import (
    RESERVED_BENCH_NAME,
    alias_domains_validation_callback,
    apps_list_validation_callback,
)

# Rich help panels for `fm update --help`, titled by the segment of the `BENCH/SITE` address each
# flag acts on. Same rule as `fm create`: scope is where the value LANDS, not how the help reads.
# `--mailpit-as-default-mail-server` said "the site's outgoing mail" while writing
# `common_site_config`, which every site of the bench shares.
#
# Rich renders panels in order of first appearance in the signature, so the bench-scoped parameters
# are all declared before the first site-scoped one.
_PANEL_BENCH = "Bench Options"
_PANEL_RUNTIME = "Bench Options: Runtime"
_PANEL_MOUNT = "Bench Options: Workspace (mount runtime only)"
_PANEL_MONITORING = "Bench Options: Monitoring"
_PANEL_SITE = "Site Options (BENCH alone means its primary site)"


def is_immutable_update_request(
    python_version: str | None,
    node_version: str | None,
    developer_mode: EnableDisableOptionsEnum | None = None,
    apps: list | None = None,
) -> bool:
    """True when an update requests changes that are immutable in image runtime.

    Thin adapter over ``requests_immutable_runtime_inputs``, which holds the rule beside the
    schema so ``fm create`` enforces the same one. This maps update's tri-state
    ``--developer-mode`` onto the predicate's boolean.
    """
    return requests_immutable_runtime_inputs(
        python_version=python_version,
        node_version=node_version,
        apps=apps,
        developer_mode_enable=developer_mode == EnableDisableOptionsEnum.enable,
    )


@example(
    "Switch to the production environment",
    "{benchname} -e prod",
    benchname="mybench",
)
@example(
    "Enable the admin tools",
    "{benchname} --admin-tools enable",
    benchname="mybench",
)
@example(
    "Turn on developer mode",
    "{benchname} --developer-mode enable",
    benchname="mybench",
)
@example(
    "Add an alias domain",
    "{benchname} --add-alias www.example.com",
    detail="No certificate is issued for the new domain; run fm ssl add afterwards.",
    benchname="mybench",
)
@example(
    "Bump the Python version",
    "{benchname} --python 3.11",
    benchname="mybench",
)
def update(
    ctx: typer.Context,
    address: BenchSiteAllArgument = None,
    admin_tools: Annotated[
        EnableDisableOptionsEnum | None,
        typer.Option(
            "--admin-tools",
            help="Enable/disable admin tools (Adminer at /adminer, Mailpit at /mailpit). BENCH starts or stops the one container pair the bench has; BENCH/SITE only adds or removes the routes from that site's hostnames, leaving the tools running for the bench's other sites.",
            show_default=False,
            rich_help_panel=_PANEL_BENCH,
        ),
    ] = None,
    environment: Annotated[
        FMBenchEnvType | None,
        typer.Option(
            "--environment",
            "-e",
            help="Switch the bench between dev and prod serving (FRAPPE_ENV), recreating the frappe container. Admin tools and developer mode are left as they are; use --admin-tools or --developer-mode to change those.",
            show_default=False,
            rich_help_panel=_PANEL_BENCH,
        ),
    ] = None,
    runtime: Annotated[
        BenchRuntime | None,
        typer.Option(
            "--runtime",
            help="Convert the bench runtime. 'mount' extracts an editable workspace from the currently deployed image, stashing anything stale it finds; converting back is a deploy, so use fm switch.",
            show_default=False,
            rich_help_panel=_PANEL_RUNTIME,
        ),
    ] = None,
    apps: Annotated[
        list[str],
        typer.Option(
            "--apps",
            "-a",
            help="Replace or add an app on the running bench (repeatable; appname:ref or org/repo:ref). Replaced code is stashed, never deleted; assets rebuild and the site migrates.",
            callback=apps_list_validation_callback,
            show_default=False,
            rich_help_panel=_PANEL_MOUNT,
        ),
    ] = [],
    developer_mode: Annotated[
        EnableDisableOptionsEnum | None,
        typer.Option(
            help="Toggle frappe developer mode, so DocType edits write to app files.",
            show_default=False,
            rich_help_panel=_PANEL_MOUNT,
        ),
    ] = None,
    mailpit_as_default_mail_server: Annotated[
        bool,
        typer.Option(
            "--mailpit-as-default-mail-server",
            help="Route outgoing mail to Mailpit for every site the bench holds. Applies when enabling admin tools.",
            show_default=False,
            rich_help_panel=_PANEL_BENCH,
        ),
    ] = False,
    upload_limit: Annotated[
        str | None,
        typer.Option(
            "--upload-limit",
            help="Set the maximum file upload size, e.g. 100M or 1G.",
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
    restart: Annotated[
        RestartPolicyEnum | None,
        typer.Option(
            "--restart",
            help="Update Docker restart policy for all bench services.",
            show_default=False,
            rich_help_panel=_PANEL_BENCH,
        ),
    ] = None,
    allow_domain_conflicts: Annotated[
        bool,
        typer.Option(
            "--allow-domain-conflicts",
            help="Add an alias domain even when another bench already serves it.",
            show_default=False,
        ),
    ] = False,
    newrelic: Annotated[
        bool | None,
        typer.Option(
            "--newrelic/--no-newrelic",
            help="Enable or disable NewRelic APM monitoring for the web process.",
            show_default=False,
            rich_help_panel=_PANEL_MONITORING,
        ),
    ] = None,
    newrelic_license_key: Annotated[
        str | None,
        typer.Option(
            "--newrelic-license-key",
            help="NewRelic ingest license key. Required the first time you enable NewRelic.",
            show_default=False,
            rich_help_panel=_PANEL_MONITORING,
        ),
    ] = None,
    add_alias: Annotated[
        str | None,
        typer.Option(
            "--add-alias",
            help="Add alias domains (comma-separated, e.g. www.example.com,api.example.com).",
            callback=alias_domains_validation_callback,
            show_default=False,
            rich_help_panel=_PANEL_SITE,
        ),
    ] = None,
    remove_alias: Annotated[
        str | None,
        typer.Option(
            "--remove-alias",
            help="Remove alias domains (comma-separated, e.g. shop.example.com).",
            callback=alias_domains_validation_callback,
            show_default=False,
            rich_help_panel=_PANEL_SITE,
        ),
    ] = None,
    db_ca: Annotated[
        Path | None,
        typer.Option(
            "--db-ca",
            help="Reinstall the external database CA after a rotation: the site PEM, the bench ca-bundle.pem the dumps use, and the recorded path are refreshed together.",
            show_default=False,
            rich_help_panel=_PANEL_SITE,
        ),
    ] = None,
):
    """
    Change a bench's settings and runtime.

    Not bench update: app code ships with fm bake then fm switch. The bench must be running, and the mount-only options need an editable workspace, so demote an image bench with --runtime mount first.

    Most options change the whole bench. The few that describe one site are grouped as Site Options in the help below, and a plain fm update BENCH applies those to the bench's primary site; name the site with fm update BENCH/SITE when the bench serves more than one.

    --apps is the exception, because fetching an app's code and installing it into a site's database are different things. A plain fm update BENCH fetches the code and records the app on the bench, so any site created afterwards gets it, and installs it into nothing. fm update BENCH/SITE installs and migrates that one site, and fm update BENCH/all does every site the bench serves, reporting failures per site and exiting non-zero without stopping at the first.
    """

    services_manager = ctx.obj["services"]
    fm_config: FMConfigManager = ctx.obj["fm_config_manager"]

    output = get_global_output_handler()
    check_bench_migration_required(address)

    # The site half of the address. `all` stays the literal reserved word: only the `--apps` block
    # can fan out, so it is the one that expands it against `bench_config.site_names`.
    apps_site = ctx.obj.get("site") if ctx.obj else None

    # An alias, a database CA and a tool route belong to ONE site: there is no sensible way to
    # attach the same alternate hostname to every site, a per-site CA path is per-site by
    # construction, and routing the tools from no site at all while their containers run is what
    # `--admin-tools disable` is for. Refused rather than silently applied to the primary, which is
    # what an unguarded `all` would do.
    if apps_site == RESERVED_BENCH_NAME:
        fanned = [
            name
            for name, given in (
                ("--add-alias", add_alias),
                ("--remove-alias", remove_alias),
                ("--db-ca", db_ca),
            )
            if given
        ]
        if fanned:
            output.display_error(
                f"{', '.join(fanned)} names one site, so it cannot take 'all'. Name the site as "
                "BENCH/SITE, or drop the site part to use the bench's primary."
            )
            raise typer.Exit(1)

        if admin_tools:
            # Not the message above: dropping the site part from `--admin-tools` addresses the
            # BENCH, which is already what "every site" means here -- it starts or stops the one
            # container pair they all reach.
            output.display_error(
                "--admin-tools cannot take 'all'. Name one site as BENCH/SITE to change just its "
                "routes, or drop the site part to start or stop the tools for the whole bench."
            )
            raise typer.Exit(1)

    bench = Bench.get_object(address, services_manager, output_handler=output)

    demoting_to_mount = runtime == BenchRuntime.mount
    if (
        bench.bench_config.runtime == BenchRuntime.image
        and not demoting_to_mount  # --runtime mount in the same command: demotion runs FIRST, then these apply
        and is_immutable_update_request(
            python_version=python_version, node_version=node_version, developer_mode=developer_mode, apps=apps
        )
    ):
        output.display_error(
            f"{bench.name} is image runtime; code, apps, Python/Node and developer mode are immutable -- "
            "ship changes with 'fm bake' then 'fm switch', or demote to an editable workspace "
            f"(add --runtime mount, or run: fm update {bench.name} --runtime mount first). "
            "'fm update' on an image bench changes settings only (SSL/env/domains/policy).",
        )
        raise typer.Exit(1)

    # Validated up front, beside the image-runtime gate: this check used to sit in the middle of the
    # decision table, so an --environment or --runtime change of the same invocation had already been
    # rendered and force-recreated on the running containers when the usage error aborted the command
    # -- and the pending bench_config.toml save with it, leaving disk and containers disagreeing.
    current_newrelic = bench.bench_config.get_newrelic_config()
    if newrelic is True and not (newrelic_license_key or (current_newrelic and current_newrelic.license_key)):
        raise typer.BadParameter("--newrelic-license-key is required when enabling NewRelic.")

    # Every refusal below runs BEFORE the mutating blocks, for the same reason the NewRelic check
    # above was hoisted: a refusal that lands mid-table has already re-rendered compose files and
    # force-recreated containers by the time it fires, and it exits before the terminal
    # save_bench_config(), leaving bench_config.toml and the running containers permanently
    # disagreeing. A refused `fm update` must change nothing.
    materializing_workspace = demoting_to_mount and bench.bench_config.runtime == BenchRuntime.image

    if runtime == BenchRuntime.image and bench.bench_config.runtime != BenchRuntime.image:
        output.display_error(
            "mount -> image conversion runs through the deploy pipeline (it must migrate the "
            "site onto the baked image): set runtime = 'image' and a top-level image in "
            f"bench_config.toml, then run fm switch {bench.name} <repo:tag>.",
        )
        raise typer.Exit(1)

    # The tag the demotion extracts the workspace from; an image bench with no recorded deploy has
    # nothing to materialize.
    demotion_tag: str | None = None
    if materializing_workspace:
        deploy_state = bench.bench_config.deploy_state
        demotion_tag = deploy_state.current_tag if deploy_state else None
        if not demotion_tag:
            output.display_error("No deployed tag recorded; cannot materialize the workspace.")
            raise typer.Exit(1)

    database_config = None
    if db_ca is not None:
        database_config = bench.bench_config.get_database_config()
        if database_config is None:
            output.display_error(
                f"{bench.site_name} has no \\[database] entry in bench_config.toml: the bench uses the fm-managed "
                "'global-db' container, whose TLS material fm owns, so there is no external CA to refresh.",
            )
            raise typer.Exit(1)

    add_domains_list = add_alias if add_alias else []
    remove_domains_list = remove_alias if remove_alias else []
    if add_domains_list:
        skip_check = allow_domain_conflicts or not fm_config.validation.enforce_domain_uniqueness
        try:
            validate_domains_unique(
                add_domains_list,
                benches_root=CLI_BENCHES_DIRECTORY,
                exclude_bench=bench.name,
                skip_check=skip_check,
            )
        except DomainConflictError as e:
            output.display_error(str(e))
            output.print("\nTo proceed anyway, use: --allow-domain-conflicts", emoji_code="")
            raise typer.Exit(1) from e

    bench_config_save = False

    if not bench.running:
        raise BenchNotRunning(bench_name=bench.name)

    def validate_version_requests() -> tuple[dict, str | None, str | None]:
        """Refuse an incompatible --python/--node before either one is written, and hand the block
        that applies them the values it reports with.

        A callable rather than another entry in the validation block above because frappe's own
        requirement is read from the workspace's apps/frappe, and when the same command demotes an
        image bench that workspace does not exist until the demotion has run.
        """
        frappe_app_path = bench.path / "workspace" / "frappe-bench" / "apps" / "frappe"
        current_versions = bench.app_manager.get_current_runtime_versions(use_run=True)

        python_req = None
        node_req = None
        if frappe_app_path.exists():
            if python_version:
                python_req = extract_python_version_requirement(frappe_app_path)
            if node_version:
                node_req = extract_node_version_requirement(frappe_app_path)

        # BOTH requested versions are validated before EITHER is written: the node refusal used to
        # run after python_version had been assigned to the in-memory bench_config and announced as
        # updated, then exited before save_bench_config() -- so the accepted half of the request was
        # reported as done and silently discarded.
        if python_version and python_req and not skip_version_check:
            is_compatible, error_msg = validate_python_version_compatibility(python_version, python_req)
            if not is_compatible:
                output.change_head("Python version validation failed")
                output.print(f"Python: {current_versions.get('python') or 'not set'} -> {python_version}")
                output.print(f"Frappe requires: {python_req}")
                output.display_error(f"{error_msg}", emoji_code=":cross_mark:")
                suggested = parse_python_version_for_runtime(python_req)
                if suggested:
                    output.print(f"Hint: Try --python {suggested}", emoji_code=":light_bulb:")
                output.print("Use --skip-version-check to bypass this validation (not recommended)")
                raise typer.Exit(code=1)

        if node_version and node_req and not skip_version_check:
            is_compatible, error_msg = validate_node_version_compatibility(node_version, node_req)
            if not is_compatible:
                output.change_head("Node version validation failed")
                output.print(f"Node: {current_versions.get('node') or 'not set'} -> {node_version}")
                output.print(f"Frappe requires: {node_req}")
                output.display_error(f"{error_msg}", emoji_code=":cross_mark:")
                suggested = parse_node_version_for_runtime(node_req)
                if suggested:
                    output.print(f"Hint: Try --node {suggested}", emoji_code=":light_bulb:")
                output.print("Use --skip-version-check to bypass this validation (not recommended)")
                raise typer.Exit(code=1)

        return current_versions, python_req, node_req

    version_requests = None
    if (python_version or node_version) and not materializing_workspace:
        version_requests = validate_version_requests()

    with spinner(output, "Updating bench configuration"):
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

        if runtime:
            if runtime == bench.bench_config.runtime:
                output.print(f"Bench runtime is already '{runtime.value}'")
            else:
                # image -> mount demotion (mount -> image was refused up front): extract the
                # editable workspace from the CURRENTLY DEPLOYED tag -- code on disk == running
                # code, so no migrate is needed; site data already lives host-side and is untouched.
                from frappe_manager.site_manager.modules.transport import fetch_image
                from frappe_manager.site_manager.modules.workspace_seed import (
                    materialize_workspace_from_image,
                    stash_conflicting_seed_paths,
                )

                output.change_head(f"Materializing editable workspace from {demotion_tag}")
                fetch_image(bench.docker_client, demotion_tag, output=output)
                frappe_bench_dir = bench.path / "workspace" / "frappe-bench"
                # Leftover code trees from an earlier mount life are STALE vs the
                # deployed tag; keeping them would break "code on disk == running
                # code". Stash them aside (never delete) and extract fresh.
                stash = stash_conflicting_seed_paths(frappe_bench_dir, output=output)
                if stash:
                    output.warning(
                        f"Existing workspace code was stale vs {demotion_tag}; moved to {stash} -- review and delete it.",
                    )
                extracted = materialize_workspace_from_image(
                    bench.docker_client, demotion_tag, frappe_bench_dir, output=output
                )
                output.print(
                    f"Extracted from image: {', '.join(extracted) if extracted else 'nothing (already present)'}"
                )

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

                output.print(f"Switched runtime to mount (workspace from {demotion_tag})")
                # Persisted the moment the demotion completes, like the NewRelic block below.
                # The workspace is extracted and the containers already run it, and the one refusal
                # still ahead of us -- an incompatible --python/--node -- can only be checked
                # against the workspace this step just created, so it cannot be hoisted with the
                # others. Deferring this write to the terminal save would let that refusal leave
                # bench_config.toml claiming image runtime for a bench now running on mount.
                bench.save_bench_config()
                bench_config_save = False

        if restart:
            old_policy = bench.bench_config.restart_policy.value
            if restart != bench.bench_config.restart_policy:
                output.change_head(f"Updating restart policy from '{old_policy}' to '{restart.value}'")

                if restart == RestartPolicyEnum.no and bench.bench_config.environment_type == FMBenchEnvType.prod:
                    output.warning("Setting restart policy to 'no' on production bench")
                    output.warning("Containers will not auto-recover from failures or system reboots")

                bench.bench_config.restart_policy = restart
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

                output.print(f"Updated restart policy to '{restart.value}'")
                bench_config_save = True
            else:
                output.print(f"Restart policy is already set to '{restart.value}'")

        if admin_tools:
            wanted = admin_tools == EnableDisableOptionsEnum.enable

            if apps_site:
                # The site half narrows the SAME flag, exactly as `fm auth BENCH/SITE` narrows
                # `--protect web`: the address is what says the scope. What differs is only the
                # mechanism the scope implies. Off for the bench means stop the one container pair,
                # because nothing is left needing it; off for one site can only mean drop that
                # site's routes, because the bench's other sites still reach the same containers.
                recorded = bench.bench_config.sites or {}
                if apps_site not in recorded:
                    output.display_error(
                        f"Bench '{bench.name}' records no entry for site '{apps_site}', so there is nowhere to store its tool routing."
                    )
                    raise typer.Exit(1)

                if wanted and not bench.bench_config.admin_tools:
                    # Nothing to route to: routing a hostname at a stopped container is a 502.
                    output.display_error(
                        f"Admin tools are disabled on {bench.name}, so there is nothing to route from '{apps_site}'. Start them for the bench first with 'fm update {bench.name} --admin-tools enable'."
                    )
                    raise typer.Exit(1)

                output.change_head(f"{'Routing' if wanted else 'Unrouting'} admin tools for {apps_site}")
                recorded[apps_site].serve_admin_tools = wanted
                bench_config_save = True

                # Creating and removing the location files is the command's job; ensure_fm_nginx_confs
                # only REFRESHES ones already on disk, so a site getting its first one renders here.
                bench.admin_tools.save_nginx_location_config()
                try:
                    bench.bench_nginx_controller.reload()
                except Exception as e:
                    output.warning(f"Config written but nginx did not reload, so it applies on next start: {e}")
                output.print(
                    f"/adminer/ and /mailpit/ {'now answer' if wanted else 'no longer answer'} on {apps_site} and its aliases"
                )

            elif wanted:
                output.change_head("Enabling Admin-tools")
                bench.bench_config.admin_tools = True

                if not bench.admin_tools.compose_file_manager.compose_path.exists():
                    # Seeds the compose file and brings the tools up, but it carries no mail choice of
                    # its own (it enables with force_configure defaulted to False), so the mail keys are
                    # written right after it -- otherwise --mailpit-as-default-mail-server is silently
                    # dropped on every bench that never had admin tools, i.e. every `-e prod` one.
                    bench.sync_admin_tools_compose()
                    if mailpit_as_default_mail_server:
                        bench.admin_tools.configure_mailpit_as_default_server()
                else:
                    bench.admin_tools.enable(force_configure=mailpit_as_default_mail_server)

                # The tools vhost renders `auth_basic_user_file .../<bench>.htpasswd`, but that file is owned
                # solely by ensure_fm_nginx_confs(), whose guard skips it while admin tools are off -- so on a
                # bench created with -e prod (or one where tools were disabled) it is absent and the freshly
                # enabled tools surface answers HTTP 500. Mint it now that the surface exists.
                bench.ensure_fm_nginx_confs()

                bench_config_save = True
                output.print("Enabled Admin-tools")

            elif (
                not bench.admin_tools.compose_file_manager.compose_path.exists()
                or not bench.bench_config.admin_tools
            ):
                # Report and fall through. An early `return` here would abort the WHOLE command,
                # silently dropping every other flag of the same invocation -- including work already
                # done (a --db-ca install awaiting the terminal save) and work still queued.
                output.print("Admin tools is already disabled")

            else:
                bench.bench_config.admin_tools = False
                bench.admin_tools.disable()
                bench_config_save = True

        if add_alias or remove_alias:
            output.change_head("Updating alias domains")
            # An alias is an alternate for a SITE, so `fm update BENCH/SITE` names the one it
            # attaches to and a bare `fm update BENCH` means the bench's primary site.
            bench.update_alias_domains(
                add_domains=add_domains_list,
                remove_domains=remove_domains_list,
                site=apps_site,
            )
            output.print("Alias domains updated successfully")

        # Handle upload limit update
        if upload_limit:
            output.change_head(f"Updating upload size limit to {upload_limit}")
            bench.update_upload_limit(upload_limit)

        if newrelic is not None or newrelic_license_key is not None:
            monitoring = bench.bench_config.monitoring or MonitoringConfig()
            newrelic_config = monitoring.newrelic or NewRelicConfig()

            if newrelic is not None:
                newrelic_config.enabled = newrelic

            if newrelic_license_key is not None:
                newrelic_config.license_key = newrelic_license_key

            monitoring.newrelic = newrelic_config
            bench.bench_config.monitoring = monitoring

            bench.generate_compose(bench.bench_config.export_to_compose_inputs())
            bench.save_bench_config()
            bench_config_save = False

            bench.supervisor.setup_newrelic(bench.path)

            output.change_head("Restarting frappe container to apply NewRelic changes")
            bench.docker_client.compose.up(services=["frappe"], detach=True, force_recreate=True)
            output.print("NewRelic configuration updated")

        if apps:
            from typing import cast

            apps_overrides = cast("list[AppConfig]", apps)
            added, apps_stash = bench.app_manager.graft_apps(apps_overrides, stash=True, use_run=False)
            if apps_stash:
                output.warning(f"Replaced app code moved to {apps_stash} -- review and delete it.")

            # Fetching the code and installing it into a site are two different operations, and only
            # the second writes to a database. The address says which sites get the second: a bare
            # bench name does the code only, so the bench's `apps` list grows and any site created
            # later picks it up, while nothing existing is migrated without being named. This used
            # to install with no site at all, which resolved to `install_app_to_site`'s default of
            # the BENCH name: `bench --site shop install-app` on a bench serving `shop.localhost`
            # names a site that does not exist, so the step failed outright.
            targets: list[str] = []
            if apps_site == RESERVED_BENCH_NAME:
                targets = list(bench.bench_config.site_names)
            elif apps_site:
                targets = [apps_site]

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

            output.change_head("Restarting services to load grafted apps")
            bench.restart_web_containers_services(use_container_restart=False)
            bench.restart_workers_containers_services(use_container_restart=False)
            bench_config_save = True

            if failed:
                output.display_error(f"Apps grafted, but these sites failed: {', '.join(failed)}")
                raise typer.Exit(1)

            if targets:
                output.print(f"Grafted apps applied to {', '.join(targets)}: {', '.join(a.name for a in apps_overrides)}")
            else:
                output.print(
                    f"Grafted app code into the bench: {', '.join(a.name for a in apps_overrides)}. "
                    f"Install it with 'fm update {bench.name}/all --apps ...' or name one site."
                )

        if python_version or node_version:
            # Refused up front unless this same command materializes the workspace the requirement
            # is read from, in which case it could not be checked any earlier than here.
            current_versions, frappe_python_req, frappe_node_req = (
                version_requests if version_requests is not None else validate_version_requests()
            )

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

            bench.save_bench_config()
            bench_config_save = False

            output.change_head("Setting up new runtime environment")
            venv_recreated = bench.app_manager.setup_python_and_node_environments(
                use_run=True, recreate_python_env=recreate_python_env
            )
            output.print("Runtime versions updated successfully")

            if venv_recreated:
                apps_txt_path = bench.path / "workspace" / "frappe-bench" / "sites" / "apps.txt"
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
            bench.restart_workers_containers_services(use_container_restart=False)
            output.print("All services restarted successfully")

        if bench_config_save:
            bench.save_bench_config()
