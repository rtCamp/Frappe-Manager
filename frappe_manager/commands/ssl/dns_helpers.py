"""Helper functions for DNS provider credential management."""

from typing import Optional
import typer
from frappe_manager.site_manager.site import Bench
from frappe_manager.site_manager.bench_config import DNSProviderConfig
from frappe_manager.ssl_manager import DNS_PROVIDER
from frappe_manager.logger.context import LoggerContext
from frappe_manager.output_manager import get_global_output_handler
from frappe_manager.metadata_manager import FMConfigManager, FMCloudflareConfig
from .helpers import get_output_handler


def _show_dns_credentials(ctx: typer.Context, provider_name: str, benchname: Optional[str] = None):
    """Show DNS credentials for a provider."""
    if benchname:
        # Show bench-level config
        services_manager = ctx.obj["services"]
        context = LoggerContext(bench=benchname, operation="dns-config-show")
        output = get_output_handler(ctx, context=context)
        logger = ctx.obj.get("logger")
        bench = Bench.get_object(benchname, services_manager, logger=logger, output_handler=output)

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
    output = get_global_output_handler()
    output.print(f"\n[bold cyan]Global DNS Credentials:[/bold cyan]")
    output.print(f"Provider: [green]{provider_name}[/green]")

    if provider_name == DNS_PROVIDER.cloudflare.value:
        output.print(f"Email: {fm_config.cloudflare.email if fm_config.cloudflare.email else '[dim]Not set[/dim]'}")
        output.print(
            f"API Token: {'[green]*** (set)[/green]' if fm_config.cloudflare.api_token else '[dim]Not set[/dim]'}"
        )
        output.print(
            f"API Key: {'[yellow]*** (set)[/yellow]' if fm_config.cloudflare.api_key else '[dim]Not set[/dim]'}"
        )


def _remove_dns_credentials(ctx: typer.Context, provider_name: str, benchname: Optional[str] = None):
    """Remove DNS credentials for a provider."""
    if benchname:
        services_manager = ctx.obj["services"]
        context = LoggerContext(bench=benchname, operation="dns-config-remove")
        output = get_output_handler(ctx, context=context)
        logger = ctx.obj.get("logger")
        bench = Bench.get_object(benchname, services_manager, logger=logger, output_handler=output)

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
            fm_config.cloudflare = FMCloudflareConfig(email=None, api_token=None, api_key=None)

        fm_config.export_to_toml()
        output = get_global_output_handler()
        output.print(f"✅ Removed global [green]{provider_name}[/green] credentials")


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
        services_manager = ctx.obj["services"]
        context = LoggerContext(bench=benchname, operation="dns-config")
        output = get_output_handler(ctx, context=context)
        logger = ctx.obj.get("logger")
        bench = Bench.get_object(benchname, services_manager, logger=logger, output_handler=output)

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
        # Configure global credentials (use output handler for global operations)
        output = get_global_output_handler()
        output.change_head(f"Configuring global {provider_name} credentials")

        fm_config = FMConfigManager.import_from_toml()

        if provider_name == DNS_PROVIDER.cloudflare.value:
            fm_config.cloudflare = FMCloudflareConfig(
                email=email,
                api_token=api_token,
                api_key=api_key,
            )

        # Save global config
        fm_config.export_to_toml()

        output.print(f"✅ Global [green]{provider_name}[/green] credentials configured")
        output.print("[dim]These credentials will be used by all benches unless overridden at bench level[/dim]")
        output.print("[dim]Saved to: ~/frappe/fm_config.toml[/dim]")
