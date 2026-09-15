"""Enable admin tools command."""

from typing import Annotated

import typer
from typer_examples import example

from frappe_manager.commands import check_bench_migration_required
from frappe_manager.commands.arguments import BenchSiteAllArgument
from frappe_manager.output_manager import get_global_output_handler, spinner
from frappe_manager.site_manager.site import Bench

from ._helpers import route_sites


@example(
    "Start the admin tools containers for a bench",
    "{benchname}",
    detail="Seeds the compose file on first use and mints the tools' htpasswd.",
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
            help="Route outgoing mail to Mailpit for every site the bench holds.",
            show_default=False,
        ),
    ] = False,
):
    """
    Start the admin tools (Adminer at /adminer, Mailpit at /mailpit), or route a site to them.

    BENCH starts the one container pair the bench has, seeding its compose file on first use and minting its htpasswd. BENCH/SITE only adds the routes for that site's hostnames, leaving the containers as they were; BENCH/all restores the routes for every site the bench serves.
    """

    services_manager = ctx.obj["services"]
    output = get_global_output_handler()
    check_bench_migration_required(address)

    # The site half of the address, put there by `bench_site_all_callback`.
    site = ctx.obj.get("site") if ctx.obj else None

    bench = Bench.get_object(address, services_manager, output_handler=output)

    if site:
        route_sites(bench, site, output, wanted=True)
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

        # The tools vhost renders `auth_basic_user_file .../<bench>.htpasswd`, but that file is
        # owned solely by ensure_fm_nginx_confs(), whose guard skips it while admin tools are off --
        # so on a bench created with tools disabled it is absent and the freshly enabled tools
        # surface answers HTTP 500. Mint it now that the surface exists.
        bench.ensure_fm_nginx_confs()

    bench.save_bench_config()
    output.print("Enabled Admin-tools")
