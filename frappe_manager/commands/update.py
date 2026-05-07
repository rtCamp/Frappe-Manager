from typing import Annotated

import typer
from typer_examples import example

from frappe_manager import CLI_BENCHES_DIRECTORY, EnableDisableOptionsEnum
from frappe_manager.metadata_manager import FMConfigManager
from frappe_manager.output_manager import get_global_output_handler, spinner
from frappe_manager.site_manager.bench_config import (
    AppConfig,
    FMBenchEnvType,
    RestartPolicyEnum,
    extract_node_version_requirement,
    extract_python_version_requirement,
    parse_node_version_for_runtime,
    parse_python_version_for_runtime,
    validate_node_version_compatibility,
    validate_python_version_compatibility,
)
from frappe_manager.site_manager.domain_conflict import DomainConflictError, validate_domains_unique
from frappe_manager.site_manager.exceptions import BenchNotRunning
from frappe_manager.site_manager.site import Bench
from frappe_manager.utils.callbacks import (
    alias_domains_validation_callback,
    sitename_callback,
    sites_autocompletion_callback,
)


@example(
    "Enable admin tools (Mailpit, Adminer)",
    "{benchname} --admin-tools enable",
    detail="Enables admin tools like Mailpit and Adminer for debugging and database access in development benches.",
    benchname="mybench",
)
@example(
    "Disable admin tools",
    "{benchname} --admin-tools disable",
    detail="Disables admin tools for security or production setups.",
    benchname="mybench",
)
@example(
    "Switch to production environment",
    "{benchname} -e prod",
    detail="Switches the bench to production environment settings and recreates necessary containers.",
    benchname="mybench",
)
@example(
    "Switch to development environment",
    "{benchname} -e dev",
    detail="Switches the bench to development environment settings and enables developer conveniences.",
    benchname="mybench",
)
@example(
    "Enable developer mode",
    "{benchname} --developer-mode enable",
    detail="Turns on Frappe developer mode which enables features useful for app development.",
    benchname="mybench",
)
@example(
    "Add alias domains",
    "{benchname} --add-alias www.example.com,api.example.com",
    detail="Adds alias domains to the bench; remember to add SSL certificates separately with 'fm ssl add'.",
    benchname="mybench",
)
@example(
    "Remove alias domains",
    "{benchname} --remove-alias shop.example.com",
    detail="Removes alias domains from bench configuration.",
    benchname="mybench",
)
@example(
    "Update Python version",
    "{benchname} --python 3.11",
    detail="Updates the bench Python runtime, recreates the virtual environment, and reinstalls all apps into the new environment.",
    benchname="mybench",
)
@example(
    "Install Python without recreating venv",
    "{benchname} --python 3.14 --no-recreate-python-env",
    detail="Installs Python 3.14 via uv and sets it as default without touching the existing virtual environment.",
    benchname="mybench",
)
@example(
    "Update Node version",
    "{benchname} --node 20",
    detail="Updates Node.js runtime used by the bench and rebuilds related assets.",
    benchname="mybench",
)
@example(
    "Set upload size limit",
    "{benchname} --upload-limit 100M",
    detail="Sets the maximum file upload size for the bench (useful for large attachments).",
    benchname="mybench",
)
def update(
    ctx: typer.Context,
    benchname: Annotated[
        str | None,
        typer.Argument(
            help="Name of the bench.",
            autocompletion=sites_autocompletion_callback,
            callback=sitename_callback,
        ),
    ] = None,
    admin_tools: Annotated[
        EnableDisableOptionsEnum | None,
        typer.Option("--admin-tools", help="Toggle admin-tools.", show_default=False),
    ] = None,
    environment: Annotated[
        FMBenchEnvType | None,
        typer.Option("--environment", "-e", help="Switch bench environment.", show_default=False),
    ] = None,
    developer_mode: Annotated[
        EnableDisableOptionsEnum | None,
        typer.Option(help="Toggle frappe developer mode.", show_default=False),
    ] = None,
    mailpit_as_default_mail_server: Annotated[
        bool,
        typer.Option(
            "--mailpit-as-default-mail-server",
            help="Configure Mailpit as default mail server",
            show_default=False,
        ),
    ] = False,
    add_alias: Annotated[
        str | None,
        typer.Option(
            "--add-alias",
            help="Add alias domains to the site (comma-separated, e.g., www.example.com,api.example.com)",
            callback=alias_domains_validation_callback,
            show_default=False,
        ),
    ] = None,
    remove_alias: Annotated[
        str | None,
        typer.Option(
            "--remove-alias",
            help="Remove alias domains from the site (comma-separated, e.g., shop.example.com)",
            callback=alias_domains_validation_callback,
            show_default=False,
        ),
    ] = None,
    upload_limit: Annotated[
        str | None,
        typer.Option(
            "--upload-limit",
            help="Set maximum upload size for files (e.g., '50M', '100M', '500M', '1G')",
            show_default=False,
        ),
    ] = None,
    python_version: Annotated[
        str | None,
        typer.Option(
            "--python",
            help="Update Python version (e.g., '3.11', '3.12', '>=3.11,<3.14'). Will recreate virtual environment.",
            show_default=False,
        ),
    ] = None,
    node_version: Annotated[
        str | None,
        typer.Option(
            "--node",
            help="Update Node version (e.g., '18', '20', '>=18'). Will install and set as default.",
            show_default=False,
        ),
    ] = None,
    skip_version_check: Annotated[
        bool,
        typer.Option(
            "--skip-version-check",
            help="Skip validation of Python/Node versions against Frappe requirements. Use with caution.",
            show_default=False,
        ),
    ] = False,
    recreate_python_env: Annotated[
        bool,
        typer.Option(
            "--recreate-python-env/--no-recreate-python-env",
            help="Recreate the Python virtual environment. Use --no-recreate-python-env to skip if current version already satisfies the requirement.",
            show_default=True,
        ),
    ] = True,
    restart: Annotated[
        RestartPolicyEnum | None,
        typer.Option(
            "--restart",
            help="Update Docker restart policy for all bench services.",
            show_default=False,
        ),
    ] = None,
    allow_domain_conflicts: Annotated[
        bool,
        typer.Option(
            "--allow-domain-conflicts",
            help="Skip domain uniqueness validation when adding aliases (not recommended).",
            show_default=False,
        ),
    ] = False,
    newrelic: Annotated[
        bool | None,
        typer.Option(
            "--newrelic/--no-newrelic",
            help="Enable or disable NewRelic APM monitoring for the web process.",
            show_default=False,
        ),
    ] = None,
    newrelic_license_key: Annotated[
        str | None,
        typer.Option(
            "--newrelic-license-key",
            help="NewRelic ingest license key.",
            show_default=False,
        ),
    ] = None,
):
    """
    Update bench configuration and settings.

    Adjusts environment type, developer mode, runtime versions, alias domains, and other bench settings.
    """

    services_manager = ctx.obj["services"]
    fm_config: FMConfigManager = ctx.obj["fm_config_manager"]

    output = get_global_output_handler()
    logger = ctx.obj.get("logger")
    bench = Bench.get_object(benchname, services_manager, logger=logger, output_handler=output)

    bench_config_save = False

    if not bench.running:
        raise BenchNotRunning(bench_name=bench.name)

    with spinner(output, "Updating bench configuration"):
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
                    db_host = bench.services.database_manager.database_server_info.host
                    bench.admin_tools.generate_compose(db_host)

                output.print("Restarting containers to apply restart policy..")
                bench.docker_client.compose.up(detach=True, force_recreate=True)

                output.print(f"Updated restart policy to '{restart.value}'")
                bench_config_save = True
            else:
                output.print(f"Restart policy is already set to '{restart.value}'")

        if admin_tools:
            if admin_tools == EnableDisableOptionsEnum.enable:
                output.change_head("Enabling Admin-tools")
                bench.bench_config.admin_tools = True

                if not bench.admin_tools.compose_file_manager.compose_path.exists():
                    bench.sync_admin_tools_compose()
                else:
                    bench.admin_tools.enable(force_configure=mailpit_as_default_mail_server)

                bench_config_save = True
                output.print("Enabled Admin-tools")

            elif admin_tools == EnableDisableOptionsEnum.disable:
                if (
                    not bench.admin_tools.compose_file_manager.compose_path.exists()
                    or not bench.bench_config.admin_tools
                ):
                    output.print("Admin tools is already disabled")
                    return
                bench.bench_config.admin_tools = False
                bench.admin_tools.disable()
                bench_config_save = True

        if add_alias or remove_alias:
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
                    raise typer.Exit(1)

            output.change_head("Updating alias domains")
            bench.update_alias_domains(add_domains=add_domains_list, remove_domains=remove_domains_list)
            output.print("Alias domains updated successfully")

        # Handle upload limit update
        if upload_limit:
            output.change_head(f"Updating upload size limit to {upload_limit}")
            bench.update_upload_limit(upload_limit)

        if newrelic is not None or newrelic_license_key is not None:
            if newrelic is True and not (newrelic_license_key or bench.bench_config.newrelic_license_key):
                raise typer.BadParameter("--newrelic-license-key is required when enabling NewRelic.")

            if newrelic is not None:
                bench.bench_config.newrelic_enabled = newrelic

            if newrelic_license_key is not None:
                bench.bench_config.newrelic_license_key = newrelic_license_key

            bench.generate_compose(bench.bench_config.export_to_compose_inputs())
            bench.save_bench_config()
            bench_config_save = False

            bench.supervisor.setup_newrelic(bench.path)

            output.change_head("Restarting frappe container to apply NewRelic changes")
            bench.docker_client.compose.up(services=["frappe", "schedule"], detach=True, force_recreate=True)

            output.change_head("Restarting worker and schedule containers to apply NewRelic changes")
            workers_configured = bench.sync_workers_compose(force_recreate=False, setup_supervisor=False)
            if workers_configured and bench.workers.compose_file_manager.exists():
                bench.workers.docker_client.compose.up(
                    services=[],
                    detach=True,
                    force_recreate=True,
                )

            output.print("NewRelic configuration updated (NR will be auto-installed on first supervisor start)")

        if python_version or node_version:
            frappe_app_path = bench.path / "workspace" / "frappe-bench" / "apps" / "frappe"

            if python_version or node_version:
                current_versions = bench.app_manager.get_current_runtime_versions(use_run=True)

            if python_version:
                old_python = current_versions.get("python") or "not set"

                frappe_python_req = None
                if frappe_app_path.exists():
                    frappe_python_req = extract_python_version_requirement(frappe_app_path)
                    if frappe_python_req and not skip_version_check:
                        is_compatible, error_msg = validate_python_version_compatibility(
                            python_version,
                            frappe_python_req,
                        )
                        if not is_compatible:
                            output.change_head("Python version validation failed")
                            output.print(f"Python: {old_python} -> {python_version}")
                            output.print(f"Frappe requires: {frappe_python_req}")
                            output.display_error(f"{error_msg}", emoji_code=":cross_mark:")
                            suggested = parse_python_version_for_runtime(frappe_python_req)
                            if suggested:
                                output.print(f"Hint: Try --python {suggested}", emoji_code=":light_bulb:")
                            output.print("Use --skip-version-check to bypass this validation (not recommended)")
                            raise typer.Exit(code=1)

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
                frappe_node_req = None
                if frappe_app_path.exists():
                    frappe_node_req = extract_node_version_requirement(frappe_app_path)
                    if frappe_node_req and not skip_version_check:
                        is_compatible, error_msg = validate_node_version_compatibility(node_version, frappe_node_req)
                        if not is_compatible:
                            output.change_head("Node version validation failed")
                            output.print(f"Node: {old_node} -> {node_version}")
                            output.print(f"Frappe requires: {frappe_node_req}")
                            output.display_error(f"{error_msg}", emoji_code=":cross_mark:")

                            suggested = parse_node_version_for_runtime(frappe_node_req)
                            if suggested:
                                output.print(f"Hint: Try --node {suggested}", emoji_code=":light_bulb:")
                            output.print("Use --skip-version-check to bypass this validation (not recommended)")
                            raise typer.Exit(code=1)

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
