"""A bench-less `fm bake` must seed a PRODUCTION config, never a dev-shaped one.

`commands.bake._build_standalone_config` builds the transient ``BenchConfig`` that a
bake without a bench provisions from. Its seeded values are the only thing deciding
the shape of the published image: ``admin_tools`` decides whether the mailpit/adminer
surfaces belong to the bench (see ``Bench.bench_config.admin_tools`` consumers), and
``developer_mode``/``environment`` decide whether the runtime is built for production.
Nothing downstream re-hardens them -- ``fm create`` goes the other way and deliberately
turns both on for a dev bench -- so the seeds are the contract.

Defended here: the seeds are prod-shaped, AND a ``--config`` overlay (the documented
escape hatch) can still flip them, which is what proves the values in the assembled
document are the ones being read rather than a parser default.
"""

import pytest

from frappe_manager.commands.bake import _build_standalone_config
from frappe_manager.site_manager.bench_config import AppConfig, FMBenchEnvType


def _apps(*specs: str) -> list[AppConfig]:
    return [AppConfig.from_string(s) for s in specs]


@pytest.fixture
def flag_only_config():
    return _build_standalone_config(_apps("erpnext:version-15"), "ghcr.io/acme/x", None, None, None, [])


def test_standalone_bake_does_not_enable_admin_tools(flag_only_config):
    # Admin tools are a dev convenience (mailpit/adminer); an image baked for a
    # remote deploy must not carry them just because it had no bench to inherit from.
    assert flag_only_config.admin_tools is False


def test_standalone_bake_is_production_and_not_developer_mode(flag_only_config):
    assert flag_only_config.developer_mode is False
    assert flag_only_config.environment_type == FMBenchEnvType.prod


def test_config_overlay_can_still_turn_the_dev_surfaces_back_on():
    # Same keys, opposite values: proves the seeded document (not a fixed default in
    # BenchConfig.import_from_toml) is what the assertions above are observing.
    overlay = 'admin_tools = true\ndeveloper_mode = true\nenvironment = "dev"\n'
    bc = _build_standalone_config(_apps("erpnext:version-15"), "ghcr.io/acme/x", None, None, None, [overlay])

    assert bc.admin_tools is True
    assert bc.developer_mode is True
    assert bc.environment_type == FMBenchEnvType.dev
