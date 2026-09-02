import sys
from enum import Enum
from typing import Annotated

import typer
from typer_examples import example

from frappe_manager.commands import check_bench_migration_required
from frappe_manager.output_manager import get_global_output_handler
from frappe_manager.site_manager.bench_config import AuthConfig, BenchRuntime, WebAuthConfig
from frappe_manager.site_manager.modules.auth import generate_password, validate_credentials
from frappe_manager.site_manager.modules.realip import validate_cidrs
from frappe_manager.site_manager.site import Bench
from frappe_manager.ssl_manager import SUPPORTED_SSL_TYPES
from frappe_manager.utils.callbacks import bench_site_autocompletion_callback, bench_site_callback

# Rich help panels for `fm auth --help`, grouped by concern.
_PANEL_SURFACES = "Surfaces (what asks for a password)"
_PANEL_CREDENTIALS = "Credentials"
_PANEL_EXEMPTIONS = "Exemptions (who skips the prompt)"
_PANEL_SAFETY = "Safety"


class AuthSurface(str, Enum):
    """The two independently protectable nginx surfaces of a bench."""

    web = "web"
    tools = "tools"


def _surface_summary(web: bool, tools: bool | None) -> str:
    """`tools` is None for a site scope, which has no tools surface to report: there is one
    Adminer and one Mailpit per bench, so the tools state belongs to the bench line, not a site's."""
    surfaces = [("web", web)] if tools is None else [("web", web), ("tools", tools)]
    protected = [name for name, on in surfaces if on]
    if not protected:
        return "off on the web surface" if tools is None else "off on both surfaces (web, tools)"
    return f"on for: {', '.join(protected)}"


def _read_password_from_stdin() -> str:
    """`--password -` keeps the secret out of the shell history: piped from
    stdin in scripts, prompted without echo on a terminal."""
    if sys.stdin.isatty():
        return typer.prompt("Password", hide_input=True)
    return sys.stdin.readline().rstrip("\r\n")


def _print_state(output, config: WebAuthConfig, hint_when_off: bool) -> None:
    """Surfaces first, then the detail that only means something while a surface
    is protected: on the all-off state credentials and exemptions are inert, and
    printing them reads as if something were still enforced. They stay in the
    config and reappear as soon as a surface is protected again."""
    tools = config.tools if isinstance(config, AuthConfig) else None
    output.print(f"Basic auth {_surface_summary(config.web, tools)}")
    if not (config.web or tools):
        if hint_when_off and (config.password or config.allow_ips or config.allow_paths):
            output.print("  credentials and exemptions stay stored and apply again when a surface is protected")
        return
    output.print(f"  user: {config.user}")
    if config.password:
        output.print(f"  password: {config.password}")
    if config.allow_ips:
        output.print(f"  no prompt from: {', '.join(config.allow_ips)}")
    if config.web and config.allow_paths:
        output.print(f"  no prompt on: {', '.join(config.allow_paths)}")


