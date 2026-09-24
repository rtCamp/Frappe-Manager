"""Bench nginx is ordered after the services it names as upstreams, by the 1.0.0 migration.

nginx resolves `frappe-site` and `socketio-site` at config parse and exits with
`[emerg] host not found in upstream` if either name is missing. A dev bench defaults to
`restart: no`, so the container that lost the startup race stays dead and takes `fm create` down
with it (rtCamp/Frappe-Manager#480). New benches get the ordering from docker-compose.tmpl;
compose files are generated once at create, so existing benches are healed here.

`service_started` is the correct condition and `service_healthy` is not: the network alias is
registered when the container starts, and nginx needs the NAME to resolve, not a ready app. No fm
service defines a healthcheck, so a health condition would block every start on something nothing
satisfies.
"""

from unittest.mock import MagicMock

import pytest
from ruamel.yaml import YAML

from frappe_manager.migration_manager.migrations.migrate_1_0_0 import MigrationV100

COMPOSE = """services:
  frappe:
    image: frappe:1
  nginx:
    image: nginx:1
  socketio:
    image: frappe:1
  redis-cache:
    image: redis:8-alpine
"""


@pytest.fixture
def step():
    migration = MigrationV100.__new__(MigrationV100)  # bypass __init__: no executor, no backups
    migration.output = MagicMock()
    migration.backup_manager = MagicMock()
    return migration


def _bench(tmp_path, text: str = COMPOSE):
    path = tmp_path / "docker-compose.yml"
    path.write_text(text)
    bench = MagicMock()
    bench.path = tmp_path
    bench.name = "shop.localhost"
    return bench, path


def _services(path):
    return YAML().load(path.read_text())["services"]


def test_nginx_is_ordered_after_both_upstreams(step, tmp_path):
    bench, path = _bench(tmp_path)

    step._add_nginx_depends_on(bench)

    assert _services(path)["nginx"]["depends_on"] == ["frappe", "socketio"]


def test_only_nginx_is_ordered(step, tmp_path):
    """Nothing else is fatal when a peer is missing, so nothing else gets an edge."""
    bench, path = _bench(tmp_path)

    step._add_nginx_depends_on(bench)

    services = _services(path)
    assert [name for name, body in services.items() if "depends_on" in body] == ["nginx"]


def test_the_heal_is_idempotent(step, tmp_path):
    """1.0.0 is unreleased, so a bench at 0.21.0.dev0 sorts below it and re-runs this migration."""
    bench, path = _bench(tmp_path)

    step._add_nginx_depends_on(bench)
    first = path.read_text()
    step._add_nginx_depends_on(bench)

    assert path.read_text() == first


def test_an_operator_s_own_depends_on_is_left_alone(step, tmp_path):
    """fm never overwrites a key the operator put there; a hand-written edge is their call."""
    bench, path = _bench(tmp_path, COMPOSE.replace("  nginx:\n", "  nginx:\n    depends_on: [frappe]\n"))

    step._add_nginx_depends_on(bench)

    assert _services(path)["nginx"]["depends_on"] == ["frappe"]


def test_a_bench_without_socketio_is_ordered_after_what_it_has(step, tmp_path):
    """An edge to a service the file does not define makes the whole compose invalid."""
    bench, path = _bench(tmp_path, COMPOSE.replace("  socketio:\n    image: frappe:1\n", ""))

    step._add_nginx_depends_on(bench)

    assert _services(path)["nginx"]["depends_on"] == ["frappe"]


def test_a_bench_with_no_compose_file_is_skipped(step, tmp_path):
    bench = MagicMock()
    bench.path = tmp_path
    bench.name = "shop.localhost"

    step._add_nginx_depends_on(bench)

    assert not (tmp_path / "docker-compose.yml").exists()


def test_the_rest_of_the_compose_file_survives(step, tmp_path):
    bench, path = _bench(tmp_path)

    step._add_nginx_depends_on(bench)

    services = _services(path)
    assert services["nginx"]["image"] == "nginx:1"
    assert set(services) == {"frappe", "nginx", "socketio", "redis-cache"}
