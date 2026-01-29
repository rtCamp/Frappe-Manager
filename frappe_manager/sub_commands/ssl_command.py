import typer
from datetime import datetime
from typing import Annotated, Optional
from frappe_manager import CLI_BENCHES_DIRECTORY, SSL_RENEW_BEFORE_DAYS
from frappe_manager.site_manager.bench_service import BenchService
from frappe_manager.site_manager.site import Bench
from frappe_manager.site_manager.bench_config import DNSProviderConfig
from frappe_manager.site_manager.exceptions import BenchSSLCertificateNotIssued
from frappe_manager.ssl_manager import LETSENCRYPT_PREFERRED_CHALLENGE, SUPPORTED_SSL_TYPES, DNS_PROVIDER
from frappe_manager.ssl_manager.certificate import SSLCertificate
from frappe_manager.ssl_manager.letsencrypt_certificate import LetsencryptSSLCertificate, CustomDomainCertificate
from frappe_manager.ssl_manager.certificate_exceptions import (
    SSLCertificateNotDueForRenewalError,
    SSLCertificateNotFoundError,
)
from frappe_manager.ssl_manager.external_domain_manager import ExternalDomainConfigManager, ExternalDomainConfig
from frappe_manager.ssl_manager.ssl_certificate_manager import SSLCertificateManager
from frappe_manager.ssl_manager.certificate_link_manager import CertificateLinkManager
from frappe_manager.ssl_manager.storage_config import SSLStorageConfig
from frappe_manager.ssl_manager.service_factory import create_certificate_service
from frappe_manager.ssl_manager.standalone_nginx_config_manager import StandaloneNginxConfigManager
from frappe_manager.utils.callbacks import sitename_callback, sites_autocompletion_callback, prompt_for_bench_selection
from frappe_manager.display_manager.DisplayManager import richprint
from frappe_manager.exceptions import SSLCertificateError
from frappe_manager.logger.context import LoggerContext
from frappe_manager.logger.contextual import ContextualLogger
from frappe_manager.logger import log
from frappe_manager.output_manager import OutputHandler, temporary_stop, spinner
from frappe_manager.output_manager.logging_output import LoggingOutputHandler
from frappe_manager.output_manager.rich_output import RichOutputHandler
from frappe_manager.metadata_manager import FMConfigManager, FMCloudflareConfig
from rich.table import Table

ssl_root_command = typer.Typer(no_args_is_help=True, rich_markup_mode="rich")
dns_config_command = typer.Typer(
    no_args_is_help=True, rich_markup_mode="rich", help="Configure DNS provider credentials for DNS-01 challenge"
)


def get_output_handler(ctx: typer.Context, context: Optional[LoggerContext] = None) -> OutputHandler:
    """
    Get the appropriate output handler based on verbose flag.

    Args:
        ctx: Typer context containing verbose flag
        context: Optional logger context for structured logging

    Returns:
        LoggingOutputHandler wrapping RichOutputHandler with contextual logging
    """
    verbose = ctx.obj.get('verbose', False)

    rich = RichOutputHandler(verbose=verbose)

    base_logger = log.get_logger()

    # Wrap with context (empty context if not provided)
    contextual_logger = ContextualLogger(base_logger, context)

    # Wrap with logging for automatic file logging
    output = LoggingOutputHandler(rich, contextual_logger)

    return output


@ssl_root_command.command()
def renew(
    ctx: typer.Context,
    benchname: Annotated[
        Optional[str],
        typer.Argument(
            help="Name of the bench (omit for standalone mode).", autocompletion=sites_autocompletion_callback
        ),
    ] = None,
    domain: Annotated[
        Optional[str],
        typer.Argument(help="Specific domain to renew. If omitted, renews all certificates for the bench/standalone."),
    ] = None,
    all: Annotated[bool, typer.Option(help="Renew ssl cert for all benches.")] = False,
    standalone: Annotated[bool, typer.Option("--standalone", help="Renew certificates for external domains")] = False,
    dry_run: Annotated[
        bool,
        typer.Option("--dry-run", help="Test renewal using Let's Encrypt staging server without modifying the system."),
    ] = False,
    force: Annotated[
        bool,
        typer.Option("--force", "-f", help="Force renewal even if certificate is not due for renewal."),
    ] = False,
):
    """
    Renew SSL certificates.

    Supports both bench mode (default) and standalone mode for external domains.
    Use --dry-run to test with Let's Encrypt staging server.
    Use --force to renew certificates regardless of expiry date.
    """

    if standalone:
        if all:
            _renew_all_external_certificates(ctx, dry_run, force)
        else:
            actual_domain = domain if domain else benchname
            if not actual_domain:
                context = LoggerContext(operation="ssl-renew-external")
                output = get_output_handler(ctx, context=context)
                output.display_error("Domain required for standalone renewal")
                with temporary_stop(output):
                    typer.echo(ctx.get_help())
                raise typer.Exit(1)
            _renew_external_certificate(ctx, actual_domain, dry_run, force)
    else:
        # Existing bench renewal logic
        services_manager = ctx.obj["services"]
        bench_service = BenchService(CLI_BENCHES_DIRECTORY, services_manager)

        if all:
            sites_list = bench_service.get_bench_names()
        else:
            benchname = prompt_for_bench_selection(benchname)

            if not benchname:
                context = LoggerContext(operation="ssl-renew")
                output = get_output_handler(ctx, context=context)
                output.display_error("Benchname required in bench mode")
                with temporary_stop(output):
                    typer.echo(ctx.get_help())
                raise typer.Exit(1)
            sites_list = [benchname]

        for benchname in sites_list:
            context = LoggerContext(bench=benchname, operation="ssl-renew")
            output = get_output_handler(ctx, context=context)
            bench = Bench.get_object(benchname, services_manager, output_handler=output)

            output.change_head(f"Renew certificate for {benchname}")
            try:
                if domain:
                    cert_domains = [cert.domain for cert in bench.certificate_manager.certificates]
                    if domain not in cert_domains:
                        output.display_error(
                            f"No SSL certificate found for domain '{domain}'.\n"
                            f"Configured certificates: {', '.join(cert_domains) if cert_domains else 'None'}\n"
                            f"To add a certificate, use: fm ssl add {benchname} {domain}"
                        )
                        raise typer.Exit(1)

                    # Renew specific domain certificate
                    with spinner(output, f"Renewing certificate for {domain}"):
                        bench.ssl.renew_certificate(domain, dry_run=dry_run, force=force)
                    if not dry_run:
                        output.print(f"Certificate renewed for {domain}", emoji_code=":white_check_mark:")
                else:
                    # Renew all certificates for the bench
                    with spinner(output, f"Renewing certificates for {benchname}"):
                        bench.ssl.renew_all_certificates(dry_run=dry_run, force=force)
            except (BenchSSLCertificateNotIssued, SSLCertificateNotDueForRenewalError) as e:
                output.warning(e.message)

            except Exception as e:
                output.warning(str(e))


