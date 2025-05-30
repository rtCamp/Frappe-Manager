from pathlib import Path
import typer
import os
import sys
import shutil
from typing import Annotated, Optional
from frappe_manager.services_manager.services_exceptions import ServicesNotCreated
from frappe_manager.display_manager.DisplayManager import richprint
from frappe_manager import CLI_DIR, CLI_BENCHES_DIRECTORY
import logging
from frappe_manager.docker_wrapper.DockerClient import DockerClient
from frappe_manager.logger import log
from frappe_manager.services_manager.services import ServicesManager
from frappe_manager.migration_manager.migration_executor import MigrationExecutor
from frappe_manager.utils.callbacks import version_callback
from frappe_manager.utils.helpers import is_cli_help_called, get_current_fm_version
from frappe_manager.services_manager.commands import services_root_command
from frappe_manager.sub_commands.self_commands import self_app
from frappe_manager.sub_commands.ssl_command import ssl_root_command
from frappe_manager.metadata_manager import FMConfigManager
from frappe_manager.migration_manager.version import Version
from frappe_manager.utils.site import pull_docker_images
from frappe_manager.commands import (
    create, delete, list_benches, start, stop,
    code, logs, shell, info, update, reset,
    restart, ngrok
)

app = typer.Typer(no_args_is_help=True, rich_markup_mode="rich")
app.add_typer(services_root_command, name="services", help="Handle global services.")
app.add_typer(self_app, name="self", help="Perform operations related to the [bold][blue]fm[/bold][/blue] itself.")
app.add_typer(ssl_root_command, name="ssl", help="Perform operations related to ssl.")

def _initialize_directories() -> bool:
    """Initialize FM directories if they don't exist"""
    if not CLI_DIR.exists():
        CLI_DIR.mkdir(parents=True, exist_ok=True)
        CLI_BENCHES_DIRECTORY.mkdir(parents=True, exist_ok=True)
        richprint.print(f"fm directory doesn't exists! Created at -> {str(CLI_DIR)}")
        return True
    
    if not CLI_DIR.is_dir():
        richprint.exit("Sites directory is not a directory! Aborting!")
    
    return False

def _setup_logging() -> logging.Logger:
    """Initialize and setup logging"""
    logger = log.get_logger()
    logger.info("")
    logger.info(f"{':' * 20}FM Invoked{':' * 20}")
    logger.info("")
    logger.info(f"RUNNING COMMAND: {' '.join(sys.argv[1:])}")
    logger.info("-" * 20)
    return logger

def _check_docker_daemon():
    """Verify Docker daemon is running"""
    if not DockerClient().server_running():
        richprint.exit("Docker daemon not running. Please start docker service.")

def _handle_first_install(is_first_install: bool, fm_config_manager: FMConfigManager):
    """Handle first time installation tasks"""
    if is_first_install:
        if not fm_config_manager.root_path.exists():
            richprint.print("It seems like the first installation. Pulling docker images...️", "🔍")
            
            if not pull_docker_images():
                shutil.rmtree(CLI_DIR)
                richprint.exit("Aborting. Not able to pull all required Docker images.")
            
            current_version = Version(get_current_fm_version())
            fm_config_manager.version = current_version
            fm_config_manager.export_to_toml()

def _initialize_services(verbose: bool, ctx: typer.Context) -> ServicesManager:
    """Initialize and setup services"""
    services_manager = ServicesManager(verbose=verbose)
    services_manager.set_typer_context(ctx)
    services_manager.init()
    
    try:
        services_manager.entrypoint_checks(start=True)
    except ServicesNotCreated as e:
        services_manager.remove_itself()
        richprint.exit(f"Not able to create services. {e}")
        
    return services_manager

