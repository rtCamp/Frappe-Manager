"""`fm services info --json` structured payload (`build_services_info_data`).

Same defect class as `fm info`: the card's rich `Group` used to leak a memory address through
`json.dumps(default=str)`. Defended here: the payload carries plain facts (raw service state,
int byte sizes, real-ip ranges/header instead of the rendered sentence), the root db credentials
are nested by engine name (not a flat `root_db`, so a second engine is a new key, not a rename),
and a handler that does not ask for structured data still gets the rendered card untouched.
"""

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import typer

from frappe_manager.commands.services.info import build_services_info_data
from frappe_manager.commands.services.info import info as services_info
from frappe_manager.site_manager.modules.realip import PROXY_CONF_FILENAME, build_proxy_realip_conf


class _CardSpy:
    """Stand-in for railcard.Card: enough surface for `info()` to run without touching Rich."""

    def __init__(self, *a, **k):
        pass

    def section(self, *a, **k):
        return self

    def fact(self, *a, **k):
        return self

    def render(self):
        return "<rendered services>"


class _FakeHandler:
    """Records the payload `info()` hands to `print_data`, without a real OutputHandler."""

    def __init__(self, wants_structured_data: bool):
        self.wants_structured_data = wants_structured_data
        self.printed = None

    def change_head(self, *a, **k):
        pass

    def print_data(self, payload):
        self.printed = payload


class ServicesInfoHarness:
    def __init__(self, tmp_path: Path, *, statuses=None, all_services=None):
        self.confd = tmp_path / "confd"
        self.confd.mkdir(parents=True)

        self.services = MagicMock(name="services_manager")
        self.services.path = tmp_path / "services"
        self.services.database_manager.database_server_info = SimpleNamespace(
            user="root", password="rootpass", host="mariadb", port=3306
        )
        self.services.proxy_storage.dirs.confd.host = str(self.confd)
        declared = ["mariadb", "nginx-proxy"]
        self.services.compose_file_manager.get_services_list.side_effect = (
            lambda exclude_disabled=False: declared if not exclude_disabled else (all_services or declared)
        )
        self.services.compose_file_manager.get_container_names.return_value = {
            "mariadb": "fm__mariadb",
            "nginx-proxy": "fm__nginx-proxy",
        }
        default = [
            {"Service": "mariadb", "State": "running", "Name": "fm__mariadb"},
            {"Service": "nginx-proxy", "State": "running", "Name": "fm__nginx-proxy"},
        ]
        self.services.docker_client.compose.get_all_services_status.return_value = (
            default if statuses is None else statuses
        )

        from frappe_manager.metadata_manager import FMPruneConfig

        self.fm_config = MagicMock(name="fm_config_manager")
        self.fm_config.prune = FMPruneConfig()

        self.ctx = MagicMock(spec=typer.Context)
        self.ctx.obj = {"services": self.services, "fm_config_manager": self.fm_config}

    def run(self, monkeypatch, *, wants_structured_data: bool) -> _FakeHandler:
        from frappe_manager.output_manager import railcard

        monkeypatch.setattr(railcard, "Card", _CardSpy)
        handler = _FakeHandler(wants_structured_data)
        monkeypatch.setattr(
            "frappe_manager.commands.services.info.get_global_output_handler", lambda: handler
        )
        services_info(self.ctx)
        return handler


def test_json_payload_nests_the_root_db_credentials_by_engine(tmp_path, monkeypatch):
    """Nested under `database_servers.mariadb`, not a flat `root_db`: a second engine (postgres)
    is then a new key, never a breaking rename of this one."""
    handler = ServicesInfoHarness(tmp_path).run(monkeypatch, wants_structured_data=True)

    assert handler.printed["database_servers"] == {
        "mariadb": {"user": "root", "password": "rootpass", "host": "mariadb", "port": 3306}
    }


def test_json_payload_service_states_are_the_raw_strings(tmp_path, monkeypatch):
    """The card renders a status dot; the payload carries the bare docker state string."""
    handler = ServicesInfoHarness(
        tmp_path, statuses=[{"Service": "mariadb", "State": "running", "Name": "fm__mariadb"}]
    ).run(monkeypatch, wants_structured_data=True)

    assert handler.printed["services"] == {"mariadb": "running", "nginx-proxy": "stopped"}
    assert handler.printed["status"] == "inactive"


def test_json_payload_real_ip_carries_raw_ranges_and_header_not_a_sentence(tmp_path, monkeypatch):
    h = ServicesInfoHarness(tmp_path)
    (h.confd / PROXY_CONF_FILENAME).write_text(
        build_proxy_realip_conf(["203.0.113.0/24", "2400:cb00::/32"], "CF-Connecting-IP", recursive=True)
    )

    handler = h.run(monkeypatch, wants_structured_data=True)

    real_ip = handler.printed["proxy"]["real_ip"]
    assert real_ip == {
        "configured": True,
        "ranges": ["203.0.113.0/24", "2400:cb00::/32"],
        "header": "CF-Connecting-IP",
        "recursive": True,
    }


def test_json_payload_real_ip_reports_not_configured_for_a_foreign_conf(tmp_path, monkeypatch):
    """A hand-written file with no fm marker must read as unconfigured, never described as fm's."""
    h = ServicesInfoHarness(tmp_path)
    (h.confd / PROXY_CONF_FILENAME).write_text("set_real_ip_from 10.0.0.0/8;\n")

    handler = h.run(monkeypatch, wants_structured_data=True)

    assert handler.printed["proxy"]["real_ip"] == {
        "configured": False,
        "ranges": [],
        "header": None,
        "recursive": False,
    }


def test_json_payload_disk_sizes_are_ints(tmp_path, monkeypatch):
    handler = ServicesInfoHarness(tmp_path).run(monkeypatch, wants_structured_data=True)

    disk = handler.printed["disk"]
    assert isinstance(disk["stale_backup_bytes"], int)
    assert isinstance(disk["log_rotate_threshold_bytes"], int)
    assert isinstance(disk["actionable"], bool)


def test_json_payload_has_no_rich_object_or_path_values(tmp_path, monkeypatch):
    import json

    handler = ServicesInfoHarness(tmp_path).run(monkeypatch, wants_structured_data=True)

    dumped = json.dumps(handler.printed)
    assert "object at 0x" not in dumped


def test_a_handler_that_does_not_want_structured_data_still_gets_the_rendered_card(tmp_path, monkeypatch):
    """Regression guard: the branch must not touch the default (Rich) path."""
    handler = ServicesInfoHarness(tmp_path).run(monkeypatch, wants_structured_data=False)

    assert handler.printed == "<rendered services>"


def test_build_services_info_data_never_hardcodes_a_single_engine_key_name(tmp_path):
    """Documents the follow-up: the field name is `database_servers` (a dict keyed by engine),
    not `root_db`/`database_server` -- so adding postgres later is a new key, not a rename."""
    services = MagicMock()
    services.path = tmp_path
    services.database_manager.database_server_info = SimpleNamespace(
        user="root", password="pw", host="mariadb", port=3306
    )
    services.proxy_storage.dirs.confd.host = str(tmp_path)

    from frappe_manager.metadata_manager import FMPruneConfig

    data = build_services_info_data(services, {"mariadb": "running"}, [], True, FMPruneConfig())

    assert set(data["database_servers"]) == {"mariadb"}
