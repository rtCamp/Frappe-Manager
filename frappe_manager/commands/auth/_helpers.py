"""Shared state and apply logic for the fm auth commands."""

import sys

import typer

from frappe_manager.commands import check_bench_migration_required
from frappe_manager.output_manager import get_global_output_handler
from frappe_manager.site_manager.bench_config import AuthConfig, BenchRuntime, WebAuthConfig
from frappe_manager.site_manager.modules.auth import generate_password, validate_credentials, web_auth_enforced
from frappe_manager.site_manager.modules.realip import validate_cidrs
from frappe_manager.site_manager.site import Bench
from frappe_manager.ssl_manager import SUPPORTED_SSL_TYPES

PANEL_SURFACES = "Surfaces (what the verb acts on)"
PANEL_CREDENTIALS = "Credentials"
PANEL_EXEMPTIONS = "Exemptions (who skips the prompt)"
PANEL_SAFETY = "Safety"

ADDRESS_HELP = "Bench, or BENCH/SITE for one of its sites. Without a site part the whole bench is addressed: every site that has no auth of its own follows it."


def surface_summary(web: bool, tools: bool | None) -> str:
    """`tools` is None for a site scope, which has no tools surface to report: there is one
    Adminer and one Mailpit per bench, so the tools state belongs to the bench line, not a site's.

    Both ends name BOTH surfaces when there are two: the off state always has ("off on both
    surfaces (web, tools)"), so an on state that protects only one must say the other is off too --
    "on for: web" alone let an operator infer tools from its absence, which read as "unknown", not
    "off"."""
    if tools is None:
        return "on for: web" if web else "off on the web surface"
    protected = [name for name, on in (("web", web), ("tools", tools)) if on]
    unprotected = [name for name, on in (("web", web), ("tools", tools)) if not on]
    if not protected:
        return "off on both surfaces (web, tools)"
    if not unprotected:
        return "on for: web, tools"
    return f"on for: {', '.join(protected)} ({', '.join(unprotected)} off)"


def read_password_from_stdin() -> str:
    """`--password -` keeps the secret out of the shell history: piped from
    stdin in scripts, prompted without echo on a terminal."""
    if sys.stdin.isatty():
        return typer.prompt("Password", hide_input=True)
    return sys.stdin.readline().rstrip("\r\n")


def _web_enforcement(bench, site: str | None, entry, config: WebAuthConfig) -> tuple[bool, str | None]:
    """(what nginx is ACTUALLY enforcing on CONFIG's web surface, a note to show when that
    disagrees with what `bench_config.toml` records). Only ever disagrees recorded-but-not-served:
    `fm auth status` must not call a scope protected because the config says so when the conf that
    would gate it is missing, stale, or was never written -- measured live: a deleted
    `custom/<site>/auth.conf`, reloaded, served 200s while this still said `on`.

    A site's OWN override is a separate failure mode `nginx_conf_serves_per_site` exists for: on a
    bench whose conf predates one server block per site, nothing renders a per-site file at all
    (`ensure_fm_nginx_confs` falls back to the bench-wide conf, which never reads a site's entry),
    so that recorded override cannot be enforced regardless of what the bench-wide conf is doing.
    """
    if not config.web:
        return False, None
    own_entry = site is not None and entry is not None and getattr(entry, "auth", None) is not None
    if own_entry and not bench.nginx_conf_serves_per_site():
        return False, (
            "recorded, but this bench's nginx conf predates one server block per site so it cannot "
            "enforce a site's own auth; run 'fm migrate' to re-render it"
        )
    check_site = site if site is not None else (bench.bench_config.site_names[0] if bench.bench_config.sites else None)
    if check_site is None:
        # Nothing served yet to check against (a --bench-only bench with no [sites] at all): the
        # record is the only answer there is, and it cannot be wrong about a site that does not exist.
        return True, None
    if web_auth_enforced(bench, check_site):
        return True, None
    return False, (
        "recorded, but nginx is not currently serving an fm auth conf for it; re-run 'fm auth enable' to re-apply"
    )