@app.callback()
def app_callback(
    ctx: typer.Context,
    verbose: Annotated[bool, typer.Option("--verbose", "-v", help="Enable verbose output.")] = False,
    version: Annotated[
        Optional[bool], typer.Option("--version", "-V", help="Show Version.", callback=version_callback)
    ] = None,
):
    """
    Frappe-Manager for creating frappe development environments.
    """
    ctx.obj = {}
    help_called = is_cli_help_called(ctx)
    ctx.obj["is_help_called"] = help_called

    if not help_called:
        richprint.start("Working")
        
        # Initialize directories
        is_first_install = _initialize_directories()
        
        # Setup logging
        global logger
        logger = _setup_logging()
        
        # Check Docker daemon
        _check_docker_daemon()
        
        # Load config and handle first install
        fm_config_manager = FMConfigManager.import_from_toml()
        _handle_first_install(is_first_install, fm_config_manager)
        
        # Handle migrations
        migrations = MigrationExecutor(fm_config_manager)
        if not migrations.execute():
            richprint.exit(f"Rollbacked to previous version of fm {migrations.prev_version}.")
        
        # Initialize services
        services_manager = _initialize_services(verbose, ctx)
        
        # Set context objects
        ctx.obj["services"] = services_manager
        ctx.obj["verbose"] = verbose
        ctx.obj['fm_config_manager'] = fm_config_manager


@app.command(no_args_is_help=True)
def create(
    ctx: typer.Context,
    benchname: Annotated[str, typer.Argument(help="Name of the bench", callback=create_command_sitename_callback)],
    apps: Annotated[
        List[str],
        typer.Option(
            "--apps",
            "-a",
            help="FrappeVerse apps to install. App should be specified in format <appname>:<branch> or <appname>.",
            callback=apps_list_validation_callback,
            show_default=False,
        ),
    ] = [],
    environment: Annotated[
        FMBenchEnvType, typer.Option("--environment", "-e", help="Select bench environment type.")
    ] = FMBenchEnvType.dev,
    letsencrypt_preferred_challenge: Annotated[
        Optional[LETSENCRYPT_PREFERRED_CHALLENGE],
        typer.Option(help="Select preferred letsencrypt challenge.", show_default=False),
    ] = None,
    letsencrypt_email: Annotated[
        Optional[str],
        typer.Option(help="Specify email for letsencrypt", show_default=False),
    ] = None,
    developer_mode: Annotated[
        EnableDisableOptionsEnum, typer.Option(help="Toggle frappe developer mode.")
    ] = EnableDisableOptionsEnum.disable,
    frappe_branch: Annotated[
        str, typer.Option(help="Specify the branch name for frappe app", callback=frappe_branch_validation_callback)
    ] = "version-15",
    template: Annotated[bool, typer.Option(help="Create template bench.")] = False,
    admin_pass: Annotated[
        str,
        typer.Option(help="Password for the 'Administrator' User."),
    ] = "admin",
    ssl: Annotated[
        SUPPORTED_SSL_TYPES, typer.Option(help="Enable https", show_default=True)
    ] = SUPPORTED_SSL_TYPES.none,
):
    # TODO Create markdown table for the below help
    """
    Create a new bench.
    """

    services_manager: ServicesManager = ctx.obj["services"]
    fm_config_manager: FMConfigManager = ctx.obj["fm_config_manager"]
    verbose = ctx.obj['verbose']

    benches = BenchesManager(CLI_BENCHES_DIRECTORY, services=services_manager, verbose=verbose)
    benches.set_typer_context(ctx)

    bench_path = benches.root_path / benchname
    bench_config_path = bench_path / CLI_BENCH_CONFIG_FILE_NAME

    if ssl == SUPPORTED_SSL_TYPES.le:
        if not letsencrypt_preferred_challenge:
            if fm_config_manager.letsencrypt.exists:
                if letsencrypt_preferred_challenge is None:
                    letsencrypt_preferred_challenge = LETSENCRYPT_PREFERRED_CHALLENGE.dns01

            if not letsencrypt_preferred_challenge:
                letsencrypt_preferred_challenge = LETSENCRYPT_PREFERRED_CHALLENGE.http01

        if fm_config_manager.letsencrypt.email == 'dummy@fm.fm' or fm_config_manager.letsencrypt.email is None:
            if not letsencrypt_email:
                richprint.stop()
                raise typer.BadParameter("No email provided, required by certbot.", param_hint='--letsencrypt-email')
            else:
                email = letsencrypt_email

            validate_email(email, check_deliverability=False)
        else:
            richprint.print(
                "Defaulting to Let's Encrypt email from [blue]fm_config.toml[/blue] since [blue]'--letsencrypt-email'[/blue] is not given."
            )
            email = fm_config_manager.letsencrypt.email

        ssl_certificate = LetsencryptSSLCertificate(
            domain=benchname,
            ssl_type=ssl,
            email=email,
            preferred_challenge=letsencrypt_preferred_challenge,
            api_key=fm_config_manager.letsencrypt.api_key,
            api_token=fm_config_manager.letsencrypt.api_token,
        )

    elif ssl == SUPPORTED_SSL_TYPES.none:
        ssl_certificate = SSLCertificate(domain=benchname, ssl_type=ssl)

    if developer_mode == EnableDisableOptionsEnum.enable:
        developer_mode_status = True
    elif developer_mode == EnableDisableOptionsEnum.disable:
        developer_mode_status = False

    bench_config: BenchConfig = BenchConfig(
        name=benchname,
        apps_list=apps,
        frappe_branch=frappe_branch,
        developer_mode=True if environment == FMBenchEnvType.dev else developer_mode_status,
        admin_tools=True if environment == FMBenchEnvType.dev else False,
        admin_pass=admin_pass,
        # TODO get this info from services, maybe ?
        environment_type=environment,
        root_path=bench_config_path,
        ssl=ssl_certificate,
    )

    compose_path = bench_path / 'docker-compose.yml'
    compose_file_manager = ComposeFile(compose_path)
    compose_project = ComposeProject(compose_file_manager, verbose)

    bench: Bench = Bench(bench_path, benchname, bench_config, compose_project, services_manager)
    benches.add_bench(bench)
    benches.create_benches(is_template_bench=template)