@example(
    "Password-protect the whole bench",
    "{benchname} --protect web",
    detail="Prompts for frappe and socketio, and prints the credentials. Turns the admin tools prompt off: add --protect tools to keep both.",
    benchname="mybench",
)
@example(
    "Protect the admin tools only",
    "{benchname} --protect tools",
    detail="Leaves the site open. This is a bench's default state.",
    benchname="mybench",
)
@example(
    "Protect one site of a bench",
    "{benchname}/b.example.com --protect web",
    detail="That site's hostnames prompt with credentials of its own; the bench's other sites keep serving exactly as before. A site with no auth of its own follows the bench, so a plain 'fm auth mybench --protect web' still covers every site.",
    benchname="mybench",
)
@example(
    "Set your own credentials",
    "{benchname} --protect web --protect tools --user alice --password -",
    detail="Reads the password from stdin, so it never lands in the shell history.",
    benchname="mybench",
)
@example(
    "Let a webhook through",
    "{benchname} --protect web --allow-path /api/method/payment_webhook",
    detail="Exempt paths replace the stored list; omitting the flag keeps it.",
    benchname="mybench",
)
@example(
    "Show the state, or remove the prompt",
    "{benchname} --status",
    detail="--off turns both surfaces off and keeps the credentials for later.",
    benchname="mybench",
)
def auth(
    ctx: typer.Context,
    address: Annotated[
        str | None,
        typer.Argument(
            metavar="BENCH(/SITE)",
            # NOT the shared `BenchSiteArgument` help. There a bare bench means the bench's primary
            # site; here it means the WHOLE bench, and an operator who read "primary site is used"
            # would think `fm auth shop --protect web` left the other sites open.
            help="Bench, or BENCH/SITE for one of its sites. Without a site part the whole bench is addressed: every site that has no auth of its own follows it.",
            autocompletion=bench_site_autocompletion_callback,
            callback=bench_site_callback,
        ),
    ] = None,
    protect: Annotated[
        list[AuthSurface],
        typer.Option(
            "--protect",
            help="Surface that asks for the password (repeatable). web = frappe and socketio, tools = /adminer/ and /mailpit/.",
            show_default=False,
            rich_help_panel=_PANEL_SURFACES,
        ),
    ] = [],
    off: Annotated[
        bool,
        typer.Option(
            "--off",
            help="Turn the prompt off on both surfaces, keeping the credentials.",
            rich_help_panel=_PANEL_SURFACES,
        ),
    ] = False,
    status: Annotated[
        bool,
        typer.Option(
            "--status",
            help="Report which surfaces are protected, with the credentials and allow lists while a surface is protected. Writes nothing.",
        ),
    ] = False,
    user: Annotated[
        str | None,
        typer.Option(
            "--user",
            help="Basic auth username for the scope you named: both surfaces of the bench, or that one site. Defaults to 'admin'.",
            show_default=False,
            rich_help_panel=_PANEL_CREDENTIALS,
        ),
    ] = None,
    password: Annotated[
        str | None,
        typer.Option(
            "--password",
            help="Basic auth password. Pass - to read it from stdin, keeping it out of the shell history. A random one is minted on the first enable.",
            show_default=False,
            rich_help_panel=_PANEL_CREDENTIALS,
        ),
    ] = None,
    rotate: Annotated[
        bool,
        typer.Option(
            "--rotate",
            help="Replace the password with a fresh random one, invalidating browser sessions that cached the old one.",
            rich_help_panel=_PANEL_CREDENTIALS,
        ),
    ] = False,
    allow_ip: Annotated[
        list[str],
        typer.Option(
            "--allow-ip",
            help="Address or CIDR that skips the prompt (repeatable; replaces the stored list). Behind a CDN this needs real-IP forwarding, see fm self real-ip.",
            show_default=False,
            rich_help_panel=_PANEL_EXEMPTIONS,
        ),
    ] = [],
    allow_path: Annotated[
        list[str],
        typer.Option(
            "--allow-path",
            help="Absolute path prefix served without a prompt, e.g. /api/method/payment_webhook (repeatable; replaces the stored list). Web surface only.",
            show_default=False,
            rich_help_panel=_PANEL_EXEMPTIONS,
        ),
    ] = [],
    clear_exemptions: Annotated[
        bool,
        typer.Option(
            "--clear-exemptions",
            help="Empty both allow lists. Applied before any --allow-ip/--allow-path in the same call.",
            rich_help_panel=_PANEL_EXEMPTIONS,
        ),
    ] = False,
    insecure: Annotated[
        bool,
        typer.Option(
            "--insecure",
            help="Protect the web surface on a bench without TLS anyway, and silence the same warning on the tools surface.",
            rich_help_panel=_PANEL_SAFETY,
        ),
    ] = False,
):
    """
    Put an HTTP basic auth prompt in front of a bench: the site, the admin tools, or both.

    --protect is declarative: the surfaces you pass become the resulting state, and a bench starts with the admin tools prompting and the site open, so --protect web alone also turns the tools prompt off; name both surfaces to keep both. Credentials and allow lists are kept when a surface goes off, so re-enabling asks for nothing. A bare fm auth BENCH reports the state.

    BENCH/SITE protects the web surface of one site, with credentials of its own, and leaves the bench's other sites serving as before. A site with no auth of its own follows the bench, so fm auth BENCH still covers every site. --protect tools takes no site part: one Adminer and one Mailpit serve the whole bench, on every hostname it has.

    Basic auth sends credentials base64-encoded, not encrypted, so on a bench without TLS they are effectively cleartext: protecting the web surface there needs --insecure. The certificate checked is the one for the hostname you named.
    """

    output = get_global_output_handler()

    if off and protect:
        output.error(
            "--off cannot be combined with --protect (--off turns both surfaces off; use --protect alone to pick which stay on)",
            exception=typer.Exit(code=1),
        )

    if off and status:
        output.error("--off cannot be combined with --status (--status never writes)", exception=typer.Exit(code=1))

    if rotate and password is not None:
        output.error(
            "--rotate cannot be combined with --password (either pick the password yourself or let fm mint one)",
            exception=typer.Exit(code=1),
        )

    writes = (
        bool(protect)
        or off
        or user is not None
        or password is not None
        or rotate
        or bool(allow_ip)
        or bool(allow_path)
        or clear_exemptions
    )

    if status and writes:
        output.error(
            "--status cannot be combined with --protect/--off/--user/--password/--rotate/--allow-ip/--allow-path/--clear-exemptions (--status only reports)",
            exception=typer.Exit(code=1),
        )

    if not writes and not status and insecure:
        output.error(
            "Nothing to do: --insecure only relaxes the TLS check; pass --protect web/--protect tools, --off, a credential flag or an exemption flag (or use --status)",
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
        password = _read_password_from_stdin()

    check_bench_migration_required(address)

    services_manager = ctx.obj["services"]
    bench = Bench.get_object(address, services_manager, output_handler=output)
    site = ctx.obj.get("site")

    # A site part cannot narrow the tools surface. There is one Adminer and one Mailpit per bench
    # and both answer on every hostname it serves, so protecting them for one site would leave the
    # SAME tools reachable unprotected on its neighbours: one of two doors into the same room.
    # Refused rather than quietly applied bench-wide, which is the version an operator finds out
    # about only when it matters.
    if site and AuthSurface.tools in protect:
        output.error(
            f"--protect tools cannot take a site part: one Adminer and one Mailpit serve the whole bench, on every hostname '{bench.name}' has, so protecting them for '{site}' alone would leave the same tools open on the others. Run 'fm auth {bench.name} --protect tools' to protect them for the bench.",
            exception=typer.Exit(code=1),
        )

    # Per-site auth is a per-site server block, and the bench's conf is rendered once at the nginx
    # container's first boot: a bench can be running this code against a conf that includes only
    # `custom/*.conf`. Recording an override there would be silent -- nginx reads none of it, the
    # site keeps following the bench, and --status would report a prompt nobody serves.
    if site and writes and not bench.nginx_conf_serves_per_site():
        output.error(
            f"Bench '{bench.name}' nginx conf predates one server block per site, so '{site}' cannot carry auth of its own yet: nginx would include none of it and the site would keep following the bench. Run 'fm migrate' to re-render it, or recreate the nginx container with 'fm restart {bench.name} --nginx --container'. 'fm auth {bench.name}' for the whole bench works today.",
            exception=typer.Exit(code=1),
        )

    entry = (bench.bench_config.sites or {}).get(site) if site else None
    if site and entry is None:
        output.error(
            f"Bench '{bench.name}' records no entry for site '{site}', so its own auth has nowhere to live. Run 'fm auth {bench.name}' to set the auth every site of the bench follows.",
            exception=typer.Exit(code=1),
        )

    scope = f"{bench.name}/{site}" if site else bench.name
    stored = entry.auth if entry is not None else bench.bench_config.auth

    if not writes:
        # Bare `fm auth BENCH`, `fm auth BENCH/SITE` and `--status` all land here: report only.
        if stored is None:
            if site:
                # Not "unconfigured": the site IS protected or not, by the bench's setting. Report
                # what it actually serves, and say where the answer came from.
                output.print(f"Basic auth for {site}: inherited from bench '{bench.name}'")
                _print_state(output, bench.bench_config.auth_for(site), hint_when_off=True)
                output.print(f"  give this site its own with 'fm auth {scope} --protect web'")
                return
            output.print("Basic auth: not configured; bench defaults apply (tools protected, web open)")
            output.print(f"Protect a surface with 'fm auth {bench.name} --protect web' to mint credentials")
            return
        if site:
            output.print(f"Basic auth for {site}: its own, overriding bench '{bench.name}'")
        _print_state(output, stored, hint_when_off=True)
        return

    # Effective current state. For the bench, an absent [auth] table means the model defaults are
    # what it serves today (admin tools protected, site open). For a site with no auth of its own,
    # the starting point is what it currently serves -- the bench's -- so `--allow-path` alone on an
    # already-protected site does not silently turn its prompt off.
    current = stored or (bench.bench_config.auth_for(site) if site else AuthConfig())

    web_on = current.web
    tools_on = bench.bench_config.auth.tools if bench.bench_config.auth else AuthConfig().tools
    if not site:
        tools_on = current.tools if isinstance(current, AuthConfig) else tools_on
    if off:
        web_on = False
        if not site:
            tools_on = False
    elif protect:
        wanted = {surface.value for surface in protect}
        web_on = AuthSurface.web.value in wanted
        if not site:
            tools_on = AuthSurface.tools.value in wanted

    if allow_path and not web_on:
        output.error(
            f"--allow-path exempts paths on the web surface only, which {scope} does not protect (add --protect web)",
            exception=typer.Exit(code=1),
        )

    # Only turning a surface ON adds exposure, so an idempotent re-run never gates.
    enabling_web = web_on and not current.web
    bench_tools = bench.bench_config.auth.tools if bench.bench_config.auth else AuthConfig().tools
    enabling_tools = tools_on and not bench_tools
    if (enabling_web or enabling_tools) and not insecure:
        # The hostname whose credentials are at stake: the named site's own, or the bench's primary.
        # Certificates are per hostname, so asking the primary about a named site would answer for
        # the wrong name and could clear a site that has no TLS at all.
        guarded_domain = site or bench.primary_domain
        certificate = bench.bench_config.certificate_for(guarded_domain) if site else bench.bench_config.get_primary_certificate()
        if certificate.ssl_type == SUPPORTED_SSL_TYPES.none:
            # The web surface is the new capability and gates every path including
            # /api, so plain http is refused outright. The tools surface has served
            # /adminer/ and /mailpit/ behind basic auth over plain http since long
            # before this command and AuthConfig defaults it on, so refusing there
            # would refuse fm's own default state: warn and proceed.
            if enabling_web:
                output.error(
                    # Two roles in one sentence: the certificate is keyed by DOMAIN (that is what a
                    # browser validates and what `SSLCertificate.domain` holds), while `fm ssl add`
                    # takes the BENCH as its first positional and the hostname separately.
                    f"Domain '{guarded_domain}' has no TLS certificate: basic auth sends the credentials base64-encoded on every request, so on the web surface they would travel in the clear in front of every path including /api. Add HTTPS with 'fm ssl add {bench.name} {guarded_domain}', or pass --insecure to accept that.",
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
                remedy = f"Run 'fm migrate' to re-render it, or recreate the nginx container with 'fm restart {bench.name} --nginx --container'."
            output.error(
                f"Bench '{bench.name}' nginx conf ({default_conf}) predates the Authorization-header fix: with web auth on, nginx would forward the credentials it just checked and frappe would reject every authenticated request with 401. {remedy} The tools surface is unaffected, so 'fm auth {bench.name} --protect tools' works today.",
                exception=typer.Exit(code=1),
            )

    credentials_touched = user is not None or password is not None or rotate

    # A site taking auth of its OWN starts from no credentials of its own, even though `current`
    # holds the bench's while it was still inheriting: carrying that password over would mean the
    # bench password opens the site, which is the one thing per-site credentials exist to prevent.
    # An explicit --user/--password still wins, and a site that already has an entry keeps its own.
    baseline = WebAuthConfig() if (site and stored is None) else current

    new_user = user if user is not None else baseline.user
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

    if site:
        # The site's own entry, so its credentials are its own: a password handed out for one site
        # is not a password to another. `tools` is absent from the model on purpose (WebAuthConfig),
        # which is why the bench's value above was never folded in here.
        entry.auth = WebAuthConfig(
            user=new_user,
            password=new_password,
            web=web_on,
            allow_ips=allow_ips if allow_ip else ([] if clear_exemptions else current.allow_ips),
            allow_paths=allow_path if allow_path else ([] if clear_exemptions else current.allow_paths),
        )
    else:
        bench.bench_config.auth = AuthConfig(
            user=new_user,
            password=new_password,
            web=web_on,
            tools=tools_on,
            allow_ips=allow_ips if allow_ip else ([] if clear_exemptions else current.allow_ips),
            allow_paths=allow_path if allow_path else ([] if clear_exemptions else current.allow_paths),
        )
    bench.save_bench_config(print_message=False)

    # Single owner of the htpasswd files and the nginx auth confs; reloads once.
    bench.ensure_fm_nginx_confs()

    applied = entry.auth if site else bench.bench_config.auth
    if site:
        output.print(f"Basic auth for {site} (its own, overriding bench '{bench.name}')")

    if credentials_touched and not web_on and (site or not tools_on):
        surfaces = "--protect web" if site else "--protect web and/or --protect tools"
        output.warning(f"Credentials saved but nothing enforces them: no surface is protected, pass {surfaces}")

    # ensure_fm_nginx_confs writes nothing for the tools surface on a bench whose
    # admin tools are off (there are no /adminer/ and /mailpit/ locations to gate), so
    # reporting it as protected without this would be a lie.
    if not site and applied.tools and not bench.bench_config.admin_tools:
        output.warning(
            f"Admin tools are disabled on {bench.name}, so nothing enforces the tools surface yet; it applies once you run 'fm update {bench.name} --admin-tools enable'"
        )

    _print_state(output, applied, hint_when_off=False)
