from pathlib import Path

import typer

from frappe_manager.docker import DockerException
from frappe_manager.output_manager import get_global_output_handler
from frappe_manager.services_manager.services import ServicesManager
from frappe_manager.site_manager.modules.realip import PROXY_CONF_FILENAME, summarize_proxy_realip_conf


def info(ctx: typer.Context):
    """
    Show the global services' card: live container state, the root database credentials and the proxy's real-ip trust.

    The root database password is printed in cleartext. It belongs to the mariadb container every bench shares, which is why it is on this card and not on any bench's fm info.
    """
    from frappe_manager.output_manager import railcard

    services_manager: ServicesManager = ctx.obj["services"]
    output = get_global_output_handler()

    output.change_head("Getting services info")

    db = services_manager.database_manager.database_server_info

    # Same read entrypoint_checks makes: the union of declared services, each marked with the
    # live container's state, and "stopped" for a service whose container does not exist at all
    # (get_all_services_status only reports containers that exist).
    service_names = services_manager.compose_file_manager.get_services_list(exclude_disabled=True)
    try:
        containers = services_manager.compose_file_manager.get_container_names().values()
        all_statuses = services_manager.docker_client.compose.get_all_services_status()
        live = {status["Service"]: status["State"] for status in all_statuses if status.get("Name") in containers}
    except DockerException:
        live = {}
    statuses = {name: live.get(name, "stopped") for name in service_names}
    active = bool(statuses) and all(state == "running" for state in statuses.values())

    # Same grammar as railcard.bench_meta: state as a WORD first, tokens only enhance it.
    status_token = "fm.status.running" if active else "fm.status.stopped"
    status_word = "running" if active else "stopped"
    meta = f"[{status_token}]{status_word}[/{status_token}] [fm.muted]· global · shared by every bench[/fm.muted]"

    # Titled "services" because that is what the command group takes.
    card = railcard.Card("services", meta, active)
    abs_path = services_manager.path.absolute()
    card.fact("dir", f"[fm.muted][link=file://{abs_path}]{abs_path}[/link][/fm.muted]")

    # ---- access
    card.section("access")
    card.fact(
        "root db",
        f"{db.user} [fm.muted]/[/fm.muted] [fm.secret]{db.password}[/fm.secret] [fm.muted]@[/fm.muted] {db.host}",
    )

    # ---- proxy (the real-ip overlay fm services real-ip maintains)
    card.section("proxy")
    conf_path = Path(services_manager.proxy_storage.dirs.confd.host) / PROXY_CONF_FILENAME
    summary = summarize_proxy_realip_conf(conf_path.read_text()) if conf_path.exists() else None
    card.fact("real-ip", summary or "[fm.muted]not configured; see fm services real-ip[/fm.muted]")

    # ---- services (live container state, same shape as the bench card's section)
    card.section("services")
    dots = "   ".join(f"{railcard.status_dot(state)} {svc}" for svc, state in sorted(statuses.items()))
    card.fact("global", dots)

    output.print_data(card.render())