@app.command()
def delete(
    ctx: typer.Context,
    benchname: Annotated[
        Optional[str],
        typer.Argument(
            help="Name of the bench.", autocompletion=sites_autocompletion_callback, callback=sitename_callback
        ),
    ] = None,
    force: Annotated[bool, typer.Option("--force", "-f", help="Force delete bench.")] = False,
):
    """Delete a bench."""

    if benchname:
        services_manager = ctx.obj["services"]
        verbose = ctx.obj['verbose']
        benches = BenchesManager(CLI_BENCHES_DIRECTORY, services=services_manager, verbose=verbose)
        benches.set_typer_context(ctx)

        bench_path: Path = benches.root_path / benchname
        bench_compose_path = bench_path / 'docker-compose.yml'
        compose_file_manager = ComposeFile(bench_compose_path)
        bench_compose_project = ComposeProject(compose_file_manager)

        bench_config_path = bench_path / CLI_BENCH_CONFIG_FILE_NAME
        # try using bench object if not then create bench

        if not bench_config_path.exists():
            uid: int = os.getuid()
            gid: int = os.getgid()

            # generate fake bench
            fake_config = BenchConfig(
                name=benchname,
                userid=uid,
                usergroup=gid,
                apps_list=[],
                frappe_branch=STABLE_APP_BRANCH_MAPPING_LIST['frappe'],
                developer_mode=False,
                # TODO do something about this forcefully delete maybe
                admin_tools=False,
                admin_pass='pass',
                environment_type=FMBenchEnvType.dev,
                ssl=SSLCertificate(domain=benchname, ssl_type=SUPPORTED_SSL_TYPES.none),
                root_path=bench_config_path,
            )

            bench = Bench(
                bench_path,
                benchname,
                fake_config,
                bench_compose_project,
                services=services_manager,
                workers_check=False,
            )

        else:
            bench = Bench.get_object(benchname, services=services_manager, workers_check=False, admin_tools_check=False)

        benches.add_bench(bench)
        benches.remove_benches()


