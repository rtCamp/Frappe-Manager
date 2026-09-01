"""Helper functions for external domain SSL certificate operations."""

import re
import subprocess
from datetime import UTC, datetime

import typer
from rich.table import Table

from frappe_manager import CLI_BENCHES_DIRECTORY, SSL_RENEW_BEFORE_DAYS
from frappe_manager.logger import get_logger, set_context
from frappe_manager.output_manager import spinner, temporary_stop
from frappe_manager.output_manager.silent_output import SilentOutputHandler
from frappe_manager.site_manager.bench_service import BenchService
from frappe_manager.site_manager.site import Bench
from frappe_manager.ssl_manager import LETSENCRYPT_PREFERRED_CHALLENGE
from frappe_manager.ssl_manager.certificate_exceptions import SSLCertificateNotDueForRenewalError
from frappe_manager.ssl_manager.certificate_link_manager import CertificateLinkManager
from frappe_manager.ssl_manager.external_domain_manager import ExternalDomainConfig, ExternalDomainConfigManager
from frappe_manager.ssl_manager.letsencrypt_certificate import build_letsencrypt_certificate
from frappe_manager.ssl_manager.service_factory import create_certificate_service
from frappe_manager.ssl_manager.ssl_certificate_manager import SSLCertificateManager
from frappe_manager.ssl_manager.standalone_nginx_config_manager import StandaloneNginxConfigManager
from frappe_manager.ssl_manager.storage_config import SSLStorageConfig
from frappe_manager.utils.helpers import get_certificate_expiry_date

from .helpers import get_output_handler

logger = get_logger(component="ssl_external")


def _build_certificate_storage(services_manager) -> tuple[SSLStorageConfig, CertificateLinkManager]:
    """Describe the global nginx-proxy SSL layout and the link manager that maintains it.

    Every external-domain entrypoint (add / remove / renew / list) needs exactly this pair.
    """
    dirs = services_manager.proxy_storage.dirs
    storage_config = SSLStorageConfig(
        ssl_dir=dirs.ssl.host,
        ssl_dir_container=dirs.ssl.container,
        certs_dir=dirs.certs.host,
        certs_dir_container=dirs.certs.container,
        vhostd_dir=dirs.vhostd.host,
        webroot_dir=dirs.html.host,  # For HTTP-01 challenge
    )
    return storage_config, CertificateLinkManager(storage_config)


def _build_standalone_nginx(services_manager) -> StandaloneNginxConfigManager:
    """Writer for standalone (non-bench) vhosts: only add and remove touch those configs."""
    dirs = services_manager.proxy_storage.dirs
    return StandaloneNginxConfigManager(
        conf_dir=dirs.confd.host,
        webroot_dir_container=dirs.html.container,
        certs_dir_container=dirs.certs.container,
    )


