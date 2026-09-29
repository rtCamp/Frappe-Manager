"""`--json` payloads for the four commands whose result used to be prose only.

`fm apps list`, `fm domain list`, `fm tools status` and `fm telemetry status` printed lines a
human reads and a script cannot. Each now builds a document instead. What is pinned here is the
shape a consumer indexes into, and the one distinction the prose could not carry: an
image-runtime bench does not KNOW its installed apps, which is a different fact from knowing it
has none.
"""

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from frappe_manager.commands.apps.list import list_apps
from frappe_manager.commands.domain.list import list_domains
from frappe_manager.commands.telemetry.status import status as telemetry_status
from frappe_manager.commands.tools.status import status as tools_status
from frappe_manager.output_manager import set_global_output_handler
from frappe_manager.output_manager.base import OutputHandler

pytestmark = pytest.mark.timeout(15)

BENCH = "mybench.localhost"


@pytest.fixture
def world():
    output = MagicMock(spec=OutputHandler)
    set_global_output_handler(output)

    bench = MagicMock(name="Bench")
    bench.name = BENCH
    cfg = bench.bench_config
    cfg.apps_list = []
    cfg.site_names = ["site-a"]
    cfg.sites = {}
    cfg.primary_site_or_none.return_value = "site-a"
    cfg.serves_admin_tools.return_value = True
    cfg.admin_tools = True

    bench_cls = MagicMock()
    bench_cls.get_object.return_value = bench
    ctx = SimpleNamespace(obj={"services": MagicMock(), "verbose": False})
    return SimpleNamespace(output=output, bench=bench, bench_cls=bench_cls, ctx=ctx)


def _payload(world):
    return world.output.print_data.call_args.args[0]


def _run(module: str, fn, world, **kwargs):
    with (
        patch(f"{module}.Bench", world.bench_cls),
        patch(f"{module}.check_bench_migration_required", MagicMock()),
    ):
        fn(world.ctx, benchname=BENCH, json_result=True, **kwargs)


class TestAppsList:
    def test_no_workspace_reports_installed_as_null_not_empty(self, world):
        """An image bench cannot read apps.txt: "unknown" must not serialize as "none"."""
        with patch("frappe_manager.commands.apps.list.host_bench_dir") as host_dir:
            host_dir.return_value.__truediv__.return_value.__truediv__.return_value.exists.return_value = False
            _run("frappe_manager.commands.apps.list", list_apps, world)

        assert _payload(world)["installed"] is None

    def test_recorded_apps_carry_their_pinned_ref(self, world):
        """The ref is what distinguishes a pin from a default; prose collapsed None to "default"."""
        world.bench.bench_config.apps_list = [SimpleNamespace(name="erpnext", repo="frappe/erpnext", ref=None)]
        with patch("frappe_manager.commands.apps.list.host_bench_dir") as host_dir:
            host_dir.return_value.__truediv__.return_value.__truediv__.return_value.exists.return_value = False
            _run("frappe_manager.commands.apps.list", list_apps, world)

        assert _payload(world)["recorded"] == [{"name": "erpnext", "repo": "frappe/erpnext", "ref": None}]


class TestDomainList:
    def test_a_site_reports_its_role_as_a_boolean_and_its_aliases_as_a_list(self, world):
        """Prose put an alias on a line shaped like the primary's; the reader could not tell them apart."""
        world.bench.bench_config.sites = {"site-a": SimpleNamespace(alias_domains=["www.example.com"])}
        _run("frappe_manager.commands.domain.list", list_domains, world)

        assert _payload(world) == [{"site": "site-a", "primary": True, "aliases": ["www.example.com"]}]


class TestToolsStatus:
    def test_routing_is_reported_per_site(self, world):
        """Admin-tools routing is per site, so one flag for the bench would be the wrong answer."""
        world.bench.bench_config.site_names = ["site-a", "site-b"]
        world.bench.bench_config.serves_admin_tools.side_effect = lambda name: name == "site-a"
        world.bench.admin_tools.compose_file_manager.compose_path.exists.return_value = True
        _run("frappe_manager.commands.tools.status", tools_status, world)

        assert _payload(world)["sites"] == {"site-a": True, "site-b": False}


class TestTelemetryStatus:
    def test_reporting_requires_both_the_flag_and_a_key(self, world):
        """Either alone sends nothing: the web process falls back to plain Gunicorn."""
        with patch("frappe_manager.commands.telemetry.status.describe_newrelic", return_value=(True, False)):
            _run("frappe_manager.commands.telemetry.status", telemetry_status, world)

        newrelic = _payload(world)["providers"]["newrelic"]
        assert newrelic["reporting"] is False
        assert newrelic["enabled"] is True
        assert newrelic["license_key_stored"] is False