@app.command()
def list(ctx: typer.Context):
    """Lists all of the available benches."""

    services_manager = ctx.obj["services"]
    verbose = ctx.obj['verbose']
    benches = BenchesManager(CLI_BENCHES_DIRECTORY, services=services_manager, verbose=verbose)
    benches.set_typer_context(ctx)
    benches.list_benches()


@app.command()
def start(
    ctx: typer.Context,
    benchname: Annotated[
        Optional[str],
        typer.Argument(
            help="Name of the bench.", autocompletion=sites_autocompletion_callback, callback=sitename_callback
        ),
    ] = None,
    force: Annotated[bool, typer.Option("--force", "-f", help="Force recreate bench containers")] = False,
    sync_bench_config_changes: Annotated[
        bool, typer.Option("--sync-config", help="Sync bench configuration changes")
    ] = False,
    reconfigure_supervisor: Annotated[
        bool, typer.Option("--reconfigure-supervisor", help="Reconfigure supervisord configuration")
    ] = False,
    reconfigure_common_site_config: Annotated[
        bool, typer.Option("--reconfigure-common-site-config", help="Reconfigure common_site_config.json")
    ] = False,
    reconfigure_workers: Annotated[
        bool, typer.Option("--reconfigure-workers", help="Reconfigure workers configuration")
    ] = False,
    include_default_workers: Annotated[bool, typer.Option(help="Include default worker configuration")] = True,
    include_custom_workers: Annotated[bool, typer.Option(help="Include custom worker configuration")] = True,
    sync_dev_packages: Annotated[bool, typer.Option("--sync-dev-packages", help="Sync dev packages")] = False,
):
    """Start a bench."""

    services_manager = ctx.obj["services"]
    verbose = ctx.obj['verbose']
    bench = Bench.get_object(benchname, services_manager)

    bench.start(
        force=force,
        sync_bench_config_changes=sync_bench_config_changes,
        reconfigure_workers=reconfigure_workers,
        include_default_workers=include_default_workers,
        include_custom_workers=include_custom_workers,
        reconfigure_common_site_config=reconfigure_common_site_config,
        reconfigure_supervisor=reconfigure_supervisor,
        sync_dev_packages=sync_dev_packages,
    )


@app.command()
def stop(
    ctx: typer.Context,
    benchname: Annotated[
        Optional[str],
        typer.Argument(
            help="Name of the bench.", autocompletion=sites_autocompletion_callback, callback=sitename_callback
        ),
    ] = None,
):
    """Stop a bench."""

    services_manager = ctx.obj["services"]
    verbose = ctx.obj['verbose']
    bench = Bench.get_object(benchname, services_manager)
    benches = BenchesManager(CLI_BENCHES_DIRECTORY, services=services_manager, verbose=verbose)
    benches.add_bench(bench)
    benches.stop_benches()


@app.command()
def code(
    ctx: typer.Context,
    benchname: Annotated[
        Optional[str],
        typer.Argument(
            help="Name of the bench.", autocompletion=sites_autocompletion_callback, callback=sitename_callback
        ),
    ] = None,
    user: Annotated[str, typer.Option(help="Connect as this user.")] = "frappe",
    extensions: Annotated[
        List[str],
        typer.Option(
            "--extension",
            "-e",
            help="List of extensions to install in vscode at startup.Provide extension id eg: ms-python.python",
            callback=code_command_extensions_callback,
            show_default=False,
        ),
    ] = DEFAULT_EXTENSIONS,
    force_start: Annotated[
        bool, typer.Option("--force-start", "-f", help="Force start the site before attaching to container.")
    ] = False,
    debugger: Annotated[bool, typer.Option("--debugger", "-d", help="Sync vscode debugger configuration.")] = False,
    workdir: Annotated[
        str, typer.Option("--work-dir", "-w", help="Set working directory in vscode.")
    ] = '/workspace/frappe-bench',
):
    """Open bench in vscode."""

    services_manager = ctx.obj["services"]
    verbose = ctx.obj['verbose']
    bench = Bench.get_object(benchname, services_manager)

    if force_start:
        bench.start()

    bench.attach_to_bench(user=user, extensions=extensions, workdir=workdir, debugger=debugger)