@ssl_root_command.command(name="list")
def list_certificates(
    ctx: typer.Context,
    benchname: Annotated[
        Optional[str],
        typer.Argument(
            help="Name of the bench (omit for standalone mode).", autocompletion=sites_autocompletion_callback
        ),
    ] = None,
    standalone: Annotated[
        bool,
        typer.Option("--standalone", help="List certificates for external (non-bench) domains"),
    ] = False,
    all: Annotated[
        bool,
        typer.Option("--all", help="List all certificates (bench + external)"),
    ] = False,
):
    """
    List SSL certificates.

    List certificates for a specific bench, external domains, or all certificates.
    """

    if all:
        _list_all_certificates(ctx)
    elif standalone:
        _list_external_certificates(ctx)
    else:
        benchname = prompt_for_bench_selection(benchname)

        if not benchname:
            context = LoggerContext(operation="ssl-list")
            output = get_output_handler(ctx, context=context)
            output.display_error("Benchname required in bench mode")
            with temporary_stop(output):
                typer.echo(ctx.get_help())
            raise typer.Exit(1)

        _list_bench_certificates(ctx, benchname)


@ssl_root_command.command(name="add")
def add_certificate(
    ctx: typer.Context,
    benchname: Annotated[
        Optional[str],
        typer.Argument(
            help="Name of the bench (omit for standalone mode).", autocompletion=sites_autocompletion_callback
        ),
    ] = None,
    domain: Annotated[Optional[str], typer.Argument(help="Domain name for the certificate")] = None,
    challenge: Annotated[
        LETSENCRYPT_PREFERRED_CHALLENGE,
        typer.Option("--challenge", "-c", help="Challenge type"),
    ] = LETSENCRYPT_PREFERRED_CHALLENGE.http01,
    cname: Annotated[
        Optional[str],
        typer.Option("--cname", help="CNAME delegation record for DNS-01 challenge (requires dns01)"),
    ] = None,
    dry_run: Annotated[
        bool,
        typer.Option(
            "--dry-run",
            help="Test certificate generation using Let's Encrypt staging server without adding it to the system.",
        ),
    ] = False,
    standalone: Annotated[
        bool,
        typer.Option(
            "--standalone",
            help="Manage SSL for external (non-bench) Docker project. Use with docker network 'fm-global-frontend-network'.",
        ),
    ] = False,
    skip_dns_check: Annotated[
        bool,
        typer.Option(
            "--skip-dns-check",
            help="Skip DNS validation before certificate generation (use if DNS will be configured later).",
        ),
    ] = False,
    wait_for_dns: Annotated[
        bool,
        typer.Option(
            "--wait-for-dns",
            help="Wait for DNS propagation (polls every 30s for up to 5 minutes).",
        ),
    ] = False,
):
    """
    Add SSL certificate for a domain.

    Supports both bench mode (default) and standalone mode for external Docker projects.
    Standalone mode allows managing SSL for any Docker project using FM's nginx-proxy.

    Use --dry-run to test certificate generation with Let's Encrypt staging server
    before committing to production. This validates DNS/HTTP configuration without
    rate limits or system modifications.
    """

    if standalone:
        # Standalone mode: domain can be first arg (as benchname) or second arg
        actual_domain = domain if domain else benchname

        if not actual_domain:
            context = LoggerContext(operation="ssl-add-external")
            output = get_output_handler(ctx, context=context)
            output.display_error("Domain is required in standalone mode")
            with temporary_stop(output):
                typer.echo(ctx.get_help())
            raise typer.Exit(1)

        if benchname and domain:
            context = LoggerContext(operation="ssl-add-external")
            output = get_output_handler(ctx, context=context)
            output.display_error("Cannot specify both benchname and domain in standalone mode")
            with temporary_stop(output):
                typer.echo(ctx.get_help())
            raise typer.Exit(1)

        _add_external_certificate(ctx, actual_domain, challenge, cname, dry_run, skip_dns_check, wait_for_dns)
    else:
        benchname = prompt_for_bench_selection(benchname)

        if not benchname or not domain:
            context = LoggerContext(operation="ssl-add")
            output = get_output_handler(ctx, context=context)
            output.display_error("Both benchname and domain are required in bench mode")
            with temporary_stop(output):
                typer.echo(ctx.get_help())
            raise typer.Exit(1)

        _add_bench_certificate(ctx, benchname, domain, challenge, cname, dry_run)


def _add_bench_certificate(
    ctx: typer.Context,
    benchname: str,
    domain: str,
    challenge: LETSENCRYPT_PREFERRED_CHALLENGE,
    cname: Optional[str],
    dry_run: bool,
):
    """Add SSL certificate for a bench domain (existing logic extracted)."""

    services_manager = ctx.obj["services"]

    context = LoggerContext(bench=benchname, operation="ssl-add")
    output = get_output_handler(ctx, context=context)
    bench = Bench.get_object(benchname, services_manager, output_handler=output)

    allowed_domains = bench.bench_config.get_all_domains()
    if domain not in allowed_domains:
        output.display_error(
            f"Domain '{domain}' is not configured for bench '{benchname}'.\n"
            f"Allowed domains: {', '.join(allowed_domains)}\n"
            f"To add an alias domain, use: fm update {benchname} --add-alias {domain}"
        )
        raise typer.Exit(1)

    if cname and challenge != LETSENCRYPT_PREFERRED_CHALLENGE.dns01:
        output.display_error("CNAME delegation (--cname) can only be used with DNS-01 challenge")
        raise typer.Exit(1)

    output.change_head(f"Adding SSL certificate for {domain}")

    try:
        if cname:
            cert = CustomDomainCertificate(
                domain=domain,
                ssl_type=SUPPORTED_SSL_TYPES.le,
                api_token=None,
                api_key=None,
                challenge_type=challenge,
                delegation_cname=cname,
            )
            output.print(f"Using CNAME delegation: {cname}", emoji_code=":information:")
        else:
            cert = LetsencryptSSLCertificate(
                domain=domain,
                ssl_type=SUPPORTED_SSL_TYPES.le,
                api_token=None,
                api_key=None,
                challenge_type=challenge,
            )

        with spinner(output, f"Adding SSL certificate for {domain}"):
            bench.certificate_manager.add_certificate(cert, dry_run=dry_run)

        if not dry_run:
            output.print(f"SSL certificate added for {domain}", emoji_code=":white_check_mark:")
            output.print("Certificate has been issued and configured.", emoji_code=":zap:")

    except ValueError as e:
        output.display_error(f"Failed to add certificate: {e}")
        raise typer.Exit(1)
    except Exception as e:
        output.display_error(f"Failed to add certificate: {e}")
        output.display_error(f"Error details: {str(e)}")
        raise typer.Exit(1)