def _fill_credentials(card, config: WebAuthConfig, *, hint_when_off: bool) -> None:
    """Supporting facts for ONE auth state: credentials per protected surface, then exemptions.
    Gated on the RECORDED intent (`config.web`/`tools`), not on enforcement -- these are the
    credentials that take effect once a surface actually serves, useful while repairing a gap
    `_web_enforcement` just reported.

    On the all-off state credentials and exemptions are inert, and showing them reads as if
    something were still enforced. They stay in the config and reappear as soon as a surface is
    protected again.
    """
    tools = config.tools if isinstance(config, AuthConfig) else None
    if not (config.web or tools):
        if hint_when_off and (config.password or config.allow_ips or config.allow_paths):
            card.fact("stored", "credentials and exemptions stay stored and apply again when a surface is protected")
        return
    # One credential fact per protected surface, because they can now differ. A single fact was
    # only ever right while both surfaces shared a file, and it is what made a silent
    # re-credentialling of the tools surface invisible.
    tools_differs = isinstance(config, AuthConfig) and (config.tools_user or config.tools_password)
    web_label = "web user" if (tools and tools_differs) else "user"
    if config.web or not tools:
        card.fact(web_label, config.user)
        if config.password:
            card.fact(web_label.replace("user", "password"), config.password)
    if tools and isinstance(config, AuthConfig):
        label = "tools user" if tools_differs else "user"
        if not config.web or tools_differs:
            card.fact(label, config.effective_tools_user)
            if config.effective_tools_password:
                card.fact(label.replace("user", "password"), config.effective_tools_password)
    if config.allow_ips:
        card.fact("exempt ips", ", ".join(config.allow_ips))
    if config.web and config.allow_paths:
        card.fact("exempt paths", ", ".join(config.allow_paths))


def build_auth_card(
    bench, site: str | None, entry, name: str, config: WebAuthConfig, *, hint_when_off: bool, source: str | None = None
):
    """The one `railcard.Card` shape every auth verb renders: `status`, `enable`, and `disable`
    alike, so writing an auth state and reading it back never disagree in shape.

    The password is shown unconditionally, the same choice `fm info` and `fm services info`
    already made for every other credential fm's cards carry, and the one this card's own --json
    payload makes too: hiding it here only while the card happens to also be named `status` would
    make the two surfaces of the SAME command disagree about what a status check reveals. The
    repo's actual answer to a password ending up somewhere it should not (a terminal recording) is
    `just readme-hero`'s enforced post-hoc redaction, not asking any one command to hold back.
    """
    from frappe_manager.output_manager import railcard

    enforced_web, web_note = _web_enforcement(bench, site, entry, config)
    tools = config.tools if isinstance(config, AuthConfig) else None
    card = railcard.Card(name, surface_summary(enforced_web, tools), active=enforced_web or bool(tools))
    if source:
        card.fact("source", source)
    if web_note:
        card.fact("web", web_note)
    _fill_credentials(card, config, hint_when_off=hint_when_off)
    return card


def resolve_scope(ctx, address: str | None, output):
    """Everything both the reporting and the writing commands need before they diverge:
    the bench, the optional site part, and the auth config that currently rules the scope."""
    check_bench_migration_required(address)
    services_manager = ctx.obj["services"]
    bench = Bench.get_object(address, services_manager, output_handler=output)
    site = ctx.obj.get("site") if ctx.obj else None
    entry = (bench.bench_config.sites or {}).get(site) if site else None
    return bench, site, entry