@app.command()
def logs(
    ctx: typer.Context,
    benchname: Annotated[
        Optional[str],
        typer.Argument(
            help="Name of the bench.", autocompletion=sites_autocompletion_callback, callback=sitename_callback
        ),
    ] = None,
    service: Annotated[
        Optional[SiteServicesEnum], typer.Option(help="Specify compose service name to show container logs.")
    ] = None,
    follow: Annotated[bool, typer.Option("--follow", "-f", help="Follow logs.")] = False,
):
    """Show frappe server logs or container logs for a given bench."""

    services_manager = ctx.obj["services"]
    verbose = ctx.obj['verbose']
    bench = Bench.get_object(benchname, services_manager)
    bench.logs(follow, service)


@app.command()
def shell(
    ctx: typer.Context,
    benchname: Annotated[
        Optional[str],
        typer.Argument(
            help="Name of the bench.", autocompletion=sites_autocompletion_callback, callback=sitename_callback
        ),
    ] = None,
    user: Annotated[Optional[str], typer.Option(help="Connect as this user.", show_default=False)] = None,
    service: Annotated[
        SiteServicesEnum, typer.Option(help="Specify compose service name for which to spawn shell.")
    ] = SiteServicesEnum.frappe,
):
    """Spawn shell for the give bench."""

    services_manager = ctx.obj["services"]
    verbose = ctx.obj['verbose']
    bench = Bench.get_object(benchname, services_manager)
    bench.shell(SiteServicesEnum(service).value, user)


@app.command()
def info(
    ctx: typer.Context,
    benchname: Annotated[
        Optional[str],
        typer.Argument(
            help="Name of the bench.", autocompletion=sites_autocompletion_callback, callback=sitename_callback
        ),
    ] = None,
):
    """Shows information about given bench."""

    services_manager = ctx.obj["services"]
    verbose = ctx.obj['verbose']
    bench = Bench.get_object(benchname, services_manager)
    bench.info()