def _add_external_certificate(
    ctx: typer.Context,
    domain: str,
    challenge: LETSENCRYPT_PREFERRED_CHALLENGE,
    cname: Optional[str],
    dry_run: bool,
    skip_dns_check: bool = False,
    wait_for_dns: bool = False,
):
    """Add SSL certificate for external (non-bench) domain."""

    services_manager = ctx.obj["services"]
    context = LoggerContext(operation="ssl-add-external")
    output = get_output_handler(ctx, context=context)

    external_config_path = services_manager.path / "nginx-proxy" / "external_domains.toml"
    external_manager = ExternalDomainConfigManager(external_config_path)

    if external_manager.domain_exists(domain):
        output.display_error(f"Certificate already exists for external domain '{domain}'")
        output.print("To update certificate:", emoji_code="")
        output.print(f"  1. Remove existing: fm ssl remove --standalone {domain}", emoji_code="")
        output.print(f"  2. Add new: fm ssl add --standalone {domain}", emoji_code="")
        raise typer.Exit(1)

    if cname and challenge != LETSENCRYPT_PREFERRED_CHALLENGE.dns01:
        output.display_error("CNAME delegation (--cname) requires DNS-01 challenge")
        with temporary_stop(output):
            typer.echo(ctx.get_help())
        raise typer.Exit(1)

    output.change_head(f"Adding SSL certificate for {domain} (standalone mode)")

    try:
        if cname:
            cert = CustomDomainCertificate(
                domain=domain,
                ssl_type=SUPPORTED_SSL_TYPES.le,
                api_token=None,
                api_key=None,
                challenge_type=challenge,
                delegation_cname=cname,
            )
            output.print(f"Using CNAME delegation: {cname}", emoji_code=":information:")

            # Validate CNAME before proceeding (unless skipped)
            if not skip_dns_check:
                from frappe_manager.ssl_manager.dns_validator import DNSValidator

                output.change_head(f"Validating DNS configuration for {domain}")
                validator = DNSValidator(output_handler=output)

                # If wait_for_dns is True, poll for propagation
                if wait_for_dns:
                    propagation = validator.wait_for_cname_propagation(
                        domain=domain,
                        challenge_alias=cname,
                        timeout=300,  # 5 minutes
                        check_interval=30,  # 30 seconds
                    )

                    if not propagation.propagated:
                        output.display_error("DNS propagation timeout")
                        output.print("", emoji_code="")
                        output.print(f"CNAME record did not propagate within 5 minutes.", emoji_code="")
                        output.print(f"Expected CNAME:", emoji_code="")
                        output.print(f"  _acme-challenge.{domain}  →  _acme-challenge.{cname}", emoji_code="")
                        output.print("", emoji_code="")
                        output.print("Please verify your DNS configuration and try again.", emoji_code="")
                        raise typer.Exit(1)

                    output.print(f"✓ {propagation.message}", emoji_code=":white_check_mark:")
                else:
                    # Single validation check (no waiting)
                    validation = validator.validate_cname_for_acme(domain, cname)

                    if not validation.valid:
                        output.display_error("DNS validation failed")
                        output.print("", emoji_code="")
                        output.print(f"Domain: {domain}", emoji_code="")
                        output.print(f"Expected CNAME:", emoji_code="")
                        output.print(f"  _acme-challenge.{domain}  →  _acme-challenge.{cname}", emoji_code="")

                        if validation.actual_value:
                            output.print("", emoji_code="")
                            output.print(f"Current CNAME:", emoji_code="")
                            output.print(f"  _acme-challenge.{domain}  →  {validation.actual_value}", emoji_code="")
                            output.print("", emoji_code="")
                            output.print("The CNAME record exists but points to the wrong target.", emoji_code="")
                        else:
                            output.print("", emoji_code="")
                            output.print("CNAME record not found.", emoji_code="")

                        output.print("", emoji_code="")
                        output.print("Please update your DNS to match the expected value above.", emoji_code="")
                        output.print("DNS changes may take up to 5 minutes to propagate.", emoji_code="")
                        output.print("", emoji_code="")
                        output.print("To skip this check, use: --skip-dns-check", emoji_code="")
                        output.print("To wait for propagation, use: --wait-for-dns", emoji_code="")
                        raise typer.Exit(1)

                    output.print("✓ CNAME record verified", emoji_code=":white_check_mark:")
        else:
            cert = LetsencryptSSLCertificate(
                domain=domain,
                ssl_type=SUPPORTED_SSL_TYPES.le,
                api_token=None,
                api_key=None,
                challenge_type=challenge,
            )

            if not skip_dns_check and challenge == LETSENCRYPT_PREFERRED_CHALLENGE.http01:
                from frappe_manager.ssl_manager.dns_validator import DNSValidator

                output.change_head(f"Checking DNS configuration for {domain}")
                validator = DNSValidator(output_handler=output)
                validation = validator.validate_a_record(domain)

                if validation.valid:
                    output.print(f"✓ Domain resolves to {validation.actual_value}", emoji_code=":white_check_mark:")
                else:
                    output.warning(f"Domain {domain} doesn't have an A record")
                    output.print(f"HTTP-01 challenge may fail if DNS is not configured correctly.", emoji_code="")
                    output.print(f"Make sure {domain} points to this server's IP address.", emoji_code="")

        global_proxy_storage = services_manager.proxy_storage

        storage_config = SSLStorageConfig(
            ssl_dir=global_proxy_storage.dirs.ssl.host,
            ssl_dir_container=global_proxy_storage.dirs.ssl.container,
            certs_dir=global_proxy_storage.dirs.certs.host,
            certs_dir_container=global_proxy_storage.dirs.certs.container,
            vhostd_dir=global_proxy_storage.dirs.vhostd.host,
            webroot_dir=global_proxy_storage.dirs.html.host,  # For HTTP-01 challenge
        )

        link_manager = CertificateLinkManager(storage_config)
        nginx_controller = services_manager.nginx_controller

        standalone_nginx = StandaloneNginxConfigManager(
            conf_dir=global_proxy_storage.dirs.confd.host,
            webroot_dir_container=global_proxy_storage.dirs.html.container,
            certs_dir_container=global_proxy_storage.dirs.certs.container,
        )

        # Step 1: Create HTTP-only nginx config (for ACME challenge)
        output.change_head(f"Setting up nginx configuration for {domain}")
        standalone_nginx.create_http_config(domain)
        output.print("Created HTTP configuration for ACME challenge", emoji_code=":white_check_mark:")

        # Step 2: Reload nginx to apply the config
        output.change_head("Reloading nginx to apply configuration")
        nginx_controller.reload()
        output.print("Nginx reloaded successfully", emoji_code=":white_check_mark:")

        def certificate_service_factory(cert, storage_cfg, output_handler):
            return create_certificate_service(cert, storage_cfg, output_handler)

        cert_manager = SSLCertificateManager(
            certificates=[],  # Start with empty list, we'll add the cert next
            service_factory=certificate_service_factory,
            link_manager=link_manager,
            nginx_controller=nginx_controller,
            storage_config=storage_config,
            output_handler=output,
            config_save_callback=None,  # No bench config callback
        )

        # Step 3: Generate certificate (HTTP-01 challenge will now work)
        try:
            with spinner(output, f"Generating SSL certificate for {domain}"):
                cert_manager.add_certificate(cert, dry_run=dry_run)
        except Exception as cert_error:
            # Certificate generation failed - clean up nginx config
            output.change_head("Cleaning up after certificate generation failure")
            try:
                standalone_nginx.remove_config(domain)
                nginx_controller.reload()
                output.print("Cleaned up nginx configuration", emoji_code=":white_check_mark:")
            except Exception as cleanup_error:
                output.debug(f"Failed to clean up nginx config: {cleanup_error}")
            # Re-raise the original certificate error
            raise cert_error

        # Step 4: Update nginx config to enable HTTPS
        output.change_head("Enabling HTTPS for {domain}")
        standalone_nginx.create_https_config(domain)
        output.print("Created HTTPS configuration", emoji_code=":white_check_mark:")

        # Step 5: Reload nginx again to enable HTTPS
        nginx_controller.reload()
        output.print("Nginx reloaded with HTTPS enabled", emoji_code=":white_check_mark:")

        if not dry_run:
            # Save to external domains config
            external_manager.add_domain(
                ExternalDomainConfig(
                    domain=domain,
                    ssl_type="letsencrypt",
                    # Email removed - Let's Encrypt discontinued notifications (June 2025)
                    added_at=datetime.now().isoformat(),
                    challenge_type=challenge.lower(),
                    delegation_cname=cname,
                    acme_client="acme.sh",
                )
            )

            output.print(f"SSL certificate added for {domain}", emoji_code=":white_check_mark:")
            output.print("Certificate configured successfully", emoji_code=":zap:")
            output.print("", emoji_code="")
            output.print("[bold cyan]To use this certificate in your Docker project:[/bold cyan]", emoji_code="")
            output.print("", emoji_code="")
            output.print("1. Add to your docker-compose.yml:", emoji_code="")
            output.print("", emoji_code="")
            output.print("   services:", emoji_code="")
            output.print("     your-app:", emoji_code="")
            output.print("       environment:", emoji_code="")
            output.print(f"         VIRTUAL_HOST: {domain}", emoji_code="")
            output.print("         VIRTUAL_PORT: 80  # Your app's port", emoji_code="")
            output.print("       networks:", emoji_code="")
            output.print("         - fm-global-frontend-network", emoji_code="")
            output.print("", emoji_code="")
            output.print("   networks:", emoji_code="")
            output.print("     fm-global-frontend-network:", emoji_code="")
            output.print("       external: true", emoji_code="")
            output.print("", emoji_code="")
            output.print("2. Start your project:", emoji_code="")
            output.print("   docker compose up -d", emoji_code="")
            output.print("", emoji_code="")
            output.print(f"3. Access your app at: https://{domain}", emoji_code="")

    except ValueError as e:
        output.display_error(f"Failed to add certificate: {e}")
        raise typer.Exit(1)
    except Exception as e:
        output.display_error(f"Failed to add certificate: {e}")
        raise typer.Exit(1)


