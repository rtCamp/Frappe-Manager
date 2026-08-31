"""`create_bench_site` targets a named site, which is what a site-add needs.

It used to hardcode the bench name as the site everywhere: the `new-site` positional, `bench use`,
`--site ... scheduler enable`, the `[database]` lookup and the schema name. That was correct while a
bench was its own single site and cannot express adding a second one.

Two of these matter beyond plumbing.

`bench use` writes `default_site` into `common_site_config.json`, which is bench-WIDE. Running it for
an added site would silently move every bare `bench` command in that bench, and every request nginx
cannot match by Host, onto the new site. So a site-add must not set the default.

`bench_config.db_name` names the FIRST site's global-db schema. Reusing it for a second site would
point two sites at one schema, which is data loss rather than a failure.
"""

from unittest.mock import MagicMock

import pytest

from frappe_manager.site_manager.bench_config import DatabaseConfig
from frappe_manager.site_manager.modules.bench_site import BenchSiteManager

BENCH = "shop"
FIRST = "shop.localhost"
SECOND = "b.example.com"
ROOT_PASSWORD = "global-db-root-secret"


@pytest.fixture
def captured() -> list[str]:
    return []


@pytest.fixture
def manager(captured):
    """A site manager whose container calls are recorded instead of run."""
    m = object.__new__(BenchSiteManager)  # bypass __init__: no Docker, no services
    m.bench_name = BENCH
    m.bench_cli_cmd = ["bench"]
    m.bench_config = MagicMock(db_name="fm_shop_localhost_aaaa", admin_pass="admin", primary_site=FIRST)
    m.bench_config.get_database_config.return_value = None  # global-db, not external
    services = m.services = MagicMock()
    services.database_manager.database_server_info.password = ROOT_PASSWORD
    services.database_manager.database_server_info.host = "global-db"
    services.database_manager.database_server_info.port = 3306
    m.output = MagicMock()
    m._container_run = lambda cmd, **_kw: captured.append(cmd)
    m._site_env = lambda site=None: {}
    return m


def _new_site(commands: list[str]) -> str:
    return next(c for c in commands if "new-site" in c)


# --------------------------------------------------------------------------- which site


def test_the_named_site_is_the_one_created(manager, captured):
    manager.create_bench_site(site=SECOND, db_name="fm_b_example_com_bbbb", set_default=False)

    assert _new_site(captured).endswith(SECOND)


def test_the_bench_s_own_site_is_the_default_when_none_is_named(manager, captured):
    """Every call before the site-add passes nothing, and must keep creating the bench's own site."""
    manager.create_bench_site()

    assert _new_site(captured).endswith(FIRST)


def test_the_scheduler_is_enabled_for_the_named_site(manager, captured):
    """`--site` is explicit so it never depends on which site happens to be the bench default."""
    manager.create_bench_site(site=SECOND, db_name="fm_b_example_com_bbbb", set_default=False)

    assert any(f"--site {SECOND} scheduler enable" in c for c in captured)
    assert not any(f"--site {FIRST} scheduler enable" in c for c in captured)


# --------------------------------------------------------- the bench default is bench-wide


def test_an_added_site_does_not_become_the_bench_default(manager, captured):
    """`bench use` writes `default_site` into common_site_config, which every site in the bench
    shares. Setting it here would move every bare `bench` command onto the new site."""
    manager.create_bench_site(site=SECOND, db_name="fm_b_example_com_bbbb", set_default=False)

    assert not any(c.startswith("bench use") for c in captured)


def test_the_first_site_does_become_the_default(manager, captured):
    """A bench's own site is its default, which is what makes a bare `bench` command work at all."""
    manager.create_bench_site()

    assert any(f"use {FIRST}" in c for c in captured)


# ------------------------------------------------------------------- a schema of its own


def test_an_added_site_gets_the_schema_it_was_given(manager, captured):
    manager.create_bench_site(site=SECOND, db_name="fm_b_example_com_bbbb", set_default=False)

    assert "--db-name fm_b_example_com_bbbb" in _new_site(captured)


def test_an_added_site_never_reuses_the_first_sites_schema(manager, captured):
    """Two sites on one schema is data loss, not a failure: the second `new-site` would install into
    the first site's tables."""
    manager.create_bench_site(site=SECOND, db_name="fm_b_example_com_bbbb", set_default=False)

    assert "fm_shop_localhost_aaaa" not in _new_site(captured)


def test_an_added_site_with_no_schema_named_lets_frappe_choose(manager, captured):
    """Better than inheriting: Frappe mints one from the site name rather than fm handing over a
    schema that belongs to another site."""
    manager.create_bench_site(site=SECOND, set_default=False)

    assert "--db-name" not in _new_site(captured)


def test_the_first_site_still_uses_the_recorded_schema(manager, captured):
    """Unchanged for every existing bench: `bench_config.db_name` is that site's schema."""
    manager.create_bench_site()

    assert "--db-name fm_shop_localhost_aaaa" in _new_site(captured)


# ----------------------------------------------------------- the external-database lookup


def test_the_database_entry_is_looked_up_for_the_named_site(manager):
    """A bench can hold one site on global-db and another on an external server, so the lookup has
    to be per site rather than per bench."""
    manager.create_bench_site(site=SECOND, db_name="fm_b_example_com_bbbb", set_default=False)

    manager.bench_config.get_database_config.assert_called_with(SECOND)


def test_an_external_added_site_sends_no_global_db_root_password(manager, captured):
    """The global-db root password means nothing on a server fm does not own and must never be sent
    there. Same guard as the first site, asserted for the added one."""
    manager.bench_config.get_database_config.return_value = DatabaseConfig(
        host="rds.internal", name="app_prod", port=3306
    )

    manager.create_bench_site(site=SECOND, set_default=False)

    assert ROOT_PASSWORD not in _new_site(captured)
    assert "--no-setup-db" in _new_site(captured)
