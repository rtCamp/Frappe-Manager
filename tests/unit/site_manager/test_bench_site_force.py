"""Regression guard for image-runtime site creation.

Image runtime pre-binds `sites/<site>`, so `compose up` creates that dir before
`bench new-site` runs. `create_bench_site(force=True)` must add `--force` so
new-site populates the existing (empty) dir instead of aborting with
"Site ... already exists". Mount runtime must NOT force.
"""

from unittest.mock import MagicMock

from frappe_manager.site_manager.modules.bench_site import BenchSiteManager


def _manager(captured):
    m = object.__new__(BenchSiteManager)  # bypass __init__ (no Docker/services setup)
    m.bench_name = "fm.alok.rt.gw"
    m.bench_cli_cmd = ["bench"]
    m.bench_config = MagicMock(db_name="db1", admin_pass="admin")  # noqa: S106
    info = m.services = MagicMock()
    info.database_manager.database_server_info.password = "p"  # noqa: S105
    info.database_manager.database_server_info.host = "global-db"
    info.database_manager.database_server_info.port = 3306
    m.output = MagicMock()

    def _run(cmd, **_kw):
        captured.append(cmd)

    m._container_run = _run  # type: ignore[method-assign]  # noqa: SLF001
    return m


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