@ssl_root_command.command(name="remove")
def remove_certificate(
    ctx: typer.Context,
    benchname: Annotated[
        Optional[str],
        typer.Argument(
            help="Name of the bench (omit for standalone mode).", autocompletion=sites_autocompletion_callback
        ),
    ] = None,
    domain: Annotated[Optional[str], typer.Argument(help="Domain name of the certificate to remove")] = None,
    force: Annotated[
        bool,
        typer.Option("--force", "-f", help="Force removal without confirmation"),
    ] = False,
    standalone: Annotated[
        bool,
        typer.Option("--standalone", help="Remove certificate for external (non-bench) domain"),
    ] = False,
):
    """
    Remove SSL certificate for a domain.

    Supports both bench mode (default) and standalone mode for external domains.
    """

    if standalone:
        # Standalone mode: domain can be first arg (as benchname) or second arg
        actual_domain = domain if domain else benchname

        if not actual_domain:
            context = LoggerContext(operation="ssl-remove-external")
            output = get_output_handler(ctx, context=context)
            output.display_error("Domain is required in standalone mode")
            with temporary_stop(output):
                typer.echo(ctx.get_help())
            raise typer.Exit(1)

        _remove_external_certificate(ctx, actual_domain, force)
    else:
        benchname = prompt_for_bench_selection(benchname)

        if not benchname or not domain:
            context = LoggerContext(operation="ssl-remove")
            output = get_output_handler(ctx, context=context)
            output.display_error("Both benchname and domain are required in bench mode")
            with temporary_stop(output):
                typer.echo(ctx.get_help())
            raise typer.Exit(1)

        _remove_bench_certificate(ctx, benchname, domain, force)


def _remove_bench_certificate(ctx: typer.Context, benchname: str, domain: str, force: bool):
    """Remove SSL certificate for a bench domain (existing logic extracted)."""

    services_manager = ctx.obj["services"]

    context = LoggerContext(bench=benchname, operation="ssl-remove")
    output = get_output_handler(ctx, context=context)
    bench = Bench.get_object(benchname, services_manager, output_handler=output)

    cert_domains = [cert.domain for cert in bench.certificate_manager.certificates]
    if domain not in cert_domains:
        output.display_error(
            f"No SSL certificate found for domain '{domain}'.\n"
            f"Configured certificates: {', '.join(cert_domains) if cert_domains else 'None'}"
        )
        raise typer.Exit(1)

    # Confirm removal unless forced
    if not force:
        with temporary_stop(output):
            choice = output.prompt_ask(
                prompt=f"Remove SSL certificate for {domain}?", choices=["yes", "no"], default="no"
            )
        if choice != "yes":
            output.print("Cancelled.", emoji_code=":x:")
            raise typer.Exit(0)

    output.change_head(f"Removing SSL certificate for {domain}")

    try:
        with spinner(output, f"Removing SSL certificate for {domain}"):
            bench.certificate_manager.remove_certificate_by_domain(domain)

        output.print(f"SSL certificate removed for {domain}", emoji_code=":white_check_mark:")

    except SSLCertificateNotFoundError as e:
        output.display_error(f"Certificate not found: {e}")
        raise typer.Exit(1)
    except Exception as e:
        output.display_error(f"Failed to remove certificate: {e}")
        output.display_error(f"Error details: {str(e)}")
        raise typer.Exit(1)


