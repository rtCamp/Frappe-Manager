"""Regression guard for image-runtime site creation, and for the external-database argv.

Image runtime pre-binds `sites/<site>`, so `compose up` creates that dir before
`bench new-site` runs. `create_bench_site(force=True)` must add `--force` so
new-site populates the existing (empty) dir instead of aborting with
"Site ... already exists". Mount runtime must NOT force.

A bench with a `[database]` entry takes the other branch entirely: it always forces
(the create pipeline wrote `site_config.json`, so the site dir exists) and always pairs
that with `--no-setup-db`, which is what makes `--force` inert. It never sends the
global-db root password to a host fm does not own.
"""

from unittest.mock import MagicMock

import pytest

from frappe_manager.site_manager.bench_config import DatabaseConfig
from frappe_manager.site_manager.exceptions import BenchOperationException
from frappe_manager.site_manager.modules.bench_site import BenchSiteManager

EXTERNAL_DB = DatabaseConfig(host="mydb.abc.rds.amazonaws.com", name="app_prod")
ROOT_PASSWORD = "global-db-root-secret"


def _manager(captured, database_config: DatabaseConfig | None = None):
    m = object.__new__(BenchSiteManager)  # bypass __init__ (no Docker/services setup)
    m.bench_name = "fm.alok.rt.gw"
    m.bench_cli_cmd = ["bench"]
    # `primary_site` is a real string, not a Mock: `create_bench_site` defaults the site it creates
    # to it and joins it into the argv, so a Mock here fails the join rather than the assertion.
    m.bench_config = MagicMock(db_name="db1", admin_pass="admin", primary_site="fm.alok.rt.gw")
    # No `[database]` entry by default: the global-db container, which is the bench the
    # forcing tests below are about. Left as a bare MagicMock this returns a truthy Mock
    # and every create silently takes the external branch instead.
    m.bench_config.get_database_config.return_value = database_config
    info = m.services = MagicMock()
    info.database_manager.database_server_info.password = ROOT_PASSWORD
    info.database_manager.database_server_info.host = "global-db"
    info.database_manager.database_server_info.port = 3306
    m.output = MagicMock()

    def _run(cmd, **_kw):
        captured.append(cmd)

    m._container_run = _run  # type: ignore[method-assign]
    return m


class _DropsNoSetupDb(list):
    """A `bench_cli_cmd` that loses `--no-setup-db` while the argv is assembled.

    Stands in for a future edit to the external branch that stops emitting the flag: that
    is the only way `--force` could travel with schema setup against a server fm does not
    own. The guard is asserted on the built argv precisely so such an edit fails loudly
    instead of dropping someone's schema.
    """

    def __add__(self, other):
        return _DropsNoSetupDb([*self, *(arg for arg in other if arg != "--no-setup-db")])

    __iadd__ = __add__


def test_force_adds_flag_to_new_site():
    captured: list[str] = []
    _manager(captured).create_bench_site(force=True)
    new_site_cmd = captured[0]
    assert "new-site" in new_site_cmd
    assert "--force" in new_site_cmd


def test_default_does_not_force():
    captured: list[str] = []
    _manager(captured).create_bench_site()
    assert "new-site" in captured[0]
    assert "--force" not in captured[0]


def test_external_create_forces_with_no_setup_db_and_no_root_password():
    captured: list[str] = []
    _manager(captured, EXTERNAL_DB).create_bench_site()
    new_site_cmd = captured[0]
    assert "new-site" in new_site_cmd
    assert "--no-setup-db" in new_site_cmd
    assert "--force" in new_site_cmd  # site dir already written; inert without schema setup
    assert "--db-root-password" not in new_site_cmd
    assert ROOT_PASSWORD not in new_site_cmd


def test_external_force_without_no_setup_db_is_refused_before_running_anything():
    captured: list[str] = []
    manager = _manager(captured, EXTERNAL_DB)
    manager.bench_cli_cmd = _DropsNoSetupDb(["bench"])

    with pytest.raises(BenchOperationException, match="drops the schema"):
        manager.create_bench_site()

    assert captured == []  # refused while still an argv, nothing reached the container
