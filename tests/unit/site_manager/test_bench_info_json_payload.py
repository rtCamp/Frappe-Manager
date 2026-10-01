"""`fm info --json` structured payload (`BenchInfo.build_bench_info_data`).

`display_info` renders a rich card whose facts are markup strings and glyphs
(`● running`, `"not enabled"`, `"1.8 GB"`). The JSON payload defended here must carry the
same facts as plain data instead: service state as the raw string a docker inspect returns,
sizes as ints, booleans as booleans -- never a rendered display string, and never the bare
`display_info(); print_data(card.render())` path that used to leak `<Group object at 0x...>`
through `json.dumps(default=str)`.
"""

from types import SimpleNamespace
from unittest.mock import MagicMock

from frappe_manager.site_manager.bench_config import (
    BenchConfig,
    BenchRuntime,
    FMBenchEnvType,
    resolve_primary_site,
)
from frappe_manager.site_manager.modules.bench_info import BenchInfo

BENCH = "bench.localhost"
SITE = "web.example.com"
ADMIN_PW = "admin-pass"


class _ConfigDouble(SimpleNamespace):
    """The real BenchConfig derives this once so `fm info` and `fm auth status` cannot disagree
    about a per-site override. A property, not a precomputed list: tests attach a site's auth after
    building the config, exactly as a config loaded then edited would."""

    @property
    def sites_with_own_auth(self) -> list[str]:
        return [name for name, entry in (self.sites or {}).items() if entry.auth is not None]


def _config(*, sites=None, **over):
    """Minimal duck-typed BenchConfig, same shape as the display_info pin
    (test_bench_info_transport_contract.py) trimmed to what build_bench_info_data reads."""
    recorded = {SITE: None} if sites is None else sites
    resolved = resolve_primary_site(BENCH, recorded)
    base = {
        "runtime": BenchRuntime.mount,
        "environment_type": FMBenchEnvType.prod,
        "restart_policy": SimpleNamespace(value="unless-stopped"),
        "admin_pass": ADMIN_PW,
        "deployments": None,
        "base_image": None,
        "apps_from": None,
        "admin_tools": False,
        "auth": None,
        "prune": None,
        "switch": None,
        "sites": {
            site: SimpleNamespace(database=database, alias_domains=[], auth=None, serve_admin_tools=None)
            for site, database in recorded.items()
        }
        or None,
        "root_path": "/nonexistent-bench-root",
        "name": BENCH,
        "site_names": list(recorded) or [BENCH],
        "primary_site": resolved,
        "get_database_config": lambda site=None: recorded.get(site or resolved),
        "get_database": lambda site=None: SimpleNamespace(type=SimpleNamespace(value="mariadb")),
    }
    base.update(over)
    config = _ConfigDouble(**base)
    config.serves_admin_tools = lambda site: BenchConfig.serves_admin_tools(config, site)
    return config


def _info(tmp_path, **over):
    kwargs = {
        "bench_name": BENCH,
        "bench_path": tmp_path,
        "bench_config": _config(),
        "services": MagicMock(),
        "workers": MagicMock(),
        "admin_tools": MagicMock(),
        "certificate_manager": MagicMock(),
        "get_db_connection_info_fn": MagicMock(return_value={"name": "db", "password": "dbpass"}),
        "has_certificate_fn": MagicMock(return_value=False),
        "is_running_fn": MagicMock(return_value=True),
        "get_services_running_status_fn": MagicMock(return_value={"nginx": "running"}),
        "unmanaged_site_dirs_fn": MagicMock(return_value=[]),
        "docker_client": None,
        "output_handler": MagicMock(wants_structured_data=True),
    }
    kwargs.update(over)
    info = BenchInfo(**kwargs)
    info.workers.compose_file_manager.get_container_names.return_value = {}
    info.workers.docker_client.compose.get_all_services_status.return_value = []
    info.admin_tools.compose_file_manager.exists.return_value = False
    info.get_bench_apps = MagicMock(return_value=[{"name": "frappe", "ref": "version-15", "commit": "abc123"}])
    return info


def test_service_state_is_the_raw_string_not_a_rendered_dot(tmp_path):
    """The card renders `status_dot(state)` (a glyph); the payload must carry the bare state."""
    data = _info(tmp_path).build_bench_info_data()
    assert data["services"] == {"bench": {"nginx": "running"}, "workers": {}, "tools": {}}


def test_admin_and_database_credentials_are_plain_fields_per_site(tmp_path):
    data = _info(tmp_path).build_bench_info_data()
    assert data["admin_credentials"] == [
        {"site": SITE, "user": "administrator", "password": f"{ADMIN_PW} (default)"}
    ]
    assert data["database_credentials"] == [{"site": SITE, "name": "db", "password": "dbpass"}]


def test_https_disabled_is_a_boolean_not_the_not_enabled_string(tmp_path):
    data = _info(tmp_path).build_bench_info_data()
    assert data["https"] == {"enabled": False, "type": None, "challenge_type": None, "expires_at": None}


def test_apps_carry_name_ref_and_commit_as_a_structured_row(tmp_path):
    data = _info(tmp_path).build_bench_info_data()
    assert data["apps"] == [{"name": "frappe", "ref": "version-15", "commit": "abc123"}]


def test_disk_sizes_are_ints_not_formatted_strings(tmp_path):
    data = _info(tmp_path).build_bench_info_data()
    assert isinstance(data["disk"]["stale_backup_bytes"], int)
    assert isinstance(data["disk"]["log_rotate_threshold_bytes"], int)
    assert isinstance(data["disk"]["actionable"], bool)


def test_no_value_is_a_rich_object_or_a_path(tmp_path):
    """Every leaf must already be JSON-native: this is the backstop the address-leak bug needed."""
    import json

    data = _info(tmp_path).build_bench_info_data()
    dumped = json.dumps(data)  # raises TypeError on anything json can't natively encode
    assert "object at 0x" not in dumped


def test_display_info_hands_the_structured_payload_to_a_handler_that_wants_it(tmp_path, monkeypatch):
    """The wiring under test: `wants_structured_data=True` must route to the data builder, not
    `card.render()`."""
    from frappe_manager.output_manager import railcard

    class _CardSpy:
        def __init__(self, *a, **k):
            pass

        def section(self, *a, **k):
            return self

        def fact(self, *a, **k):
            return self

        def render(self):
            return "<rendered card>"

    monkeypatch.setattr(railcard, "Card", _CardSpy)
    info = _info(tmp_path)

    info.display_info()

    printed = info.output.print_data.call_args.args[0]
    assert printed != "<rendered card>"
    assert printed["name"] == BENCH


def test_display_info_still_renders_the_card_for_a_handler_that_does_not_want_data(tmp_path, monkeypatch):
    """The default (Rich) path must be untouched: no regression for the human-facing card."""
    from frappe_manager.output_manager import railcard

    class _CardSpy:
        def __init__(self, *a, **k):
            pass

        def section(self, *a, **k):
            return self

        def fact(self, *a, **k):
            return self

        def render(self):
            return "<rendered card>"

    monkeypatch.setattr(railcard, "Card", _CardSpy)
    info = _info(tmp_path, output_handler=MagicMock(wants_structured_data=False))

    info.display_info()

    info.output.print_data.assert_called_once_with("<rendered card>")
