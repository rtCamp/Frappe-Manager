"""Helper functions for DNS provider credential management."""

import typer

from frappe_manager.metadata_manager import FMConfigManager
from frappe_manager.output_manager import OutputHandler, get_global_output_handler
from frappe_manager.site_manager.site import Bench
from frappe_manager.ssl_manager import DNS_PROVIDER
from frappe_manager.ssl_manager.dns_provider import DNSProviderConfig

from .helpers import get_output_handler


def _print_credential_lines(output: OutputHandler, config: DNSProviderConfig, emoji_code: str = "") -> None:
    """Print one credential set with its secrets masked."""
    output.print(f"  Provider: [fm.ok]{config.provider.value}[/fm.ok]", emoji_code=emoji_code)
    output.print(f"  Email: {config.email if config.email else '[fm.muted]Not set[/fm.muted]'}", emoji_code=emoji_code)
    output.print(
        f"  API Token: {'[fm.ok]*** (set)[/fm.ok]' if config.api_token else '[fm.muted]Not set[/fm.muted]'}",
        emoji_code=emoji_code,
    )
    output.print(
        f"  API Key: {'[fm.warn]*** (set)[/fm.warn]' if config.api_key else '[fm.muted]Not set[/fm.muted]'}",
        emoji_code=emoji_code,
    )


def _show_dns_credentials(
    ctx: typer.Context,
    provider_name: str,
    benchname: str | None = None,
    label: str | None = None,
):
    """List every labelled credential set at both scopes; a label narrows the listing to one."""
    default_label = DNS_PROVIDER.cloudflare.value

    if benchname:
        services_manager = ctx.obj["services"]
        output = get_output_handler(ctx)
        bench = Bench.get_object(benchname, services_manager, output_handler=output)

        entries = bench.bench_config.dns_providers or {}
        if label:
            entries = {name: entry for name, entry in entries.items() if name == label}

        output.print(f"\n[fm.accent]DNS Credentials for bench '{benchname}':[/fm.accent]", emoji_code="")

        if entries:
            for name in sorted(entries):
                suffix = " [fm.muted](default)[/fm.muted]" if name == default_label else ""
                output.print(f"\\[ssl.dns_providers.{name}]{suffix}", emoji_code="")
                _print_credential_lines(output, entries[name])
        else:
            missing = f"No '{label}'" if label else f"No {provider_name}"
            output.print(
                f"[fm.warn]{missing} credentials configured for bench '{benchname}'[/fm.warn]",
                emoji_code=":warning:",
            )
            output.print("[fm.muted]Falling back to global configuration (if any)[/fm.muted]", emoji_code="")

    # Global scope is now structurally identical to bench scope: the default account is the set
    # labelled 'cloudflare', not a separate `[cloudflare]` table, so there is one mechanism instead
    # of two. The migration relocates the old table into that label.
    fm_config = FMConfigManager.import_from_toml()
    output = get_global_output_handler()
    output.print("\n[fm.accent]Global DNS Credentials:[/fm.accent]", emoji_code="")

    global_entries = fm_config.dns_providers or {}
    if label:
        global_entries = {name: entry for name, entry in global_entries.items() if name == label}

    for name in sorted(global_entries):
        suffix = " [fm.muted](default)[/fm.muted]" if name == default_label else ""
        output.print(f"\\[ssl.dns_providers.{name}]{suffix}", emoji_code="")
        _print_credential_lines(output, global_entries[name])

    if not global_entries:
        missing = f"No '{label}'" if label else f"No {provider_name}"
        output.print(f"[fm.warn]{missing} credentials configured globally[/fm.warn]", emoji_code=":warning:")


def _resolve_removal_target(output: OutputHandler, label: str | None, candidates: list[str], scope: str) -> str:
    """The labelled set to delete. Callers pass a non-empty candidate list, or a label to validate."""
    if label:
        if label not in candidates:
            available = f"Available: {', '.join(candidates)}" if candidates else "No labelled sets are stored there."
            output.display_error(f"No credential set labelled '{label}' in {scope}. {available}")
            raise typer.Exit(1)
        return label

    # Guessing here deletes a credential the user did not name, and nothing would report which
    # account lost its token.
    if len(candidates) > 1:
        output.display_error(
            f"{scope} holds several credential sets: {', '.join(candidates)}. "
            "Pass [bold]--name <label>[/bold] to say which one to remove."
        )
        raise typer.Exit(1)

    return candidates[0]


