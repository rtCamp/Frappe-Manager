"""Reading which domains are in maintenance.

Lives here rather than in `commands/maintenance/` so `fm info` can report the same fact without a
command importing a command: `fm info` said `running` for a bench answering 503 to every visitor,
because the only code that knew was behind the CLI verb that writes it.
"""

from frappe_manager.services_manager.proxy_dropins import ProxyDropins


def domains_in_maintenance(dropins: ProxyDropins, domains: list[str]) -> list[str]:
    """Those of ``domains`` whose maintenance fragment currently exists.

    Reads `ProxyDropins.active()` rather than any recorded flag: the fragment is what nginx
    actually serves from, so a config edited by hand or a half-finished enable is reported as it
    IS, not as fm last intended it. `active()` never raises on an unreadable path, so neither does
    this -- `fm info`'s job is reporting, and must survive a proxy directory it cannot read.
    """
    return [domain for domain in domains if "maintenance" in dropins.active(domain)]
