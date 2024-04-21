from pathlib import Path
import typer
import os
import sys
import shutil
from typing import Annotated, List, Optional
from frappe_manager.compose_project.compose_project import ComposeProject
from frappe_manager.services_manager.services_exceptions import ServicesNotCreated
from frappe_manager.site_manager.SiteManager import BenchesManager
from frappe_manager.display_manager.DisplayManager import richprint
from frappe_manager import (
    CLI_BENCH_CONFIG_FILE_NAME,
    CLI_DIR,
    DEFAULT_EXTENSIONS,
    STABLE_APP_BRANCH_MAPPING_LIST,
    AdminToolOptionEnum,
    SiteServicesEnum,
    CLI_BENCHES_DIRECTORY,
)
from frappe_manager.docker_wrapper.DockerClient import DockerClient
from frappe_manager.logger import log
from frappe_manager.services_manager.services import ServicesManager
from frappe_manager.migration_manager.migration_executor import MigrationExecutor
from frappe_manager.site_manager.site import Bench
from frappe_manager.site_manager.workers_manager.SiteWorker import BenchWorkers
from frappe_manager.ssl_manager import SUPPORTED_SSL_TYPES
from frappe_manager.ssl_manager.certificate import SSLCertificate
from frappe_manager.ssl_manager.letsencrypt_certificate import LetsencryptSSLCertificate
from frappe_manager.utils.callbacks import (
    apps_list_validation_callback,
    create_command_sitename_callback,
    frappe_branch_validation_callback,
    sites_autocompletion_callback,
    version_callback,
    sitename_callback,
    code_command_extensions_callback,
)
from frappe_manager.utils.helpers import (
    format_ssl_certificate_time_remaining,
    is_cli_help_called,
    get_current_fm_version,
    pull_docker_images,
)
from frappe_manager.services_manager.commands import services_root_command
from frappe_manager.sub_commands.self_commands import self_app
from frappe_manager.sub_commands.ssl_command import ssl_root_command
from frappe_manager.metadata_manager import FMConfigManager
from frappe_manager.site_manager.bench_config import BenchConfig, FMBenchEnvType
from frappe_manager.migration_manager.version import Version
from frappe_manager.compose_manager.ComposeFile import ComposeFile
from email_validator import validate_email

