"""Helper functions for external domain SSL certificate operations."""

import re
import subprocess
from datetime import datetime, timezone

import typer
from rich.table import Table

from frappe_manager import CLI_BENCHES_DIRECTORY, SSL_RENEW_BEFORE_DAYS
from frappe_manager.logger import ContextualLogger, log
from frappe_manager.logger.context import LoggerContext
from frappe_manager.output_manager import spinner, temporary_stop
from frappe_manager.output_manager.silent_output import SilentOutputHandler
from frappe_manager.site_manager.bench_service import BenchService
from frappe_manager.site_manager.site import Bench
from frappe_manager.ssl_manager import LETSENCRYPT_PREFERRED_CHALLENGE, SUPPORTED_SSL_TYPES
from frappe_manager.ssl_manager.certificate_link_manager import CertificateLinkManager
from frappe_manager.ssl_manager.external_domain_manager import ExternalDomainConfig, ExternalDomainConfigManager
from frappe_manager.ssl_manager.letsencrypt_certificate import CustomDomainCertificate, LetsencryptSSLCertificate
from frappe_manager.ssl_manager.service_factory import create_certificate_service
from frappe_manager.ssl_manager.ssl_certificate_manager import SSLCertificateManager
from frappe_manager.ssl_manager.standalone_nginx_config_manager import StandaloneNginxConfigManager
from frappe_manager.ssl_manager.storage_config import SSLStorageConfig
from frappe_manager.utils.helpers import get_certificate_expiry_date

from .helpers import get_output_handler


def _add_external_certificate(
    ctx: typer.Context,
    domain: str,
    challenge: LETSENCRYPT_PREFERRED_CHALLENGE,
    cname: str | None,
    dry_run: bool,
    skip_dns_check: bool = False,
    wait_for_dns: bool = False,
):
    """Add SSL certificate for external (non-bench) domain."""

    services_manager = ctx.obj["services"]
    context = LoggerContext(operation="ssl-add-external")
    logger = ContextualLogger(log.get_logger(), context)
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
                        output.print("CNAME record did not propagate within 5 minutes.", emoji_code="")
                        output.print("Expected CNAME:", emoji_code="")
                        output.print(f"  _acme-challenge.{domain}  →  _acme-challenge.{cname}", emoji_code="")
                        output.print("", emoji_code="")
                        output.print("Please verify your DNS configuration and try again.", emoji_code="")
                        raise typer.Exit(1)

                    output.print(f"{propagation.message}", emoji_code=":white_check_mark:")
                else:
                    # Single validation check (no waiting)
                    validation = validator.validate_cname_for_acme(domain, cname)

                    if not validation.valid:
                        output.display_error("DNS validation failed")
                        output.print("", emoji_code="")
                        output.print(f"Domain: {domain}", emoji_code="")
                        output.print("Expected CNAME:", emoji_code="")
                        output.print(f"  _acme-challenge.{domain}  →  _acme-challenge.{cname}", emoji_code="")

                        if validation.actual_value:
                            output.print("", emoji_code="")
                            output.print("Current CNAME:", emoji_code="")
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

                    output.print("CNAME record verified", emoji_code=":white_check_mark:")
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
                    output.print(f"Domain resolves to {validation.actual_value}", emoji_code=":white_check_mark:")
                else:
                    output.warning(f"Domain {domain} doesn't have an A record")
                    output.print("HTTP-01 challenge may fail if DNS is not configured correctly.", emoji_code="")
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
            return create_certificate_service(logger, cert, storage_cfg, output_handler)

        cert_manager = SSLCertificateManager(
            logger=logger,
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
        try:
            standalone_nginx.create_https_config(domain)
            output.print("Created HTTPS configuration", emoji_code=":white_check_mark:")

            # Step 5: Reload nginx again to enable HTTPS
            nginx_controller.reload()
            output.print("Nginx reloaded with HTTPS enabled", emoji_code=":white_check_mark:")
        except Exception as post_cert_error:
            # HTTPS config or reload failed — cert was already issued; clean up to avoid orphan
            output.change_head("Cleaning up after HTTPS configuration failure")
            try:
                cert_manager.remove_certificate_by_domain(domain)
                standalone_nginx.remove_config(domain)
                nginx_controller.reload()
                output.print("Cleaned up nginx configuration and certificate", emoji_code=":white_check_mark:")
            except Exception as cleanup_error:
                output.debug(f"Failed to clean up after HTTPS config failure: {cleanup_error}")
            raise post_cert_error

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
                ),
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


def _remove_external_certificate(ctx: typer.Context, domain: str, yes: bool):
    services_manager = ctx.obj["services"]
    context = LoggerContext(operation="ssl-remove-external")
    logger = ContextualLogger(log.get_logger(), context)
    output = get_output_handler(ctx, context=context)

    external_config_path = services_manager.path / "nginx-proxy" / "external_domains.toml"
    external_manager = ExternalDomainConfigManager(external_config_path)

    if not external_manager.domain_exists(domain):
        output.display_error(f"Certificate does not exist for external domain '{domain}'")
        raise typer.Exit(1)

    domain_config = external_manager.get_domain(domain)
    output.change_head(f"Removing SSL certificate for {domain}")

    if not yes:
        with temporary_stop(output):
            choice = output.prompt_ask(
                prompt=f"Remove SSL certificate for {domain}?",
                choices=["yes", "no"],
                default="no",
                required_flag="--yes or -y",
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
            return create_certificate_service(logger, cert, storage_cfg, output_handler)

        cert_manager = SSLCertificateManager(
            logger=logger,
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
        domain_pattern = r"^# (.+?)/$"

        detected_domains = set()
        for line in result.stdout.split("\n"):
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
                output = SilentOutputHandler()
                bench = Bench.get_object(bench_name, services_manager, logger=None, output_handler=output)
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

            expiry_date = get_certificate_expiry_date(fullchain_path)
            if expiry_date:
                expiry = expiry_date.strftime("%Y-%m-%d %H:%M")
                # Make datetime.now() timezone-aware to match expiry_date
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
        output.print("[dim]  fm ssl add --standalone <domain>[/dim]", emoji_code="")


def _renew_external_certificate(ctx: typer.Context, domain: str, dry_run: bool, force: bool = False):
    """Renew SSL certificate for a specific external domain."""

    services_manager = ctx.obj["services"]
    context = LoggerContext(operation="ssl-renew-external")
    logger = ContextualLogger(log.get_logger(), context)
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
            return create_certificate_service(logger, cert, storage_cfg, output_handler)

        cert_manager = SSLCertificateManager(
            logger=logger,
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