def _remove_external_certificate(ctx: typer.Context, domain: str, force: bool):
    """Remove SSL certificate for external domain."""

    services_manager = ctx.obj["services"]
    context = LoggerContext(operation="ssl-remove-external")
    output = get_output_handler(ctx, context=context)

    external_config_path = services_manager.path / "nginx-proxy" / "external_domains.toml"
    external_manager = ExternalDomainConfigManager(external_config_path)

    if not external_manager.domain_exists(domain):
        output.display_error(f"No external certificate found for domain '{domain}'")
        output.print("To list external certificates: fm ssl list --standalone", emoji_code="")
        raise typer.Exit(1)

    # Confirm removal unless forced
    if not force:
        with temporary_stop(output):
            choice = output.prompt_ask(
                prompt=f"Remove SSL certificate for {domain}?", choices=["yes", "no"], default="no"
            )
        if choice != "yes":
            output.print("Cancelled.", emoji_code=":x:")
            raise typer.Exit(0)

    output.change_head(f"Removing SSL certificate for {domain}")

    try:
        cert_config = external_manager.get_domain(domain)
        if not cert_config:
            raise ValueError(f"Domain config not found for {domain}")

        cert = external_manager.to_ssl_certificate(domain)
        if not cert:
            raise ValueError(f"Could not create certificate object for {domain}")

        global_proxy_storage = services_manager.proxy_storage

        storage_config = SSLStorageConfig(
            ssl_dir=global_proxy_storage.dirs.ssl.host,
            ssl_dir_container=global_proxy_storage.dirs.ssl.container,
            certs_dir=global_proxy_storage.dirs.certs.host,
            certs_dir_container=global_proxy_storage.dirs.certs.container,
            vhostd_dir=global_proxy_storage.dirs.vhostd.host,
            webroot_dir=global_proxy_storage.dirs.html.host,
        )

        link_manager = CertificateLinkManager(storage_config)
        nginx_controller = services_manager.nginx_controller

        standalone_nginx = StandaloneNginxConfigManager(
            conf_dir=global_proxy_storage.dirs.confd.host,
            webroot_dir_container=global_proxy_storage.dirs.html.container,
            certs_dir_container=global_proxy_storage.dirs.certs.container,
        )

        def certificate_service_factory(cert, storage_cfg, output_handler):
            return create_certificate_service(cert, storage_cfg, output_handler)

        cert_manager = SSLCertificateManager(
            certificates=[cert],
            service_factory=certificate_service_factory,
            link_manager=link_manager,
            nginx_controller=nginx_controller,
            storage_config=storage_config,
            output_handler=output,
            config_save_callback=None,
        )

        # Remove certificate (removes symlinks, vhost.d, and cert files)
        with spinner(output, f"Removing SSL certificate for {domain}"):
            cert_manager.remove_certificate_by_domain(domain)

        # Remove standalone nginx configuration
        output.change_head(f"Removing nginx configuration for {domain}")
        standalone_nginx.remove_config(domain)
        output.print("Removed nginx configuration", emoji_code=":white_check_mark:")

        # Reload nginx
        nginx_controller.reload()
        output.print("Nginx reloaded", emoji_code=":white_check_mark:")

        # Remove from external domains config
        external_manager.remove_domain(domain)

        output.print(f"SSL certificate removed for {domain}", emoji_code=":white_check_mark:")

    except Exception as e:
        output.display_error(f"Failed to remove certificate: {e}")
        raise typer.Exit(1)


def _list_bench_certificates(ctx: typer.Context, benchname: str):
    """List all SSL certificates for a bench (existing logic extracted)."""

    services_manager = ctx.obj["services"]

    context = LoggerContext(bench=benchname, operation="ssl-list")
    output = get_output_handler(ctx, context=context)
    bench = Bench.get_object(benchname, services_manager, output_handler=output)

    all_domains = bench.bench_config.get_all_domains()

    certs = bench.certificate_manager.list_certificates()

    cert_map = {cert['domain']: cert for cert in certs}

    from rich.table import Table

    table = Table(show_header=True, header_style="bold magenta")
    table.add_column("Domain", style="cyan")
    table.add_column("Type", style="yellow")
    table.add_column("Challenge", style="magenta")
    table.add_column("Status", style="green")
    table.add_column("Expiry", style="blue")
    table.add_column("Days Left", justify="right")
    table.add_column("Renewal", style="red")

    # Show all domains, whether they have certificates or not
    for domain in all_domains:
        if domain in cert_map:
            # Domain has a certificate configured
            cert = cert_map[domain]
            ssl_type = cert['ssl_type']
            challenge_type = cert.get('challenge_type', 'N/A')
            status = "✅ Issued" if cert['exists'] else "❌ Not Issued"

            if cert['exists'] and cert['expiry_date']:
                expiry = cert['expiry_date'].strftime('%Y-%m-%d %H:%M')
                days_left = str(cert['days_until_expiry'])
                renewal = "⚠️ DUE" if cert['needs_renewal'] else "✓ OK"
            else:
                expiry = "N/A"
                days_left = "N/A"
                renewal = "N/A"
        else:
            # Domain has no certificate configured
            ssl_type = "none"
            challenge_type = "N/A"
            status = "⚪ No SSL"
            expiry = "N/A"
            days_left = "N/A"
            renewal = "N/A"

        table.add_row(domain, ssl_type, challenge_type, status, expiry, days_left, renewal)

    output.stop()
    output.print_data(table)


def _get_non_bench_domains_from_nginx(services_manager) -> list[str]:
    """
    Detect domains being proxied by nginx-proxy that are NOT Frappe benches.

    Returns list of domain names found in nginx config that:
    - Have active backends (VIRTUAL_HOST containers)
    - Are not managed by FM benches
    """
    try:
        nginx_container_name = services_manager.compose_file_manager.get_container_names().get("global-nginx-proxy")
        if not nginx_container_name:
            return []

        # Read default.conf which docker-gen generates
        import subprocess

        result = subprocess.run(
            ["docker", "exec", nginx_container_name, "cat", "/etc/nginx/conf.d/default.conf"],
            capture_output=True,
            text=True,
            timeout=10,
        )

        if result.returncode != 0:
            return []

        # Parse upstream blocks to find domains
        # Format: "# domain.com/"
        # Followed by: "upstream domain.com {"
        import re

        domain_pattern = r'^# (.+?)/$'

        detected_domains = set()
        for line in result.stdout.split('\n'):
            match = re.match(domain_pattern, line)
            if match:
                domain = match.group(1)
                detected_domains.add(domain)

        # Filter out bench domains
        bench_service = BenchService(CLI_BENCHES_DIRECTORY, services_manager)
        benches = bench_service.get_bench_names()

        bench_domains = set()
        for bench_name in benches:
            try:
                # Create silent output handler for internal bench detection
                from frappe_manager.output_manager.silent_output import SilentOutputHandler

                output = SilentOutputHandler()
                bench = Bench.get_object(bench_name, services_manager, output_handler=output)
                bench_domains.update(bench.bench_config.get_all_domains())
            except Exception:
                continue

        # Return only non-bench domains
        non_bench_domains = detected_domains - bench_domains
        return sorted(list(non_bench_domains))

    except Exception as e:
        # Silently fail if we can't detect domains
        return []


