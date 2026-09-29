from pathlib import Path

import typer

from frappe_manager.commands.arguments import JsonResultOption
from frappe_manager.docker import DockerException
from frappe_manager.metadata_manager import FMConfigManager
from frappe_manager.output_manager import get_global_output_handler
from frappe_manager.services_manager.services import ServicesManager
from frappe_manager.site_manager.modules.realip import (
    PROXY_CONF_FILENAME,
    is_fm_realip_conf,
    summarize_proxy_realip_conf,
)


def _trusted_proxies_data(conf_path: Path) -> dict:
    """Structured counterpart of ``summarize_proxy_realip_conf``: raw ranges/header, not a
    rendered sentence. ``None``/empty fields mean "not configured", same rule the sentence form
    uses to never describe a hand-written file as fm's."""
    text = conf_path.read_text() if conf_path.exists() else ""
    if not text or not is_fm_realip_conf(text):
        return {"configured": False, "ranges": [], "header": None, "recursive": False}
    lines = text.splitlines()
    ranges = [
        line.removeprefix("set_real_ip_from ").rstrip(";").strip()
        for line in lines
        if line.startswith("set_real_ip_from ")
    ]
    header = next(
        (
            line.removeprefix("real_ip_header ").rstrip(";").strip()
            for line in lines
            if line.startswith("real_ip_header ")
        ),
        None,
    )
    recursive = any(line.startswith("real_ip_recursive") for line in lines)
    return {"configured": True, "ranges": ranges, "header": header, "recursive": recursive}


def build_services_info_data(services_manager: ServicesManager, statuses: dict, disabled: list, active: bool, prune_cfg) -> dict:
    """Structured facts for ``fm services info --json``, mirroring the card built in ``info()``.

    Only the mariadb root server is reported: fm's second engine (postgres, see
    ``ServicesManager.database_server_info_for``) has no root endpoint surfaced on this card yet.
    Nested under ``database_servers`` (keyed by engine) rather than a flat ``root_db`` so adding
    postgres later is a new key, not a breaking rename.
    """
    from frappe_manager.migration_manager import backup_manager
    from frappe_manager.utils.prune import parse_size, plan_log_prune, plan_session_prune

    db = services_manager.database_manager.database_server_info
    conf_path = Path(services_manager.proxy_storage.dirs.confd.host) / PROXY_CONF_FILENAME
    fm_config = FMConfigManager.import_from_toml()

    stale_backup_sessions = 0
    stale_backup_bytes = 0
    kept_backup_sessions = 0
    for root in [backup_manager.CLI_MIGARATIONS_DIR / "migrations"]:
        plan = plan_session_prune(root, prune_cfg.keep_backup_sessions)
        stale_backup_sessions += plan.count
        stale_backup_bytes += plan.size
        kept_backup_sessions += plan.kept

    over_bytes = parse_size(prune_cfg.rotate_logs_over)
    log_plan = plan_log_prune(
        [services_manager.path / "mariadb" / "logs", services_manager.path / "nginx-proxy" / "logs"],
        over_bytes,
        prune_cfg.keep_log_archives,
    )

    return {
        "status": "active" if active else "inactive",
        "dir": str(services_manager.path.absolute()),
        "database_servers": {
            "mariadb": {"user": db.user, "password": db.password, "host": db.host, "port": db.port},
        },
        "proxy": {
            "trusted_proxies": _trusted_proxies_data(conf_path),
            "ports": {
                "http": fm_config.proxy.http_port,
                "https": fm_config.proxy.https_port,
                "bind": fm_config.proxy.bind,
            },
        },
        "services": statuses,
        "disabled_services": disabled,
        "disk": {
            "stale_backup_sessions": stale_backup_sessions,
            "stale_backup_bytes": stale_backup_bytes,
            "kept_backup_sessions": kept_backup_sessions,
            "logs_over_threshold": len(log_plan.rotations),
            "log_rotate_threshold_bytes": over_bytes,
            "log_rotate_bytes": log_plan.rotate_size,
            "actionable": bool(stale_backup_sessions or log_plan.rotations),
        },
    }

def info(ctx: typer.Context, json_result: JsonResultOption = False):
    """
    Show the global services' card: live container state, the root database credentials, the ports the proxy publishes on and which proxies in front of it are trusted.

    The root database password is printed in cleartext. It belongs to the mariadb container every bench shares, which is why it is on this card and not on any bench's fm info.
    """
    from frappe_manager.output_manager import railcard

    services_manager: ServicesManager = ctx.obj["services"]
    output = get_global_output_handler()
    if json_result:
        output.set_json_results()

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

    card.section("access")
    card.fact(
        "root db",
        f"{db.user} [fm.muted]/[/fm.muted] [fm.secret]{db.password}[/fm.secret] [fm.muted]@[/fm.muted] {db.host}",
    )

    card.section("proxy")
    conf_path = Path(services_manager.proxy_storage.dirs.confd.host) / PROXY_CONF_FILENAME
    summary = summarize_proxy_realip_conf(conf_path.read_text()) if conf_path.exists() else None
    proxy_cfg = FMConfigManager.import_from_toml().proxy
    where = f"{proxy_cfg.bind} " if proxy_cfg.bind else ""
    card.fact("ports", f"{where}{proxy_cfg.http_port} [fm.muted]http[/fm.muted]   {proxy_cfg.https_port} [fm.muted]https[/fm.muted]")
    card.fact(
        "trusted proxies",
        summary or "[fm.muted]none; every request is judged on the connection fm received[/fm.muted]",
    )

    card.section("services")
    dots = "   ".join(f"{railcard.status_dot(state)} {svc}" for svc, state in sorted(statuses.items()))
    card.fact("global", dots)

    # A service fm has switched off is absent from `statuses` above, and a card that simply omits
    # it reads as "fm forgot about the database". Named, with the reason, because an operator who
    # went looking for mariadb needs to know it is off ON PURPOSE and what turns it back on.
    disabled = [
        name
        for name in services_manager.compose_file_manager.get_services_list()
        if name not in statuses
    ]
    if disabled:
        card.fact("off", ", ".join(sorted(disabled)))
        card.fact("", "[fm.muted]every site here uses an external database; starts when one needs it[/fm.muted]")

    # CLI_MIGARATIONS_DIR read as a module attribute: the test suite repoints it away from
    # the developer's real ~/frappe/backups (see tests/conftest.py).
    from frappe_manager.migration_manager import backup_manager
    from frappe_manager.utils.prune import parse_size, summarize_disk_status

    prune_cfg = ctx.obj["fm_config_manager"].prune
    summary, actionable = summarize_disk_status(
        session_roots=[backup_manager.CLI_MIGARATIONS_DIR / "migrations"],
        log_dirs=[services_manager.path / "mariadb" / "logs", services_manager.path / "nginx-proxy" / "logs"],
        keep_sessions=prune_cfg.keep_backup_sessions,
        keep_archives=prune_cfg.keep_log_archives,
        over_bytes=parse_size(prune_cfg.rotate_logs_over),
    )
    card.section("disk")
    if actionable:
        card.fact("status", f"{summary}  [fm.info]fm services prune[/fm.info]")
    else:
        card.fact("status", f"[fm.muted]{summary}[/fm.muted]")

    # `wants_structured_data` (JSONOutputHandler, and --json through the logging wrapper) asks
    # for facts instead of the card: a memory-address repr otherwise, since a rich Group/Table
    # has no __str__ for json.dumps(default=str) to fall back on.
    if output.wants_structured_data:
        output.print_data(build_services_info_data(services_manager, statuses, sorted(disabled), active, prune_cfg))
    else:
        output.print_data(card.render())