def _remove_dns_credentials(
    ctx: typer.Context,
    provider_name: str,
    benchname: str | None = None,
    label: str | None = None,
):
    """Delete one credential set. Without a label the target has to be unambiguous."""
    if benchname:
        services_manager = ctx.obj["services"]
        output = get_output_handler(ctx)
        bench = Bench.get_object(benchname, services_manager, output_handler=output)

        entries = bench.bench_config.dns_providers or {}
        if not entries:
            output.warning(f"No {provider_name} credentials configured for bench '{benchname}'")
            return

        target = _resolve_removal_target(output, label, sorted(entries), f"bench '{benchname}'")
        entries.pop(target)
        bench.bench_config.dns_providers = entries or None
        bench.bench_config.export_to_toml(bench.bench_config.root_path)
        output.print(
            f"Removed [fm.ok]{target}[/fm.ok] credentials for bench '{benchname}'",
            emoji_code=":white_check_mark:",
        )
        return

    # Global scope mirrors bench scope exactly now that the `[cloudflare]` table is gone, so the
    # ambiguity special-case it used to need is gone with it.
    fm_config = FMConfigManager.import_from_toml()
    output = get_global_output_handler()
    entries = fm_config.dns_providers or {}

    if not entries:
        output.warning(f"No {provider_name} credentials configured globally")
        return

    target = _resolve_removal_target(output, label, sorted(entries), "~/frappe/fm_config.toml")
    entries.pop(target)
    fm_config.dns_providers = entries or None
    fm_config.export_to_toml()
    output.print(f"✅ Removed global [fm.ok]{target}[/fm.ok] credentials")


def _configure_dns_credentials(
    ctx: typer.Context,
    provider_name: str,
    benchname: str | None,
    api_token: str | None,
    api_key: str | None,
    email: str | None,
    label: str | None = None,
):
    """Configure DNS credentials for a provider. The label picks the table, the benchname the file."""
    # A label-less write targets the default label at BOTH scopes now. Globally that used to mean the
    # separate `[cloudflare]` table; folding it into a label leaves one mechanism, and the migration
    # moves any existing table into exactly this entry.
    label = label or DNS_PROVIDER.cloudflare.value

    if benchname:
        services_manager = ctx.obj["services"]
        output = get_output_handler(ctx)
        bench = Bench.get_object(benchname, services_manager, output_handler=output)

        output.change_head(f"Configuring {provider_name} credentials for bench '{benchname}'")

        if not bench.bench_config.dns_providers:
            bench.bench_config.dns_providers = {}

        bench.bench_config.dns_providers[label] = DNSProviderConfig(
            provider=DNS_PROVIDER.cloudflare,
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
        output.print(
            "[fm.muted]These credentials will be used for DNS-01 challenges on this bench[/fm.muted]", emoji_code=""
        )
        output.print(
            f"[fm.muted]Saved to: \\[ssl.dns_providers.{label}] in {bench.bench_config.root_path}[/fm.muted]",
            emoji_code="",
        )
        return

    output = get_global_output_handler()
    fm_config = FMConfigManager.import_from_toml()

    output.change_head(f"Configuring global {provider_name} credentials '{label}'")

    if not fm_config.dns_providers:
        fm_config.dns_providers = {}

    fm_config.dns_providers[label] = DNSProviderConfig(
        provider=DNS_PROVIDER.cloudflare,
        email=email,
        api_token=api_token,
        api_key=api_key,
    )
    fm_config.export_to_toml()

    output.print(f"✅ Global [fm.ok]{provider_name}[/fm.ok] credentials '{label}' configured")
    if label == DNS_PROVIDER.cloudflare.value:
        output.print(
            "[fm.muted]This is the default set: every bench uses it unless a certificate names another[/fm.muted]"
        )
    else:
        output.print(
            "[fm.muted]Bind a certificate to it with: fm ssl add <bench> <domain> --challenge dns01 "
            f"--dns-provider {label}[/fm.muted]"
        )
    output.print(f"[fm.muted]Saved to: \\[ssl.dns_providers.{label}] in ~/frappe/fm_config.toml[/fm.muted]")
