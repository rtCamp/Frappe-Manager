from typing import Annotated, List, Optional

from email_validator import validate_email
import typer

from frappe_manager import (
    CLI_BENCHES_DIRECTORY,
    CLI_BENCH_CONFIG_FILE_NAME,
    EnableDisableOptionsEnum,
)
from frappe_manager.compose_manager.ComposeFile import ComposeFile
from frappe_manager.compose_project.compose_project import ComposeProject
from frappe_manager.display_manager.DisplayManager import richprint
from frappe_manager.metadata_manager import FMConfigManager
from frappe_manager.services_manager.services import ServicesManager
from frappe_manager.site_manager.SiteManager import BenchesManager
from frappe_manager.site_manager.bench_config import BenchConfig, FMBenchEnvType
from frappe_manager.site_manager.site import Bench
from frappe_manager.ssl_manager import LETSENCRYPT_PREFERRED_CHALLENGE, SUPPORTED_SSL_TYPES
from frappe_manager.ssl_manager.certificate import SSLCertificate
from frappe_manager.ssl_manager.letsencrypt_certificate import LetsencryptSSLCertificate
from frappe_manager.utils.callbacks import (
    apps_list_validation_callback,
    create_command_sitename_callback,
    frappe_branch_validation_callback,
)
from frappe_manager.commands import app


@app.command()
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
