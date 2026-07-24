"""Helper functions for DNS provider credential management."""

import typer

from frappe_manager.logger.context import LoggerContext
from frappe_manager.metadata_manager import FMCloudflareConfig, FMConfigManager
from frappe_manager.output_manager import get_global_output_handler
from frappe_manager.site_manager.bench_config import DNSProviderConfig
from frappe_manager.site_manager.site import Bench
from frappe_manager.ssl_manager import DNS_PROVIDER

from .helpers import get_output_handler


def _show_dns_credentials(ctx: typer.Context, provider_name: str, benchname: str | None = None):
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
            output.print(f"\n[fm.accent]DNS Credentials for bench '{benchname}':[/fm.accent]", emoji_code="")
            output.print(f"Provider: [fm.ok]{provider_name}[/fm.ok]", emoji_code="")
            output.print(f"Email: {config.email if config.email else '[fm.muted]Not set[/fm.muted]'}", emoji_code="")
            output.print(
                f"API Token: {'[fm.ok]*** (set)[/fm.ok]' if config.api_token else '[fm.muted]Not set[/fm.muted]'}",
                emoji_code="",
            )
            output.print(
                f"API Key: {'[fm.warn]*** (set)[/fm.warn]' if config.api_key else '[fm.muted]Not set[/fm.muted]'}",
                emoji_code="",
            )
        else:
            output.print(
                f"\n[fm.warn]No {provider_name} credentials configured for bench '{benchname}'[/fm.warn]",
                emoji_code=":warning:",
            )
            output.print("[fm.muted]Falling back to global configuration (if any)[/fm.muted]", emoji_code="")

    # Show global config (always show, no output handler needed for global info display)
    fm_config = FMConfigManager.import_from_toml()
    output = get_global_output_handler()
    output.print("\n[fm.accent]Global DNS Credentials:[/fm.accent]")
    output.print(f"Provider: [fm.ok]{provider_name}[/fm.ok]")

    if provider_name == DNS_PROVIDER.cloudflare.value:
        output.print(f"Email: {fm_config.cloudflare.email if fm_config.cloudflare.email else '[fm.muted]Not set[/fm.muted]'}")
        output.print(
            f"API Token: {'[fm.ok]*** (set)[/fm.ok]' if fm_config.cloudflare.api_token else '[fm.muted]Not set[/fm.muted]'}",
        )
        output.print(
            f"API Key: {'[fm.warn]*** (set)[/fm.warn]' if fm_config.cloudflare.api_key else '[fm.muted]Not set[/fm.muted]'}",
        )


def _remove_dns_credentials(ctx: typer.Context, provider_name: str, benchname: str | None = None):
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
                f"Removed [fm.ok]{provider_name}[/fm.ok] credentials for bench '{benchname}'",
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
        output.print(f"✅ Removed global [fm.ok]{provider_name}[/fm.ok] credentials")


def _configure_dns_credentials(
    ctx: typer.Context,
    provider_name: str,
    benchname: str | None,
    api_token: str | None,
    api_key: str | None,
    email: str | None,
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
            f"[fm.ok]{provider_name}[/fm.ok] credentials configured for bench '{benchname}'",
            emoji_code=":white_check_mark:",
        )
        output.print("[fm.muted]These credentials will be used for DNS-01 challenges on this bench[/fm.muted]", emoji_code="")
        output.print(f"[fm.muted]Saved to: {bench.bench_config.root_path}[/fm.muted]", emoji_code="")
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

        output.print(f"✅ Global [fm.ok]{provider_name}[/fm.ok] credentials configured")
        output.print("[fm.muted]These credentials will be used by all benches unless overridden at bench level[/fm.muted]")
        output.print("[fm.muted]Saved to: ~/frappe/fm_config.toml[/fm.muted]")
