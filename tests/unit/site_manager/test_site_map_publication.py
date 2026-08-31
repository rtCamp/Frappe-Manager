"""`Bench.republish_site_map` is the one step that makes a site reachable or unreachable.

It exists because `fm create BENCH/SITE` and `fm delete BENCH/SITE` need the identical step at
opposite ends of their sequences, and because getting either half of it wrong is invisible until
traffic arrives at the wrong site.

Two properties carry the weight. The map is derived from the RECORDED sites, so a caller must save
the config before publishing. And nginx is force-recreated rather than reloaded, because the map
reaches it as an environment variable read once at container start: a reload rereads config files and
would leave the container serving the previous map.
"""

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from frappe_manager.site_manager.site import Bench

BENCH = "shop"
FIRST = "shop.localhost"
SECOND = "b.example.com"


@pytest.fixture
def bench():
    """A bench stand-in whose config reports two recorded sites."""
    b = Bench.__new__(Bench)  # bypass __init__: no Docker, no compose, no services
    b.name = BENCH
    b.logger = MagicMock()
    b.output = MagicMock()
    b.bench_config = MagicMock()
    b.bench_config.environment_type = SimpleNamespace(value="prod")
    b.bench_config.export_to_compose_inputs.return_value = {
        "environment": {"nginx": {"VIRTUAL_HOST": f"{FIRST},{SECOND}"}}
    }
    b.generate_compose = MagicMock()
    # The map arrives as an environment variable, so publishing RECREATES the container. A restart
    # or a reload keeps the container and therefore keeps the old environment.
    b.docker_client = MagicMock()
    b.output.live_lines = MagicMock()
    return b


def test_the_map_is_rendered_from_the_recorded_sites(bench):
    """Not from anything passed in: the config is the record, and publishing reads it."""
    bench.republish_site_map()

    bench.bench_config.export_to_compose_inputs.assert_called_once_with()
    published = bench.generate_compose.call_args.args[0]
    assert published["environment"]["nginx"]["VIRTUAL_HOST"] == f"{FIRST},{SECOND}"


def test_the_environment_type_travels_with_the_map(bench):
    """`FRAPPE_ENV` is injected at render time rather than stored in the compose inputs, so a
    republish that dropped it would restart the bench's frappe container into the wrong mode."""
    bench.republish_site_map()

    assert bench.generate_compose.call_args.args[0]["environment"]["frappe"]["FRAPPE_ENV"] == "prod"


def test_an_absent_environment_block_is_created_rather_than_crashing(bench):
    """A bench whose compose inputs carry no environment at all still has to publish."""
    bench.bench_config.export_to_compose_inputs.return_value = {}

    bench.republish_site_map()

    assert bench.generate_compose.call_args.args[0]["environment"]["frappe"]["FRAPPE_ENV"] == "prod"


def test_other_services_environments_survive_the_injection(bench):
    """The injection targets `frappe` only; clobbering the sibling keys would unset VIRTUAL_HOST and
    take every site off the proxy."""
    bench.republish_site_map()

    published = bench.generate_compose.call_args.args[0]
    assert "VIRTUAL_HOST" in published["environment"]["nginx"]


def test_nginx_is_recreated_not_restarted(bench):
    """The map arrives as an environment variable, and a container keeps the environment it was
    created with. A restart re-ran the OLD `SITE_MAPPINGS`, so an added site answered 503 from its
    own bench's nginx while fm reported it published."""
    bench.republish_site_map()

    kwargs = bench.docker_client.compose.up.call_args.kwargs
    assert kwargs["services"] == ["nginx"]
    assert kwargs["force_recreate"] is True
    # And the real `Bench.restart_nginx_service` is never reached, which is the bug this replaces.
    bench.docker_client.compose.restart.assert_not_called()


def test_the_compose_is_written_before_nginx_is_recreated(bench):
    """The other order recreates the container against the old compose, which is the same bug as a
    reload and just as quiet."""
    order = []
    bench.generate_compose.side_effect = lambda _inputs: order.append("compose")
    bench.docker_client.compose.up.side_effect = lambda **_kw: order.append("nginx")

    bench.republish_site_map()

    assert order == ["compose", "nginx"]


def test_publishing_never_writes_the_config(bench):
    """It reads the record. Callers own saving it, which is what makes "save, then publish" a
    sequence they can get right or wrong."""
    bench.save_bench_config = MagicMock()

    bench.republish_site_map()

    bench.save_bench_config.assert_not_called()
