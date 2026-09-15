"""Shared address-scope guards for `fm tools enable`/`fm tools disable`.

Extracted so the two verbs stay symmetric: the site half of a `BENCH/SITE|all` address only ever
routes or unroutes the running containers, never starts or stops them, and both directions share
the exact same guards.
"""

import typer

from frappe_manager.output_manager import OutputHandler, spinner
from frappe_manager.site_manager.site import Bench
from frappe_manager.utils.callbacks import RESERVED_BENCH_NAME


def route_sites(bench: Bench, site: str, output: OutputHandler, *, wanted: bool) -> None:
    """Route or unroute the bench's admin tools for the site half of the address.

    Same guards `fm update --admin-tools BENCH/SITE` used (`update.py:629-652`): every targeted
    site must already be recorded, the bench's nginx conf must carry one server block per site (so
    a per-site drop-in is actually read), and enabling refuses when the bench's tools are not
    running -- routing a hostname at a stopped container pair is a 502, not an enable.
    """
    recorded = bench.bench_config.sites or {}
    fanning = site == RESERVED_BENCH_NAME
    targets = list(bench.bench_config.site_names) if fanning else [site]

    missing = [s for s in targets if s not in recorded]
    if missing:
        output.display_error(
            f"Bench '{bench.name}' records no entry for site {', '.join(repr(s) for s in missing)}, "
            "so there is nowhere to store tool routing."
        )
        raise typer.Exit(1)

    if not bench.nginx_conf_serves_per_site():
        output.display_error(
            f"Bench '{bench.name}' nginx conf predates one server block per site, so tool routing "
            "cannot be set per site yet: nginx would include none of it and the tools would answer "
            "on no hostname at all. Run 'fm migrate' to re-render it, or recreate the nginx "
            f"container with 'fm restart {bench.name} --nginx --container'. "
            f"'fm tools enable {bench.name}' for the whole bench works today."
        )
        raise typer.Exit(1)

    label = "every site" if fanning else site
    if wanted and not bench.bench_config.admin_tools:
        # Nothing to route to: routing a hostname at a stopped container is a 502.
        output.display_error(
            f"Admin tools are disabled on {bench.name}, so there is nothing to route from {label}. "
            f"Start them for the bench first with 'fm tools enable {bench.name}'."
        )
        raise typer.Exit(1)

    with spinner(output, f"{'Routing' if wanted else 'Unrouting'} admin tools for {label}"):
        for target in targets:
            recorded[target].serve_admin_tools = wanted

        # Creating and removing the location files is the command's job; ensure_fm_nginx_confs only
        # REFRESHES ones already on disk, so a site getting its first one renders here.
        bench.admin_tools.save_nginx_location_config()
        try:
            bench.bench_nginx_controller.reload()
        except Exception as e:
            output.warning(f"Config written but nginx did not reload, so it applies on next start: {e}")

    bench.save_bench_config()

    output.print(
        f"/adminer/ and /mailpit/ {'now answer' if wanted else 'no longer answer'} on {label} and "
        f"{'their' if fanning else 'its'} aliases"
    )
    if fanning and not wanted:
        # Otherwise this reads as `fm tools disable BENCH`, and the running containers look like
        # the command failing to do what it said.
        output.print(
            f"The Adminer and Mailpit containers are still running; stop them with "
            f"'fm tools disable {bench.name}'"
        )