def _list_external_certificates(ctx: typer.Context):
    """List all external domain SSL certificates and detected non-SSL domains."""

    services_manager = ctx.obj["services"]
    context = LoggerContext(operation="ssl-list-external")
    output = get_output_handler(ctx, context=context)

    external_config_path = services_manager.path / "nginx-proxy" / "external_domains.toml"
    external_manager = ExternalDomainConfigManager(external_config_path)

    external_domains = external_manager.list_domains()

    detected_domains = _get_non_bench_domains_from_nginx(services_manager)

    # Filter out domains that already have SSL certificates
    external_domain_names = {d.domain for d in external_domains}
    non_ssl_domains = [d for d in detected_domains if d not in external_domain_names]

    if not external_domains and not non_ssl_domains:
        output.print("No external domains or SSL certificates configured", emoji_code=":information:")
        output.print("", emoji_code="")
        output.print("To add an external certificate:", emoji_code="")
        output.print("  fm ssl add --standalone <domain>", emoji_code="")
        return

    global_proxy_storage = services_manager.proxy_storage
    storage_config = SSLStorageConfig(
        ssl_dir=global_proxy_storage.dirs.ssl.host,
        ssl_dir_container=global_proxy_storage.dirs.ssl.container,
        certs_dir=global_proxy_storage.dirs.certs.host,
        certs_dir_container=global_proxy_storage.dirs.certs.container,
        vhostd_dir=global_proxy_storage.dirs.vhostd.host,
        webroot_dir=global_proxy_storage.dirs.html.host,
    )

    link_manager = CertificateLinkManager(storage_config)

    from rich.table import Table

    table = Table(title="External Domains & SSL Certificates", show_header=True, header_style="bold magenta")
    table.add_column("Domain", style="cyan")
    table.add_column("Type", style="yellow")
    table.add_column("Status", style="green")
    table.add_column("Expiry", style="blue")
    table.add_column("Days Left", justify="right")
    table.add_column("Renewal", style="red")

    # Add SSL-enabled domains
    for domain_config in external_domains:
        domain = domain_config.domain
        ssl_type = domain_config.ssl_type

        try:
            privkey_path, fullchain_path = link_manager.get_certificate_paths(domain)

            from frappe_manager.utils.helpers import get_certificate_expiry_date

            expiry_date = get_certificate_expiry_date(fullchain_path)
            if expiry_date:
                expiry = expiry_date.strftime('%Y-%m-%d %H:%M')
                # Make datetime.now() timezone-aware to match expiry_date
                from datetime import timezone

                now = datetime.now(timezone.utc)
                days_left = (expiry_date - now).days
                needs_renewal = days_left <= SSL_RENEW_BEFORE_DAYS
                renewal = "⚠️ DUE" if needs_renewal else "✓ OK"
                status = "✅ Issued"
            else:
                expiry = "N/A"
                days_left = "N/A"
                renewal = "N/A"
                status = "⚠️ Unknown"
        except Exception as e:
            output.debug(f"Error getting certificate status for {domain}: {e}")
            status = "❌ Missing"
            expiry = "N/A"
            days_left = "N/A"
            renewal = "N/A"

        table.add_row(domain, ssl_type, status, expiry, str(days_left), renewal)

    # Add detected non-SSL domains
    for domain in non_ssl_domains:
        table.add_row(domain, "none", "🔓 No SSL", "N/A", "N/A", "N/A")

    output.stop()
    output.print_data(table)

    if non_ssl_domains:
        output.print("\n[yellow]💡 Tip: Add SSL certificates for non-SSL domains:[/yellow]", emoji_code="")
        output.print(f"[dim]  fm ssl add --standalone <domain>[/dim]", emoji_code="")


def _list_all_certificates(ctx: typer.Context):
    """List all SSL certificates (bench + external)."""

    services_manager = ctx.obj["services"]
    context = LoggerContext(operation="ssl-list-all")
    output = get_output_handler(ctx, context=context)

    output.print("\n[bold cyan]═══ External Certificates ═══[/bold cyan]\n", emoji_code="")
    _list_external_certificates(ctx)

    output.print("\n[bold cyan]═══ Bench Certificates ═══[/bold cyan]\n", emoji_code="")

    bench_service = BenchService(CLI_BENCHES_DIRECTORY, services_manager)
    benches = bench_service.get_bench_names()

    if not benches:
        output.print("ℹ️  No benches found", emoji_code="")
    else:
        for bench_name in benches:
            output.print(f"\n[bold]Bench: {bench_name}[/bold]", emoji_code="")
            _list_bench_certificates(ctx, bench_name)


def _renew_external_certificate(ctx: typer.Context, domain: str, dry_run: bool, force: bool = False):
    """Renew SSL certificate for a specific external domain."""

    services_manager = ctx.obj["services"]
    context = LoggerContext(operation="ssl-renew-external")
    output = get_output_handler(ctx, context=context)

    external_config_path = services_manager.path / "nginx-proxy" / "external_domains.toml"
    external_manager = ExternalDomainConfigManager(external_config_path)

    if not external_manager.domain_exists(domain):
        output.display_error(f"No external certificate found for domain '{domain}'")
        output.print("To list external certificates: fm ssl list --standalone", emoji_code="")
        raise typer.Exit(1)

    output.change_head(f"Renewing certificate for {domain}")

    try:
        cert = external_manager.to_ssl_certificate(domain)
        if not cert:
            raise ValueError(f"Could not create certificate object for {domain}")

        global_proxy_storage = services_manager.proxy_storage

        storage_config = SSLStorageConfig(
            ssl_dir=global_proxy_storage.dirs.ssl.host,
            ssl_dir_container=global_proxy_storage.dirs.ssl.container,
            certs_dir=global_proxy_storage.dirs.certs.host,
            certs_dir_container=global_proxy_storage.dirs.certs.container,
            vhostd_dir=global_proxy_storage.dirs.vhostd.host,
            webroot_dir=global_proxy_storage.dirs.html.host,
        )

        link_manager = CertificateLinkManager(storage_config)
        nginx_controller = services_manager.nginx_controller

        def certificate_service_factory(cert, storage_cfg, output_handler):
            return create_certificate_service(cert, storage_cfg, output_handler)

        cert_manager = SSLCertificateManager(
            certificates=[cert],
            service_factory=certificate_service_factory,
            link_manager=link_manager,
            nginx_controller=nginx_controller,
            storage_config=storage_config,
            output_handler=output,
            config_save_callback=None,
        )

        with spinner(output, f"Renewing certificate for {domain}"):
            cert_manager.renew_certificate(domain=domain, dry_run=dry_run, force=force)
        output.print(f"Certificate renewal for {domain} completed", emoji_code=":white_check_mark:")

    except Exception as e:
        output.display_error(f"Failed to renew certificate: {e}")
        raise typer.Exit(1)


