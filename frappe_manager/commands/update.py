import typer
from typing import Annotated, Optional
from frappe_manager import EnableDisableOptionsEnum
from frappe_manager.ssl_manager import SUPPORTED_SSL_TYPES, LETSENCRYPT_PREFERRED_CHALLENGE
from frappe_manager.site_manager.bench import Bench
from frappe_manager.site_manager.site_exceptions import BenchNotRunning
from frappe_manager.ssl_manager.certificate import SSLCertificate
from frappe_manager.ssl_manager.letsencrypt_certificate import LetsencryptSSLCertificate
from frappe_manager.utils.callbacks import sites_autocompletion_callback, sitename_callback
from frappe_manager.utils.helpers import format_ssl_certificate_time_remaining
from frappe_manager.display_manager.DisplayManager import richprint
from email_validator import validate_email
from frappe_manager.site_manager.bench_config import FMBenchEnvType

from frappe_manager.commands import app

def handle_ssl_site_selection(bench, site_name, benchname, ssl_type):
    """Handle site selection for SSL operations with better UX"""
    if not site_name:
        if len(bench.sites) == 0:
            richprint.exit(f"No sites found in bench {benchname}")
        elif len(bench.sites) == 1:
            site_name = list(bench.sites.keys())[0]
            richprint.print(f"Applying SSL to site: {site_name}")
        else:
            # Multi-site: show options and require selection
            richprint.print(f"SSL operations require site selection. Available sites in {benchname}:")
            for site in bench.sites.keys():
                marker = " (default)" if site == bench.get_default_site().name else ""
                current_ssl = "SSL enabled" if bench.has_certificate(site) else "No SSL"
                richprint.print(f"  - {site}{marker} ({current_ssl})")
            richprint.exit("Please specify --site <sitename> for SSL operations")
    
    return site_name

@app.command()
def update(
    ctx: typer.Context,
    benchname: Annotated[
        Optional[str],
        typer.Argument(
            help="Name of the bench.", autocompletion=sites_autocompletion_callback, callback=sitename_callback
        ),
    ] = None,
    site_name: Annotated[
        Optional[str],
        typer.Option("--site", help="Site name for SSL operations (uses default site if not specified)", show_default=False),
    ] = None,
    ssl: Annotated[Optional[SUPPORTED_SSL_TYPES], typer.Option(help="Enable SSL.", show_default=False)] = None,
    admin_tools: Annotated[
        Optional[EnableDisableOptionsEnum],
        typer.Option("--admin-tools", help="Toggle admin-tools.", show_default=False),
    ] = None,
    default_site: Annotated[
        Optional[str],
        typer.Option("--default-site", help="Set default site for the bench", show_default=False),
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
    fm_config_manager = ctx.obj["fm_config_manager"]

    bench_config_save = False

    if not bench.compose_project.running:
        raise BenchNotRunning(bench_name=bench.name)

    # Handle default site setting
    if default_site:
        if default_site not in bench.sites:
            richprint.exit(f"Site {default_site} not found in bench {benchname}")
        
        current_default = bench.get_default_site()
        if current_default and current_default.name == default_site:
            richprint.print(f"Site {default_site} is already the default site")
        else:
            bench.set_default_site(default_site)
            richprint.print(f"Set {default_site} as default site for bench {benchname}")
            bench_config_save = True

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
        # Use enhanced site selection for SSL
        site_name = handle_ssl_site_selection(bench, site_name, benchname, ssl)
        
        site = bench.get_site(site_name)
        if not site:
            richprint.exit(f"Site {site_name} not found in bench {benchname}")

        # Show current SSL status
        if bench.has_certificate(site_name):
            richprint.warning(f"Site {site_name} already has SSL certificate. This will replace it.")

        new_ssl_certificate = SSLCertificate(domain=site_name, ssl_type=SUPPORTED_SSL_TYPES.none)

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
                domain=site.name,
                ssl_type=ssl,
                email=email,
                preferred_challenge=letsencrypt_preferred_challenge,
                api_key=fm_config_manager.letsencrypt.api_key,
                api_token=fm_config_manager.letsencrypt.api_token,
            )

        richprint.print("Updating Certificate.")
        bench.update_certificate(new_ssl_certificate, site_name)
        richprint.print("Certificate Updated.")

        if bench.has_certificate(site_name):
            richprint.print(
                f"SSL Certificate will expire in {format_ssl_certificate_time_remaining(site.get_certificate_expiry())}"
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
