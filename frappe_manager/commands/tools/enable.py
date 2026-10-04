from typing import Annotated

import typer
from typer_examples import example

from frappe_manager.commands import check_bench_migration_required
from frappe_manager.commands.arguments import BenchSiteAllArgument
from frappe_manager.output_manager import get_global_output_handler, spinner
from frappe_manager.site_manager.site import Bench
from frappe_manager.utils.callbacks import RESERVED_BENCH_NAME

from ._helpers import route_sites


@example(
    "Start the admin tools containers for a bench",
    "{benchname}",
    detail="Seeds the compose file on first use; routing only, it does not protect the tools.",
    benchname="mybench",
)
@example(
    "Also make Mailpit the bench's default outgoing mail server",
    "{benchname} --mailpit-as-default-mail-server",
    benchname="mybench",
)
@example(
    "Route one site's hostnames to the already-running tools",
    "{benchname}/site1.localhost",
    detail="The bench's other sites and their existing routes are untouched.",
    benchname="mybench",
)
@example(
    "Route one site's mail to Mailpit, leaving the rest on their real mail server",
    "{benchname}/site1.localhost --mailpit-as-default-mail-server",
    detail="Writes the mail keys into that site's own site_config.json, which wins over the bench-wide config.",
    benchname="mybench",
)
@example(
    "Restore every opted-out site's route at once",
    "{benchname}/all",
    detail="Fans the route out over every site the bench serves; the containers were already running.",
    benchname="mybench",
)
def enable(
    ctx: typer.Context,
    address: BenchSiteAllArgument = None,
    mailpit_as_default_mail_server: Annotated[
        bool,
        typer.Option(
            "--mailpit-as-default-mail-server",
            help="Route outgoing mail to Mailpit: on BENCH for every site the bench holds (via common_site_config), on BENCH/SITE for that one site only (via its site_config.json). Applies when the site has no default outgoing Email Account configured in Frappe.",
            show_default=False,
        ),
    ] = False,
):
    """
    Start the admin tools (Adminer at /adminer, Mailpit at /mailpit), or route a site to them.

    BENCH starts the one container pair the bench has, seeding its compose file on first use; this only routes, so protecting what it starts is a separate 'fm auth enable BENCH --tools' call. BENCH/SITE only adds the routes for that site's hostnames, leaving the containers as they were; BENCH/all restores the routes for every site the bench serves.
    """

    services_manager = ctx.obj["services"]
    output = get_global_output_handler()
    check_bench_migration_required(address)

    # The site half of the address, put there by `bench_site_all_callback`.
    site = ctx.obj.get("site") if ctx.obj else None

    if site == RESERVED_BENCH_NAME and mailpit_as_default_mail_server:
        output.display_error(
            "--mailpit-as-default-mail-server cannot take 'all': use the bare BENCH address -- "
            "the bench-wide setting covers every site, including ones created later."
        )
        raise typer.Exit(1)

    bench = Bench.get_object(address, services_manager, output_handler=output)

    if site:
        route_sites(bench, site, output, wanted=True)
        if mailpit_as_default_mail_server:
            bench.admin_tools.configure_mailpit_for_site(site)
        return

    with spinner(output, "Enabling admin tools"):
        bench.bench_config.admin_tools = True

        if not bench.admin_tools.compose_file_manager.compose_path.exists():
            # Seeds the compose file and brings the tools up, but it carries no mail choice of its
            # own (it enables with force_configure defaulted to False), so the mail keys are
            # written right after it -- otherwise --mailpit-as-default-mail-server would be
            # silently dropped on every bench that never had admin tools, i.e. every -e prod one.
            bench.sync_admin_tools_compose()
            if mailpit_as_default_mail_server:
                bench.admin_tools.configure_mailpit_as_default_server()
        else:
            bench.admin_tools.enable(force_configure=mailpit_as_default_mail_server)

        # The tools vhost renders `auth_basic_user_file .../<bench>.htpasswd` only while the tools
        # surface is protected, so this writes the conf for whatever auth records; it never decides
        # the auth itself.
        bench.ensure_fm_nginx_confs()

    bench.save_bench_config()
    output.print("Enabled Admin-tools")

    # Warn across the concern, never act across it: `fm auth enable` gives the mirror warning when
    # auth claims the tools surface on a bench serving none. Adminer is a database console, so the
    # gap between routing it and protecting it is the operator's to close knowingly.
    auth = bench.bench_config.auth
    if auth is None or not auth.tools:
        output.warning(
            f"Nothing protects the admin tools yet; run 'fm auth enable {bench.name} --tools' to require a password"
        )