def _renew_all_external_certificates(ctx: typer.Context, dry_run: bool, force: bool = False):
    """Renew all external domain SSL certificates."""

    services_manager = ctx.obj["services"]
    context = LoggerContext(operation="ssl-renew-external-all")
    output = get_output_handler(ctx, context=context)

    external_config_path = services_manager.path / "nginx-proxy" / "external_domains.toml"
    external_manager = ExternalDomainConfigManager(external_config_path)

    external_domains = external_manager.list_domains()

    if not external_domains:
        output.print("No external SSL certificates to renew", emoji_code=":information:")
        return

    output.change_head(f"Renewing {len(external_domains)} external certificate(s)")

    for domain_config in external_domains:
        domain = domain_config.domain
        try:
            _renew_external_certificate(ctx, domain, dry_run, force)
        except Exception as e:
            output.warning(f"Failed to renew {domain}: {e}")


# ============================================================================
# DNS Provider Configuration Commands
# ============================================================================


@dns_config_command.command(name="cloudflare")
def dns_config_cloudflare(
    ctx: typer.Context,
    benchname: Annotated[
        Optional[str],
        typer.Argument(
            help="Bench name for bench-specific credentials. Omit for global configuration.",
            autocompletion=sites_autocompletion_callback,
        ),
    ] = None,
    api_token: Annotated[
        Optional[str],
        typer.Option("--api-token", help="Cloudflare API Token (recommended - scoped permissions)"),
    ] = None,
    api_key: Annotated[
        Optional[str],
        typer.Option("--api-key", help="Cloudflare Global API Key (legacy - full account access)"),
    ] = None,
    email: Annotated[
        Optional[str],
        typer.Option("--email", help="Cloudflare account email (required with Global API Key)"),
    ] = None,
    show: Annotated[
        bool,
        typer.Option("--show", "-s", help="Show current Cloudflare DNS credentials"),
    ] = False,
    remove: Annotated[
        bool,
        typer.Option("--remove", "-r", help="Remove Cloudflare DNS credentials"),
    ] = False,
):
    """
    Configure Cloudflare DNS credentials for DNS-01 challenge.

    Credentials can be configured at two levels:
    - [bold]Global[/bold]: Used by all benches (omit benchname)
    - [bold]Bench-specific[/bold]: Override for a specific bench (provide benchname)

    [bold cyan]Authentication Methods:[/bold cyan]

    1. [green]API Token[/green] (Recommended):
       - More secure with scoped permissions
       - Create at: https://dash.cloudflare.com/profile/api-tokens
       - Template: "Edit zone DNS"
       - Required permission: Zone > DNS > Edit

    2. [yellow]Global API Key[/yellow] (Legacy):
       - Full account access (less secure)
       - Requires --email with your Cloudflare account email
       - Find at: https://dash.cloudflare.com/profile/api-tokens
    """
    provider_name = DNS_PROVIDER.cloudflare.value

    # Show configuration
    if show:
        _show_dns_credentials(ctx, provider_name, benchname)
        return

    # Remove configuration
    if remove:
        _remove_dns_credentials(ctx, provider_name, benchname)
        return

    # Validate Cloudflare-specific credentials
    if not api_token and not api_key:
        richprint.error("Either [bold]--api-token[/bold] or [bold]--api-key[/bold] must be provided")
        richprint.print("\n[green]Recommended:[/green] Use --api-token for better security and scoped permissions")
        richprint.print("[yellow]Legacy:[/yellow] Use --api-key with --email for Global API Key authentication")
        richprint.print("\n[dim]Create API Token at: https://dash.cloudflare.com/profile/api-tokens[/dim]")
        raise typer.Exit(1)

    if api_key and not email:
        richprint.error("[bold]--email[/bold] is required when using [bold]--api-key[/bold] (Global API Key)")
        richprint.print("\n[yellow]Note:[/yellow] API Key authentication requires your Cloudflare account email")
        richprint.print("[green]Better option:[/green] Use --api-token instead (doesn't require email)")
        raise typer.Exit(1)

    # Configure credentials
    _configure_dns_credentials(ctx, provider_name, benchname, api_token, api_key, email)


@ssl_root_command.command(name="acme-sh", context_settings={"allow_extra_args": True, "ignore_unknown_options": True})
def acmesh_passthrough(
    ctx: typer.Context,
):
    """
    Run acme.sh commands directly with FM's environment (advanced users).

    [bold yellow]⚠️  Advanced users only![/bold yellow]
    This bypasses FM's certificate management. Use 'fm ssl add/remove/renew' for normal operations.

    This command provides direct access to acme.sh for advanced operations like certificate
    listing, info checking, manual renewals, revocations, and debugging.

    All acme.sh commands run with FM's SSL directory configuration and full access to
    certificate storage.
    """
    import os
    from frappe_manager.utils.subprocess import stream_command_output

    args = ctx.args

    services_manager = ctx.obj["services"]
    output = get_output_handler(ctx)

    global_proxy_storage = services_manager.proxy_storage
    ssl_dir = global_proxy_storage.dirs.ssl.host
    acmesh_home = ssl_dir / "acmesh" / ".acme.sh"
    acmesh_bin = acmesh_home / "acme.sh"

    if not acmesh_bin.exists():
        output.display_error("acme.sh is not installed yet")
        output.info("Run 'fm ssl add <benchname> <domain>' to install acme.sh first")
        raise typer.Exit(1)

    cmd = [str(acmesh_bin), "--home", str(acmesh_home)]

    # If no args provided, show help
    if not args:
        cmd.append("--help")
    else:
        cmd.extend(args)

    env = os.environ.copy()
    env["LE_WORKING_DIR"] = str(acmesh_home)

    output.change_head("Running acme.sh")
    output.info(f"Command: acme.sh {' '.join(args or ['--help'])}")
    output.info(f"Home: {acmesh_home}")
    output.print("")

    # Stream output directly to user
    exit_code_holder = [0]

    def stream_with_exit_tracking():
        """Generator that tracks exit code while yielding output."""
        for source, line in stream_command_output(cmd, env=env, cwd=None):
            if source == "exit_code":
                exit_code_holder[0] = int(line.decode())
            yield source, line

    # Display all output (print directly for raw acme.sh output)
    for source, line in stream_with_exit_tracking():
        if source in ("stdout", "stderr"):
            # Print directly without prefix for raw acme.sh output
            decoded = line.decode()
            print(decoded, flush=True)

    # Exit with acme.sh's exit code
    if exit_code_holder[0] != 0:
        output.print("")
        output.display_error(f"acme.sh exited with code {exit_code_holder[0]}")
        raise typer.Exit(exit_code_holder[0])
    else:
        output.print("")
        output.print("Command completed successfully", emoji_code=":white_check_mark:")


