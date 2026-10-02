from typing import Annotated

import typer
from typer_examples import example

from frappe_manager.commands import check_bench_migration_required
from frappe_manager.commands.arguments import JsonResultOption
from frappe_manager.commands.maintenance._helpers import (
    _bench_domains,
    _extract_bench,
    _extract_code,
    _extract_scheme,
    _extract_token,
    _foreign_vhost_content,
    _maintenance_domains,
    conf_state,
    optional_bench_site_callback,
    proxy_paths,
)
from frappe_manager.output_manager import get_global_output_handler, railcard
from frappe_manager.site_manager.modules.public_scheme import host_proxy_state, public_scheme, public_url
from frappe_manager.utils.callbacks import bench_site_autocompletion_callback


@example(
    "Check one bench",
    "{benchname}",
    benchname="mybench",
)
@example(
    "Check one site's hostnames only",
    "{benchname}/shop.example.com",
    benchname="mybench",
)
@example(
    "See every domain in maintenance, across every bench",
    "",
)
def status(
    ctx: typer.Context,
    address: Annotated[
        str | None,
        typer.Argument(
            metavar="BENCH(/SITE)",
            help="Bench, or BENCH/SITE for one site's hostnames only. Omit it to list every domain in maintenance, across every bench.",
            autocompletion=bench_site_autocompletion_callback,
            callback=optional_bench_site_callback,
        ),
    ] = None,
    json_results: JsonResultOption = False,
):
    """
    Report maintenance state per domain, with the bypass URL.

    Writes nothing. Without a bench, lists every domain currently in maintenance across every bench.
    """

    output = get_global_output_handler()
    if json_results:
        output.set_json_results()
    benchname = address
    _services, dropins, _html_host_dir, _html_container_dir = proxy_paths(ctx)

    if benchname is None:
        by_bench: dict[str, list[tuple[str, str]]] = {}
        for domain in _maintenance_domains(dropins):
            text = dropins.fragment_path(domain, "maintenance").read_text()
            by_bench.setdefault(_extract_bench(text), []).append((domain, text))

        if output.wants_structured_data:
            # One flat row per domain, carrying the bench, because a caller asking the host-wide
            # question is asking WHICH domains and whose. Keys shared with the addressed payload
            # keep their meaning.
            output.print_data(
                [
                    {
                        "bench": bench,
                        "domain": domain,
                        "maintenance": True,
                        "code": _extract_code(text),
                        "bypass_token": _extract_token(text),
                        "bypass_url": f"{_extract_scheme(text)}://{domain}/fm-bypass/{_extract_token(text)}",
                        "bypass_off_url": f"{_extract_scheme(text)}://{domain}/fm-bypass/off",
                    }
                    for bench, entries in sorted(by_bench.items())
                    for domain, text in sorted(entries, key=lambda entry: entry[0])
                ]
            )
            return

        if not by_bench:
            # The healthy default across the whole host: nothing anywhere is in maintenance.
            output.print_data(railcard.Card("maintenance", "none in maintenance", active=True).render())
            return

        # One card per bench, not one per domain: a host with a dozen benches in maintenance at
        # once would otherwise be a wall of single-fact cards repeating the same bench name.
        items = []
        for bench, entries in sorted(by_bench.items()):
            count = len(entries)
            meta = "on" if count == 1 else f"on: {count} domains"
            card = railcard.Card(bench, f"[fm.status.stopped]{meta}[/fm.status.stopped]", active=False)
            for domain, text in sorted(entries, key=lambda entry: entry[0]):
                scheme = _extract_scheme(text)
                card.fact(domain, f"code {_extract_code(text)}")
                card.fact("", f"[fm.muted]bypass[/fm.muted] {scheme}://{domain}/fm-bypass/{_extract_token(text)}")
                card.fact("", f"[fm.muted]drop it[/fm.muted] {scheme}://{domain}/fm-bypass/off")
            items.append(card)
        output.print_data(railcard.cards(items))
        return

    check_bench_migration_required(benchname)

    site = ctx.obj.get("site") if ctx.obj else None
    domains, domain_ssl, _all_domains = _bench_domains(benchname, site)

    front, http_port, https_port = host_proxy_state()
    rows = []
    for domain in domains:
        base = public_url(domain, public_scheme(bool(domain_ssl.get(domain)), front), http_port, https_port)
        on = conf_state(dropins, domain)
        text = dropins.fragment_path(domain, "maintenance").read_text() if on else ""
        rows.append(
            {
                "domain": domain,
                "maintenance": bool(on),
                "code": _extract_code(text) if on else None,
                "bypass_url": f"{base}/fm-bypass/{_extract_token(text)}" if on else None,
                # The way OUT, beside the way in. `fm maintenance enable` prints both and status is
                # what an operator runs once that output is gone, so omitting it left them holding
                # a cookie with no documented way to drop it.
                "bypass_off_url": f"{base}/fm-bypass/off" if on else None,
                # A vhost.d file fm did not write. Worth reporting because it explains why an
                # enable will merge rather than create, but it is NOT the answer to "is this in
                # maintenance" and must not lead.
                "custom_vhost": (not on) and _foreign_vhost_content(dropins.vhostd_dir / domain),
            }
        )
    if output.wants_structured_data:
        output.print_data(rows)
        return
    on_count = sum(1 for row in rows if row["maintenance"])
    if on_count == 0:
        status_word = "off"
    elif len(rows) == 1:
        status_word = "on"
    elif on_count == len(rows):
        status_word = f"on: {len(rows)} domains"
    else:
        status_word = f"on: {on_count}/{len(rows)} domains"
    # Mirrors bench_meta's own choice to color maintenance with the "stopped" token: ON is the
    # attention state here (visitors see the maintenance page), not the healthy default.
    status_token = "fm.status.stopped" if on_count else "fm.status.running"
    scope = benchname if site is None else f"{benchname}/{site}"
    card = railcard.Card(scope, f"[{status_token}]{status_word}[/{status_token}]", active=on_count == 0)
    for row in rows:
        if row["maintenance"]:
            card.fact(
                row["domain"],
                f"on [fm.muted]·[/fm.muted] code {row['code']}",
            )
            card.fact("", f"[fm.muted]bypass[/fm.muted] {row['bypass_url']}")
            card.fact("", f"[fm.muted]drop it[/fm.muted] {row['bypass_off_url']}")
        elif row["custom_vhost"]:
            # Answer first, and in words an operator reading "is my site down" actually
            # understands: not fm's internal "custom vhost config" jargon.
            card.fact(row["domain"], "off [fm.muted](this domain already has nginx config fm did not write)[/fm.muted]")
        else:
            card.fact(row["domain"], "off")
    output.print_data(card.render())