def _build_certificate_manager(
    certificates,
    storage_config: SSLStorageConfig,
    link_manager: CertificateLinkManager,
    nginx_controller,
    output,
) -> SSLCertificateManager:
    """Certificate manager for an external domain.

    `certificates` is the only thing that varies: add starts empty and hands the cert to
    add_certificate() afterwards, while remove and renew seed the manager with it.
    `config_save_callback` is always None -- there is no bench config to write back to.
    """

    def certificate_service_factory(cert, storage_cfg, output_handler):
        return create_certificate_service(cert, storage_cfg, output_handler)

    return SSLCertificateManager(
        certificates=certificates,
        service_factory=certificate_service_factory,
        link_manager=link_manager,
        nginx_controller=nginx_controller,
        storage_config=storage_config,
        output_handler=output,
        config_save_callback=None,
    )


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
    set_context(operation="ssl-add-external")
    output = get_output_handler(ctx)

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
        cert = build_letsencrypt_certificate(domain, challenge, cname)

        if cname:
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
        elif not skip_dns_check and challenge == LETSENCRYPT_PREFERRED_CHALLENGE.http01:
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

        storage_config, link_manager = _build_certificate_storage(services_manager)
        nginx_controller = services_manager.nginx_controller
        standalone_nginx = _build_standalone_nginx(services_manager)

        # Step 1: Create HTTP-only nginx config (for ACME challenge)
        output.change_head(f"Setting up nginx configuration for {domain}")
        standalone_nginx.create_http_config(domain)
        output.print("Created HTTP configuration for ACME challenge", emoji_code=":white_check_mark:")

        # Step 2: Reload nginx to apply the config
        output.change_head("Reloading nginx to apply configuration")
        nginx_controller.reload()
        output.print("Nginx reloaded successfully", emoji_code=":white_check_mark:")

        # Start with an empty list; the cert is handed to add_certificate() next
        cert_manager = _build_certificate_manager([], storage_config, link_manager, nginx_controller, output)

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

        # Steps 4 and 5 mutate the SHARED nginx-proxy conf.d, so they are dry-run guarded the
        # same way the persistence step below is: a dry run deliberately never issues (or links)
        # a certificate, and an HTTPS vhost pointing at absent cert files is a fatal nginx config
        # error that breaks reloads and startup for every bench the global proxy fronts.
        if not dry_run:
            # Step 4: Update nginx config to enable HTTPS
            output.change_head(f"Enabling HTTPS for {domain}")
            try:
                standalone_nginx.create_https_config(domain)
                output.print("Created HTTPS configuration", emoji_code=":white_check_mark:")

                # Step 5: Reload nginx again to enable HTTPS
                nginx_controller.reload()
                output.print("Nginx reloaded with HTTPS enabled", emoji_code=":white_check_mark:")
            except Exception as post_cert_error:
                # HTTPS config or reload failed -- cert was already issued; clean up to avoid orphan
                output.change_head("Cleaning up after HTTPS configuration failure")
                # Certificate removal must not be able to block the nginx cleanup: the cert is
                # only registered on the non-dry-run path, so on a dry run this raises
                # SSLCertificateNotFoundError and the orphaned vhost this handler exists to
                # delete would survive.
                try:
                    cert_manager.remove_certificate_by_domain(domain)
                except Exception as cert_cleanup_error:
                    output.debug(f"Failed to clean up after HTTPS config failure: {cert_cleanup_error}")
                try:
                    standalone_nginx.remove_config(domain)
                    nginx_controller.reload()
                    output.print("Cleaned up nginx configuration and certificate", emoji_code=":white_check_mark:")
                except Exception as cleanup_error:
                    output.debug(f"Failed to clean up after HTTPS config failure: {cleanup_error}")
                raise post_cert_error
        else:
            # A dry run persists nothing, so the temporary ACME-challenge vhost from step 1 must
            # go as well: left behind it is an invisible standalone vhost that neither
            # `fm ssl remove --standalone` nor `fm ssl list --standalone` can reach, because
            # the domain was never written to external_domains.toml.
            output.change_head("Cleaning up dry-run nginx configuration")
            standalone_nginx.remove_config(domain)
            nginx_controller.reload()
            output.print("Removed temporary nginx configuration (dry run)", emoji_code=":white_check_mark:")

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
            output.print("[fm.accent]To use this certificate in your Docker project:[/fm.accent]", emoji_code="")
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
    set_context(operation="ssl-remove-external")
    output = get_output_handler(ctx)

    external_config_path = services_manager.path / "nginx-proxy" / "external_domains.toml"
    external_manager = ExternalDomainConfigManager(external_config_path)

    if not external_manager.domain_exists(domain):
        output.display_error(f"Certificate does not exist for external domain '{domain}'")
        raise typer.Exit(1)

    domain_config = external_manager.get_domain(domain)
    output.change_head(f"Removing SSL certificate for {domain}")

    if not yes:
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

        storage_config, link_manager = _build_certificate_storage(services_manager)
        nginx_controller = services_manager.nginx_controller
        standalone_nginx = _build_standalone_nginx(services_manager)

        cert_manager = _build_certificate_manager([cert], storage_config, link_manager, nginx_controller, output)

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
                bench = Bench.get_object(bench_name, services_manager, output_handler=output)
                bench_domains.update(bench.bench_config.domains)
            except Exception as e:
                logger.debug(f"cert scan skipped {bench_name}: {e}")
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
    output = get_output_handler(ctx)

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

    _storage_config, link_manager = _build_certificate_storage(services_manager)

    table = Table(title="External Domains & SSL Certificates", show_header=True, header_style="fm.accent")
    table.add_column("Domain", style="fm.info")
    table.add_column("Type", style="fm.warn")
    table.add_column("Status", style="fm.ok")
    table.add_column("Expiry", style="fm.info")
    table.add_column("Days Left", justify="right")
    table.add_column("Renewal", style="fm.error")

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
                now = datetime.now(UTC)
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

    output.print_data(table)

    if non_ssl_domains:
        output.print("\n[fm.warn]💡 Tip: Add SSL certificates for non-SSL domains:[/fm.warn]", emoji_code="")
        output.print("[fm.muted]  fm ssl add --standalone <domain>[/fm.muted]", emoji_code="")