def apply_auth(
    ctx,
    address: str | None,
    *,
    web_target: bool | None,
    tools_target: bool | None,
    tools_named: bool,
    user: str | None = None,
    password: str | None = None,
    rotate: bool = False,
    allow_ip: list[str] | None = None,
    allow_path: list[str] | None = None,
    clear_exemptions: bool = False,
    insecure: bool = False,
) -> None:
    """Write the auth state both `enable` and `disable` resolve to.

    `web_target`/`tools_target` are the RESULTING state of each surface, or None to leave it
    as it is. That tri-state is what makes the surfaces additive: naming one surface says
    nothing about the other, unlike the declarative `--protect` this replaces.
    """

    output = get_global_output_handler()
    allow_ip = allow_ip or []
    allow_path = allow_path or []

    if rotate and password is not None:
        output.error(
            "--rotate cannot be combined with --password (either pick the password yourself or let fm mint one)",
            exception=typer.Exit(code=1),
        )

    allow_ips: list[str] = []
    if allow_ip:
        try:
            allow_ips = validate_cidrs(allow_ip)
        except ValueError as e:
            output.error(f"--allow-ip: {e}", exception=typer.Exit(code=1))

    for path in allow_path:
        if not path.startswith("/"):
            output.error(
                f"--allow-path must be an absolute path prefix like /api/method/ping, got {path!r}",
                exception=typer.Exit(code=1),
            )

    if password == "-":
        password = read_password_from_stdin()

    bench, site, entry = resolve_scope(ctx, address, output)

    if site and tools_named:
        output.error(
            f"--tools cannot take a site part: one Adminer and one Mailpit serve the whole bench, on every hostname '{bench.name}' has, so acting on them for '{site}' alone would leave the same tools open on the others. Run 'fm auth enable {bench.name} --tools' to protect them for the bench.",
            exception=typer.Exit(code=1),
        )

    # Per-site auth needs a per-site server block, but nginx conf renders once at the container's
    # first boot: an older bench's conf may include only `custom/*.conf`. Recording an override
    # there would be silent, and the status command would report a prompt nobody serves.
    if site and not bench.nginx_conf_serves_per_site():
        output.error(
            f"Bench '{bench.name}' nginx conf predates one server block per site, so '{site}' cannot carry auth of its own yet: nginx would include none of it and the site would keep following the bench. Run 'fm migrate' to re-render it, or recreate the nginx container with 'fm restart {bench.name} --nginx --recreate'. 'fm auth enable {bench.name}' for the whole bench works today.",
            exception=typer.Exit(code=1),
        )

    if site and entry is None:
        output.error(
            f"Bench '{bench.name}' records no entry for site '{site}', so its own auth has nowhere to live. Run 'fm auth enable {bench.name}' to set the auth every site of the bench follows.",
            exception=typer.Exit(code=1),
        )

    scope = f"{bench.name}/{site}" if site else bench.name
    stored = entry.auth if entry is not None else bench.bench_config.auth

    # Effective current state. For the bench, an absent [auth] table means the model defaults are
    # what it serves today (admin tools protected, site open). For a site with no auth of its own,
    # the starting point is what it currently serves -- the bench's -- so `--allow-path` alone on an
    # already-protected site does not silently turn its prompt off.
    current = stored or (bench.bench_config.auth_for(site) if site else AuthConfig())
    bench_tools = bench.bench_config.auth.tools if bench.bench_config.auth else AuthConfig().tools

    web_on = current.web if web_target is None else web_target
    tools_on = bench_tools if (tools_target is None or site) else tools_target

    if allow_path and not web_on:
        output.error(
            f"--allow-path exempts paths on the web surface only, which {scope} does not protect (add --web)",
            exception=typer.Exit(code=1),
        )

    # Only turning a surface ON adds exposure, so an idempotent re-run never gates.
    enabling_web = web_on and not current.web
    enabling_tools = tools_on and not bench_tools
    if (enabling_web or enabling_tools) and not insecure:
        # The hostname whose credentials are at stake: the named site's own, or the bench's primary.
        # Certificates are per hostname, so asking the primary about a named site would answer for
        # the wrong name and could clear a site that has no TLS at all.
        guarded_domain = site or bench.primary_domain
        certificate = (
            bench.bench_config.certificate_for(guarded_domain) if site else bench.bench_config.get_primary_certificate()
        )
        if certificate.ssl_type == SUPPORTED_SSL_TYPES.none:
            # Web is the new capability so plain http is refused outright; tools has served
            # /adminer/ and /mailpit/ over http since before this check existed and defaults on,
            # so refusing it too would break fm's own default state -- warn instead.
            if enabling_web:
                output.error(
                    # Certificate lookup is keyed by DOMAIN (`SSLCertificate.domain`), while the
                    # `fm ssl add` hint below takes BENCH then the domain separately.
                    f"Domain '{guarded_domain}' has no TLS certificate: basic auth sends the credentials base64-encoded on every request, so on the web surface they would travel in the clear in front of every path including /api. Add HTTPS with 'fm ssl add {bench.name}/{guarded_domain}', or pass --insecure to accept that.",
                    exception=typer.Exit(code=1),
                )
            output.warning(
                f"Domain '{guarded_domain}' has no TLS certificate: basic auth sends the credentials base64-encoded on every request, so the admin tools credentials are effectively cleartext (--insecure silences this)"
            )

    # Capability gate for the web surface only. nginx forwards the Authorization
    # header it just authenticated, frappe reads it as an API key and raises
    # AuthenticationError, so the image template strips it for authenticated
    # requests via `map $remote_user $fm_upstream_auth`. A conf rendered before
    # that fix would answer 401 to every authenticated request under web auth.
    # Checked whenever the result leaves web protected (unlike the TLS gate, this
    # is about the conf being able to serve web auth at all, not about newly
    # exposing credentials), and never for the tools surface, which is unaffected.
    if web_on:
        default_conf = bench.path / "configs" / "nginx" / "conf" / "conf.d" / "default.conf"
        # An absent conf is rendered fresh from the current image on next start.
        if default_conf.is_file() and "$fm_upstream_auth" not in default_conf.read_text():
            if bench.bench_config.runtime == BenchRuntime.image:
                remedy = f"Re-bake the bench image so nginx picks up the fix: 'fm bake {bench.name}' followed by 'fm switch {bench.name}'."
            else:
                remedy = f"Run 'fm migrate' to re-render it, or recreate the nginx container with 'fm restart {bench.name} --nginx --recreate'."
            output.error(
                f"Bench '{bench.name}' nginx conf ({default_conf}) predates the Authorization-header fix: with web auth on, nginx would forward the credentials it just checked and frappe would reject every authenticated request with 401. {remedy} The tools surface is unaffected, so 'fm auth enable {bench.name} --tools' works today.",
                exception=typer.Exit(code=1),
            )

    credentials_touched = user is not None or password is not None or rotate

    # A site taking auth of its OWN starts from no credentials of its own, even though `current`
    # holds the bench's while it was still inheriting: carrying that password over would mean the
    # bench password opens the site, which is the one thing per-site credentials exist to prevent.
    # An explicit --user/--password still wins, and a site that already has an entry keeps its own.
    baseline = WebAuthConfig() if (site and stored is None) else current

    # Minted on the FIRST record only, never on a scope that already has one: re-credentialling an
    # existing entry would lock out whoever holds it. Keyed on `stored is None` rather than on the
    # value, because a stored "admin" and the model's own default are indistinguishable here.
    minting_user = user is None and stored is None
    new_user = user if user is not None else (generate_password(12) if minting_user else baseline.user)
    new_password = baseline.password
    if password is not None:
        new_password = password
    elif rotate:
        new_password = generate_password()
    if new_password is None and (web_on or tools_on or credentials_touched):
        new_password = generate_password()

    if new_password is not None:
        try:
            validate_credentials(new_user, new_password)
        except ValueError as e:
            output.error(f"Invalid credentials: {e}", exception=typer.Exit(code=1))

    new_allow_ips = allow_ips if allow_ip else ([] if clear_exemptions else current.allow_ips)
    new_allow_paths = allow_path if allow_path else ([] if clear_exemptions else current.allow_paths)

    if site:
        # `tools` has no per-site field (WebAuthConfig only tracks it at the bench), so the
        # bench value is never folded in here. Named fields below always overwrite (clearing
        # `web` back to False must actually take), but WebAuthConfig is extra="allow": mutate
        # the loaded entry in place so a stray hand-edited key survives; rebuilding via
        # WebAuthConfig(**kwargs) would silently drop it.
        if entry.auth is not None:
            entry.auth.user = new_user
            entry.auth.password = new_password
            entry.auth.web = web_on
            entry.auth.allow_ips = new_allow_ips
            entry.auth.allow_paths = new_allow_paths
        else:
            entry.auth = WebAuthConfig(
                user=new_user,
                password=new_password,
                web=web_on,
                allow_ips=new_allow_ips,
                allow_paths=new_allow_paths,
            )
    elif bench.bench_config.auth is not None:
        # Same reasoning as the site branch above, for the bench's own [auth]: mutate the loaded
        # AuthConfig in place so a stray key survives, and only construct fresh below when the
        # bench has no [auth] table yet to preserve.
        # The credential follows the surface NAMED, not the bench: `--tools` writes the tools pair
        # and `--web` the web pair, so protecting one surface cannot silently re-credential an
        # already-protected other and lock out whoever holds it. Naming neither means both, which
        # is the common case and keeps a single shared credential.
        stored_auth = bench.bench_config.auth
        if credentials_touched and tools_named:
            stored_auth.tools_user = new_user
            stored_auth.tools_password = new_password
        else:
            stored_auth.user = new_user
            stored_auth.password = new_password
        stored_auth.web = web_on
        stored_auth.tools = tools_on
        stored_auth.allow_ips = new_allow_ips
        stored_auth.allow_paths = new_allow_paths
    else:
        bench.bench_config.auth = AuthConfig(
            user=new_user,
            password=new_password,
            web=web_on,
            tools=tools_on,
            allow_ips=new_allow_ips,
            allow_paths=new_allow_paths,
        )
    bench.save_bench_config(print_message=False)

    # Single owner of the htpasswd files and the nginx auth confs; reloads once.
    bench.ensure_fm_nginx_confs()

    applied = entry.auth if site else bench.bench_config.auth

    # ensure_fm_nginx_confs writes nothing for the tools surface on a bench whose
    # admin tools are off (there are no /adminer/ and /mailpit/ locations to gate), so
    # reporting it as protected without this would be a lie.
    if not site and applied.tools and not bench.bench_config.admin_tools:
        output.warning(
            f"Admin tools are disabled on {bench.name}, so nothing enforces the tools surface yet; it applies once you run 'fm tools enable {bench.name}'"
        )

    card = build_auth_card(
        bench,
        site,
        entry,
        scope,
        applied,
        hint_when_off=False,
        source=f"its own, overriding bench '{bench.name}'" if site else None,
    )
    output.print_data(card.render())