@app.command()
def update(
    ctx: typer.Context,
    benchname: Annotated[
        Optional[str],
        typer.Argument(
            help="Name of the bench.", autocompletion=sites_autocompletion_callback, callback=sitename_callback
        ),
    ] = None,
    ssl: Annotated[Optional[SUPPORTED_SSL_TYPES], typer.Option(help="Enable SSL.", show_default=False)] = None,
    admin_tools: Annotated[
        Optional[EnableDisableOptionsEnum],
        typer.Option("--admin-tools", help="Toggle admin-tools.", show_default=False),
    ] = None,
    letsencrypt_preferred_challenge: Annotated[
        Optional[LETSENCRYPT_PREFERRED_CHALLENGE],
        typer.Option(help="Select preferred letsencrypt challenge.", show_default=False),
    ] = None,
    letsencrypt_email: Annotated[
        Optional[str],
        typer.Option(help="Specify email for letsencrypt", show_default=False),
    ] = None,
    environment: Annotated[
        Optional[FMBenchEnvType],
        typer.Option("--environment", "-e", help="Switch bench environment.", show_default=False),
    ] = None,
    developer_mode: Annotated[
        Optional[EnableDisableOptionsEnum],
        typer.Option(help="Toggle frappe developer mode.", show_default=False),
    ] = None,
    mailpit_as_default_mail_server: Annotated[
        bool,
        typer.Option(
            "--mailpit-as-default-mail-server", help="Configure Mailpit as default mail server", show_default=False
        ),
    ] = False,
):
    """Update bench."""

    services_manager = ctx.obj["services"]
    bench = Bench.get_object(benchname, services_manager)
    fm_config_manager: FMConfigManager = ctx.obj["fm_config_manager"]

    bench_config_save = False

    if not bench.compose_project.running:
        raise BenchNotRunning(bench_name=bench.name)

    if developer_mode:
        if developer_mode == EnableDisableOptionsEnum.enable:
            bench.bench_config.developer_mode = True
            richprint.print("Enabling frappe developer mode.")
            bench.set_common_bench_config({'developer_mode': bench.bench_config.developer_mode})
            richprint.print("Enabled frappe developer mode.")
        elif developer_mode == EnableDisableOptionsEnum.disable:
            bench.bench_config.developer_mode = False
            richprint.print("Disabling frappe developer mode.")
            bench.set_common_bench_config({'developer_mode': bench.bench_config.developer_mode})
            richprint.print("Enabled frappe developer mode.")

        bench_config_save = True

    if environment:
        richprint.change_head(f"Switching bench environemnt to {environment.value}")
        bench.bench_config.environment_type = environment
        bench.switch_bench_env()
        richprint.print(f"Switched bench environemnt to {environment.value}.")
        bench_config_save = True

    if ssl:
        new_ssl_certificate = SSLCertificate(domain=benchname, ssl_type=SUPPORTED_SSL_TYPES.none)

        if ssl == SUPPORTED_SSL_TYPES.le:
            if not letsencrypt_preferred_challenge:
                if fm_config_manager.letsencrypt.exists:
                    if letsencrypt_preferred_challenge is None:
                        letsencrypt_preferred_challenge = LETSENCRYPT_PREFERRED_CHALLENGE.dns01

                if not letsencrypt_preferred_challenge:
                    letsencrypt_preferred_challenge = LETSENCRYPT_PREFERRED_CHALLENGE.http01

            if fm_config_manager.letsencrypt.email == 'dummy@fm.fm' or fm_config_manager.letsencrypt.email is None:
                if not letsencrypt_email:
                    richprint.stop()
                    raise typer.BadParameter(
                        "No email provided, required by certbot.", param_hint='--letsencrypt-email'
                    )
                else:
                    email = letsencrypt_email

                validate_email(email, check_deliverability=False)
            else:
                richprint.print(
                    "Defaulting to Let's Encrypt email from [blue]fm_config.toml[/blue] since [blue]'--letsencrypt-email'[/blue] is not given."
                )
                email = fm_config_manager.letsencrypt.email

            new_ssl_certificate = LetsencryptSSLCertificate(
                domain=benchname,
                ssl_type=ssl,
                email=email,
                preferred_challenge=letsencrypt_preferred_challenge,
                api_key=fm_config_manager.letsencrypt.api_key,
                api_token=fm_config_manager.letsencrypt.api_token,
            )

        richprint.print("Updating Certificate.")
        bench.update_certificate(new_ssl_certificate)
        richprint.print("Certificate Updated.")

        if bench.has_certificate():
            richprint.print(
                f"SSL Certificate will expire in {format_ssl_certificate_time_remaining(bench.certificate_manager.get_certficate_expiry())}"
            )

    if admin_tools:
        if admin_tools == EnableDisableOptionsEnum.enable:
            richprint.change_head("Enabling Admin-tools")
            bench.bench_config.admin_tools = True

            if not bench.admin_tools.compose_project.compose_file_manager.compose_path.exists():
                bench.sync_admin_tools_compose()
            else:
                bench.admin_tools.enable(force_configure=mailpit_as_default_mail_server)

            bench_config_save = True
            richprint.print("Enabled Admin-tools.")

        elif admin_tools == EnableDisableOptionsEnum.disable:
            if (
                not bench.admin_tools.compose_project.compose_file_manager.compose_path.exists()
                or not bench.bench_config.admin_tools
            ):
                richprint.print("Admin tools is already disabled.")
                return
            else:
                bench.bench_config.admin_tools = False
                bench.admin_tools.disable()
                bench_config_save = True

    if bench_config_save:
        bench.save_bench_config()