app = typer.Typer(no_args_is_help=True, rich_markup_mode="rich")
app.add_typer(services_root_command, name="services", help="Handle global services.")
app.add_typer(self_app, name="self", help="Perform operations related to the [bold][blue]fm[/bold][/blue] itself.")
app.add_typer(ssl_root_command, name="ssl", help="Perform operations related to ssl.")


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
        first_time_install = False

        richprint.start(f"Working")

        if not CLI_DIR.exists():
            # creating the sites dir
            # TODO check if it's writeable and readable -> by writing a file to it and catching exception
            CLI_DIR.mkdir(parents=True, exist_ok=True)
            CLI_BENCHES_DIRECTORY.mkdir(parents=True, exist_ok=True)
            richprint.print(f"fm directory doesn't exists! Created at -> {str(CLI_DIR)}")
            first_time_install = True
        else:
            if not CLI_DIR.is_dir():
                richprint.exit("Sites directory is not a directory! Aborting!")

        # logging
        global logger
        logger = log.get_logger()
        logger.info("")
        logger.info(f"{':'*20}FM Invoked{':'*20}")
        logger.info("")

        # logging command provided by user
        logger.info(f"RUNNING COMMAND: {' '.join(sys.argv[1:])}")
        logger.info("-" * 20)

        # check docker daemon service
        if not DockerClient().server_running():
            richprint.exit("Docker daemon not running. Please start docker service.")

        fm_config_manager: FMConfigManager = FMConfigManager.import_from_toml()

        # docker pull
        if first_time_install:
            if not fm_config_manager.root_path.exists():
                richprint.print('🔍 It seems like the first installation. Pulling images... 🖼️')
                completed_status = pull_docker_images()
                if not completed_status:
                    shutil.rmtree(CLI_DIR)
                    richprint.exit("Aborting. [bold][blue]fm[/blue][/bold] will not be able to work without images. 🖼️")

                    current_version = Version(get_current_fm_version())
                    fm_config_manager.version = current_version
                    fm_config_manager.export_to_toml()

        migrations = MigrationExecutor(fm_config_manager)
        migration_status = migrations.execute()
        if not migration_status:
            richprint.exit(f"Rollbacked to previous version of fm {migrations.prev_version}.")

        services_manager: ServicesManager = ServicesManager(verbose=verbose)
        services_manager.init()
        try:
            services_manager.entrypoint_checks()
        except ServicesNotCreated as e:
            services_manager.remove_itself()
            richprint.exit(f"Not able to create services. {e}")

        if not services_manager.compose_project.running:
            services_manager.are_ports_free()
            services_manager.compose_project.start_service()

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
        FMBenchEnvType, typer.Option("--environment", "--env", help="Select bench environment type.")
    ] = FMBenchEnvType.dev,
    developer_mode: Annotated[
        bool, typer.Option(help="Enable developer mode. Defaults to enable in dev environment.")
    ] = False,
    frappe_branch: Annotated[
        str, typer.Option(help="Specify the branch name for frappe app", callback=frappe_branch_validation_callback)
    ] = "version-15",
    template: Annotated[bool, typer.Option(help="Create template bench.")] = False,
    admin_pass: Annotated[
        str,
        typer.Option(
            help="Default Password for the standard 'Administrator' User. This will be used as the password for the Administrator User for all new bench."
        ),
    ] = "admin",
    ssl: Annotated[
        SUPPORTED_SSL_TYPES, typer.Option(help="Enable https", show_default=False)
    ] = SUPPORTED_SSL_TYPES.none,
):
    # TODO Create markdown table for the below help
    """
    Create a new bench.

    Frappe\[version-15] will be installed by default.

    [bold white on black]Examples:[/bold white on black]

    [bold]# Install frappe\[version-15][/bold]
    $ [blue]fm create example[/blue]

    [bold]# Install frappe\[develop][/bold]
    $ [blue]fm create example --frappe-branch develop[/blue]

    [bold]# Install frappe\[version-15], erpnext\[version-15] and hrms\[version-15][/bold]
    $ [blue]fm create example --apps erpnext:version-15 --apps hrms:version-15[/blue]

    [bold]# Install frappe\[version-15], erpnext\[version-14] and hrms\[version-14][/bold]
    $ [blue]fm create example --frappe-branch version-14 --apps erpnext:version-14 --apps hrms:version-14[/blue]
    """

    services_manager: ServicesManager = ctx.obj["services"]
    fm_config_manager: FMConfigManager = ctx.obj["fm_config_manager"]
    verbose = ctx.obj['verbose']

    benches = BenchesManager(CLI_BENCHES_DIRECTORY, services=services_manager, verbose=verbose)
    benches.set_typer_context(ctx)

    bench_path = benches.root_path / benchname
    bench_config_path = bench_path / CLI_BENCH_CONFIG_FILE_NAME

    if ssl == SUPPORTED_SSL_TYPES.le:
        if fm_config_manager.le_email == 'dummy@fm.fm':
            email = richprint.prompt_ask(prompt='Please enter [bold][green]email[/bold][/green] for Let\'s Encrypt')
            validate_email(email, check_deliverability=False)
            fm_config_manager.le_email = email
            fm_config_manager.export_to_toml()
            richprint.print("Let's Encrypt email saved to configuration. It will be used automatically from now on.")
        else:
            richprint.print("Using Let's Encrypt email from configuration.")
            email = fm_config_manager.le_email
            fm_config_manager.export_to_toml()

        ssl_certificate = LetsencryptSSLCertificate(domain=benchname, ssl_type=ssl, email=email)

    elif ssl == SUPPORTED_SSL_TYPES.none:
        ssl_certificate = SSLCertificate(domain=benchname, ssl_type=ssl)

    bench_config: BenchConfig = BenchConfig(
        name=benchname,
        apps_list=apps,
        frappe_branch=frappe_branch,
        developer_mode=True if environment == FMBenchEnvType.dev else developer_mode,
        admin_tools=True if environment == FMBenchEnvType.dev else False,
        admin_pass=admin_pass,
        # TODO get this info from services, maybe ?
        mariadb_host=services_manager.database_manager.database_server_info.host,
        mariadb_root_pass='/run/secrets/db_root_password',
        environment_type=environment,
        root_path=bench_config_path,
        ssl=ssl_certificate,
    )

    compose_path = bench_path / 'docker-compose.yml'
    bench_workers = BenchWorkers(benchname, bench_path)
    compose_file_manager = ComposeFile(compose_path)
    compose_project = ComposeProject(compose_file_manager, verbose)

    bench: Bench = Bench(bench_path, benchname, bench_config, compose_project, bench_workers, services_manager)
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
        bench_workers = BenchWorkers(benchname, bench_path)

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
                mariadb_host='global-db',
                mariadb_root_pass="/run/secrets/db_root_password",
                environment_type=FMBenchEnvType.dev,
                ssl=SSLCertificate(domain=benchname, ssl_type=SUPPORTED_SSL_TYPES.none),
                root_path=bench_config_path,
            )
            bench = Bench(
                bench_path,
                benchname,
                fake_config,
                bench_compose_project,
                bench_workers,
                services=services_manager,
                workers_check=False,
            )

        else:
            bench = Bench.get_object(benchname, services_manager, workers_check=False, admin_tools_check=False)

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
):
    """Start a bench."""

    services_manager = ctx.obj["services"]
    verbose = ctx.obj['verbose']
    bench = Bench.get_object(benchname, services_manager)
    bench.start(force=force)


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
        Optional[SiteServicesEnum], typer.Option(help="Specify service name to show container logs.")
    ] = None,
    follow: Annotated[bool, typer.Option("--follow", "-f", help="Follow logs.")] = False,
):
    """Show frappe dev server logs or container logs for a given bench."""
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
    user: Annotated[Optional[str], typer.Option(help="Connect as this user.")] = None,
    service: Annotated[SiteServicesEnum, typer.Option(help="Specify Service")] = SiteServicesEnum.frappe,
):
    """Open shell for the give bench."""

    services_manager = ctx.obj["services"]
    verbose = ctx.obj['verbose']
    bench = Bench.get_object(benchname,services_manager)
    bench.shell(SiteServicesEnum(service).value, user)