ssl_root_command.add_typer(dns_config_command, name="dns-config", help="Configure DNS provider credentials")


# ============================================================================
# Helper Functions for DNS Configuration
# ============================================================================


def _show_dns_credentials(ctx: typer.Context, provider_name: str, benchname: Optional[str] = None):
    """Show DNS credentials for a provider."""
    if benchname:
        # Show bench-level config
        services_manager = ctx.obj["services"]
        context = LoggerContext(bench=benchname, operation="dns-config-show")
        output = get_output_handler(ctx, context=context)
        bench = Bench.get_object(benchname, services_manager, output_handler=output)

        if bench.bench_config.dns_providers and provider_name in bench.bench_config.dns_providers:
            config = bench.bench_config.dns_providers[provider_name]
            output.print(f"\n[bold cyan]DNS Credentials for bench '{benchname}':[/bold cyan]", emoji_code="")
            output.print(f"Provider: [green]{provider_name}[/green]", emoji_code="")
            output.print(f"Email: {config.email if config.email else '[dim]Not set[/dim]'}", emoji_code="")
            output.print(
                f"API Token: {'[green]*** (set)[/green]' if config.api_token else '[dim]Not set[/dim]'}", emoji_code=""
            )
            output.print(
                f"API Key: {'[yellow]*** (set)[/yellow]' if config.api_key else '[dim]Not set[/dim]'}", emoji_code=""
            )
        else:
            output.print(
                f"\n[yellow]No {provider_name} credentials configured for bench '{benchname}'[/yellow]",
                emoji_code=":warning:",
            )
            output.print("[dim]Falling back to global configuration (if any)[/dim]", emoji_code="")

    # Show global config (always show, no output handler needed for global info display)
    fm_config = FMConfigManager.import_from_toml()
    richprint.print(f"\n[bold cyan]Global DNS Credentials:[/bold cyan]")
    richprint.print(f"Provider: [green]{provider_name}[/green]")

    if provider_name == DNS_PROVIDER.cloudflare.value:
        richprint.print(f"Email: {fm_config.cloudflare.email if fm_config.cloudflare.email else '[dim]Not set[/dim]'}")
        richprint.print(
            f"API Token: {'[green]*** (set)[/green]' if fm_config.cloudflare.api_token else '[dim]Not set[/dim]'}"
        )
        richprint.print(
            f"API Key: {'[yellow]*** (set)[/yellow]' if fm_config.cloudflare.api_key else '[dim]Not set[/dim]'}"
        )


def _remove_dns_credentials(ctx: typer.Context, provider_name: str, benchname: Optional[str] = None):
    """Remove DNS credentials for a provider."""
    if benchname:
        # Remove bench-level config
        services_manager = ctx.obj["services"]
        context = LoggerContext(bench=benchname, operation="dns-config-remove")
        output = get_output_handler(ctx, context=context)
        bench = Bench.get_object(benchname, services_manager, output_handler=output)

        if bench.bench_config.dns_providers and provider_name in bench.bench_config.dns_providers:
            bench.bench_config.dns_providers.pop(provider_name)
            if not bench.bench_config.dns_providers:
                bench.bench_config.dns_providers = None
            bench.bench_config.export_to_toml(bench.bench_config.root_path)
            output.print(
                f"Removed [green]{provider_name}[/green] credentials for bench '{benchname}'",
                emoji_code=":white_check_mark:",
            )
        else:
            output.warning(f"No {provider_name} credentials configured for bench '{benchname}'")
    else:
        # Remove global config
        fm_config = FMConfigManager.import_from_toml()

        if provider_name == DNS_PROVIDER.cloudflare.value:
            fm_config.cloudflare = FMCloudflareConfig()

        fm_config.export_to_toml()
        richprint.print(f"✅ Removed global [green]{provider_name}[/green] credentials")


def _configure_dns_credentials(
    ctx: typer.Context,
    provider_name: str,
    benchname: Optional[str],
    api_token: Optional[str],
    api_key: Optional[str],
    email: Optional[str],
):
    """Configure DNS credentials for a provider."""
    if benchname:
        # Configure bench-level credentials
        services_manager = ctx.obj["services"]
        context = LoggerContext(bench=benchname, operation="dns-config")
        output = get_output_handler(ctx, context=context)
        bench = Bench.get_object(benchname, services_manager, output_handler=output)

        output.change_head(f"Configuring {provider_name} credentials for bench '{benchname}'")

        if not bench.bench_config.dns_providers:
            bench.bench_config.dns_providers = {}

        bench.bench_config.dns_providers[provider_name] = DNSProviderConfig(
            email=email,
            api_token=api_token,
            api_key=api_key,
        )

        # Save bench config
        bench.bench_config.export_to_toml(bench.bench_config.root_path)

        output.print(
            f"[green]{provider_name}[/green] credentials configured for bench '{benchname}'",
            emoji_code=":white_check_mark:",
        )
        output.print(f"[dim]These credentials will be used for DNS-01 challenges on this bench[/dim]", emoji_code="")
        output.print(f"[dim]Saved to: {bench.bench_config.root_path}[/dim]", emoji_code="")
    else:
        # Configure global credentials (use richprint for global operations)
        richprint.change_head(f"Configuring global {provider_name} credentials")

        fm_config = FMConfigManager.import_from_toml()

        if provider_name == DNS_PROVIDER.cloudflare.value:
            fm_config.cloudflare = FMCloudflareConfig(
                email=email,
                api_token=api_token,
                api_key=api_key,
            )

        # Save global config
        fm_config.export_to_toml()

        richprint.print(f"✅ Global [green]{provider_name}[/green] credentials configured")
        richprint.print("[dim]These credentials will be used by all benches unless overridden at bench level[/dim]")
        richprint.print("[dim]Saved to: ~/frappe/fm_config.toml[/dim]")