@app.command()
def reset(
    ctx: typer.Context,
    benchname: Annotated[
        Optional[str],
        typer.Argument(
            help="Name of the bench.", autocompletion=sites_autocompletion_callback, callback=sitename_callback
        ),
    ] = None,
    admin_pass: Annotated[
        Optional[str],
        typer.Option(help="Password for the 'Administrator' User."),
    ] = None,
):
    """Reset bench site and reinstall all installed apps."""

    services_manager = ctx.obj["services"]
    verbose = ctx.obj['verbose']
    bench = Bench.get_object(benchname, services_manager)
    bench.reset(admin_pass)


@app.command()
def restart(
    ctx: typer.Context,
    benchname: Annotated[
        Optional[str],
        typer.Argument(
            help="Name of the bench.", autocompletion=sites_autocompletion_callback, callback=sitename_callback
        ),
    ] = None,
    web: Annotated[
        bool,
        typer.Option(help="Restart web service i.e socketio and frappe server."),
    ] = True,
    workers: Annotated[
        bool,
        typer.Option(help="Restart worker services i.e schedule and all workers."),
    ] = True,
    redis: Annotated[
        bool,
        typer.Option(help="Restart redis services."),
    ] = False,
):
    """Restart bench services."""

    services_manager = ctx.obj["services"]
    verbose = ctx.obj['verbose']
    bench = Bench.get_object(benchname, services_manager)

    if web:
        bench.restart_web_containers_services()

    if workers:
        bench.restart_workers_containers_services()

    if redis:
        bench.restart_redis_services_containers()


@app.command()
def ngrok(
    ctx: typer.Context,
    benchname: Annotated[
        Optional[str],
        typer.Argument(
            help="Name of the bench.", autocompletion=sites_autocompletion_callback, callback=sitename_callback
        ),
    ] = None,
    auth_token: Annotated[
        Optional[str],
        typer.Option("--auth-token", "-t", help="Ngrok authentication token", envvar="NGROK_AUTHTOKEN"),
    ] = None,
):
    """Create ngrok tunnel for the bench."""
    services_manager = ctx.obj["services"]
    verbose = ctx.obj['verbose']
    bench = Bench.get_object(benchname, services_manager)

    if not bench.compose_project.running:
        raise BenchNotRunning(bench_name=bench.name)

    fm_config_manager: FMConfigManager = ctx.obj["fm_config_manager"]

    richprint.start("Setting up ngrok tunnel")

    # Use token from config if available and no token provided
    if not auth_token and fm_config_manager.ngrok_auth_token:
        auth_token = fm_config_manager.ngrok_auth_token
        richprint.print("Using ngrok auth token from config file", emoji_code=":key:")
    elif not auth_token:
        richprint.exit(
            "Ngrok auth token is required. Please provide it with --auth-token or set NGROK_AUTHTOKEN environment variable."
        )

    # If token provided and not in config, ask to save
    if auth_token and not fm_config_manager.ngrok_auth_token:
        richprint.print("New auth token provided", emoji_code=":new:")
        should_save = richprint.prompt_ask(
            prompt="Do you want to save the ngrok auth token in config for future use?",
            choices=['yes', 'no'],
        )
        if should_save == 'yes':
            richprint.print("Saving auth token to config...", emoji_code=":floppy_disk:")
            fm_config_manager.ngrok_auth_token = auth_token
            fm_config_manager.export_to_toml()
            richprint.print("Saved ngrok auth token to config", emoji_code=":white_check_mark:")

    richprint.print(f"Creating ngrok tunnel for {bench.name}", emoji_code=":link:")

    try:
        create_tunnel(bench.name, auth_token)
    except Exception as e:
        richprint.error(f"Failed to create tunnel: {str(e)}")
        raise