def _renew_external_certificate(ctx: typer.Context, domain: str, dry_run: bool, force: bool = False):
    """Renew SSL certificate for a specific external domain."""

    services_manager = ctx.obj["services"]
    set_context(operation="ssl-renew-external")
    output = get_output_handler(ctx)

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

        storage_config, link_manager = _build_certificate_storage(services_manager)
        nginx_controller = services_manager.nginx_controller

        cert_manager = _build_certificate_manager([cert], storage_config, link_manager, nginx_controller, output)

        with spinner(output, f"Renewing certificate for {domain}"):
            cert_manager.renew_certificate(domain=domain, dry_run=dry_run, force=force)
        output.print(f"Certificate renewal for {domain} completed", emoji_code=":white_check_mark:")

    # A not-yet-due certificate is a healthy state, not a failure: renew_certificate raises this
    # before any acme.sh call. The bench path in renew.py warns and exits 0; match it, otherwise
    # `--all` reports every healthy domain as a failure.
    except SSLCertificateNotDueForRenewalError as e:
        output.warning(e.message)
        return

    except Exception as e:
        output.display_error(f"Failed to renew certificate: {e}")
        raise typer.Exit(1)


def _renew_all_external_certificates(ctx: typer.Context, dry_run: bool, force: bool = False):
    """Renew all external domain SSL certificates."""

    services_manager = ctx.obj["services"]
    set_context(operation="ssl-renew-external-all")
    output = get_output_handler(ctx)

    external_config_path = services_manager.path / "nginx-proxy" / "external_domains.toml"
    external_manager = ExternalDomainConfigManager(external_config_path)

    external_domains = external_manager.list_domains()

    if not external_domains:
        output.print("No external SSL certificates to renew", emoji_code=":information:")
        return

    output.change_head(f"Renewing {len(external_domains)} external certificate(s)")

    failed: list[str] = []

    for domain_config in external_domains:
        domain = domain_config.domain
        try:
            _renew_external_certificate(ctx, domain, dry_run, force)
        except typer.Exit as e:
            # _renew_external_certificate has already printed the real reason and signalled
            # failure with `raise typer.Exit(1)`. click.exceptions.Exit carries no message, so
            # `str(e)` is empty -- catching it here as Exception printed a bare "Failed to
            # renew <domain>: " and, worse, swallowed the nonzero exit.
            if e.exit_code == 0:
                continue
            output.warning(f"Failed to renew {domain}: renewal exited with code {e.exit_code} (reason reported above)")
            failed.append(domain)
        except Exception as e:
            output.warning(f"Failed to renew {domain}: {e}")
            failed.append(domain)

    if failed:
        output.display_error(
            f"Failed to renew {len(failed)} of {len(external_domains)} certificate(s): {', '.join(failed)}"
        )
        raise typer.Exit(1)