@app.command()
def info(
    ctx: typer.Context,
    benchname: Annotated[Optional[str], typer.Argument(help="Name of the bench.", autocompletion=sites_autocompletion_callback, callback=sitename_callback)] = None,
):
    """Shows information about given bench."""

    services_manager = ctx.obj["services"]
    verbose = ctx.obj['verbose']
    bench = Bench.get_object(benchname,services_manager)
    bench.info()


@app.command()
def update(
    ctx: typer.Context,
    benchname: Annotated[Optional[str], typer.Argument(help="Name of the bench.", autocompletion=sites_autocompletion_callback, callback=sitename_callback)] = None,
    ssl: Annotated[Optional[SUPPORTED_SSL_TYPES], typer.Option(help="Enable https",show_default=False)] = None
):
    """Shows information about given bench."""

    services_manager = ctx.obj["services"]
    bench = Bench.get_object(benchname,services_manager)

    if ssl:
        if ssl == SUPPORTED_SSL_TYPES.le:
            richprint.stop()
            email = Prompt.ask("Please enter [bold][green]email[/bold][/green] for Let\'s Encrypt")
            validate_email(email,check_deliverability=False)
            ssl_certificate = LetsencryptSSLCertificate(domain=benchname,ssl_type=ssl,email=email)
            richprint.start('Working')
            bench.bench_config.ssl = ssl_certificate
            bench.create_certificate(bench.get_certificate_manager())
            bench.save_bench_config()

        elif ssl == SUPPORTED_SSL_TYPES.none:
            bench.remove_certificate(bench.get_certificate_manager())
