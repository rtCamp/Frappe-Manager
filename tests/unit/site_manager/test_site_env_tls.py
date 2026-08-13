"""Which site's TLS material a bench command is handed.

`MYSQL_HOME` is the only channel that carries a CA to the `mariadb` client: Frappe's
`get_command` builds every `mariadb`/`mariadb-dump` invocation from user, host, port and password
alone and never reads `db_ssl_*`, so a shell-out finds its CA only through `<MYSQL_HOME>/my.cnf`.
fm writes that file per SITE, because two sites in one bench can sit on two different servers with
two different CAs.

So `BenchSiteManager._site_env` has to resolve the site it was ASKED about -- the argument when
there is one, the bench's own name otherwise -- and look the database config up under that name.
Handing over the wrong site's directory is silent: the file is simply absent or carries the wrong
CA, and the connection is refused with 3159 or fails verification at the point of use.
"""

from unittest.mock import MagicMock

import pytest

from frappe_manager.site_manager.bench_config import BenchConfig
from frappe_manager.site_manager.modules import db_tls
from frappe_manager.site_manager.modules.bench_site import BenchSiteManager

SITE = "app.example.com"
OTHER = "shop.example.com"
PLAIN = "dev.localhost"

_TOML = f"""
name = "{SITE}"
developer_mode = false
admin_tools = false
environment = "prod"

[[apps]]
name = "erpnext"
repo = "frappe/erpnext"

[database."{SITE}"]
host = "mydb.abc.rds.amazonaws.com"
name = "app_prod"
user = "app_svc"

[database."{OTHER}"]
host = "shopdb.xyz.rds.amazonaws.com"
name = "shop_prod"
user = "shop_svc"
"""


@pytest.fixture
def manager(tmp_path) -> BenchSiteManager:
    """A real `BenchConfig` (so the per-site lookup is the real one) on a manager built without
    `__init__`: nothing here needs Docker or the services stack."""
    path = tmp_path / "bench_config.toml"
    path.write_text(_TOML)

    m = object.__new__(BenchSiteManager)
    m.bench_name = SITE
    m.bench_config = BenchConfig.import_from_toml(path)
    m.bench_cli_cmd = ["bench"]
    m.output = MagicMock()
    return m


def test_env_defaults_to_the_benchs_own_site(manager):
    assert manager._site_env() == {"MYSQL_HOME": db_tls.site_mysql_home(SITE)}


def test_env_follows_the_site_it_was_asked_about(manager):
    env = manager._site_env(OTHER)

    assert env == {"MYSQL_HOME": db_tls.site_mysql_home(OTHER)}
    # The distinction is the whole point: the two sites have different CAs on disk.
    assert env["MYSQL_HOME"] != db_tls.site_mysql_home(SITE)


def test_a_site_on_the_global_db_container_gets_no_env(manager):
    # No `[database]` entry for it, so there is no CA and no option file to point at.
    assert manager._site_env(PLAIN) == {}


def test_the_env_reaching_a_command_is_the_one_for_the_site_it_runs_for(manager):
    """End of the wire: `create_site_dirs` is issued per site, so the exec it builds must carry
    that site's `MYSQL_HOME` and no other's."""
    captured: list[dict] = []
    manager._container_exec_argv = lambda argv, **kw: captured.append({"argv": argv, **kw})

    manager.create_site_dirs(OTHER)

    assert captured[0]["env"] == {"MYSQL_HOME": db_tls.site_mysql_home(OTHER)}
    assert OTHER in " ".join(captured[0]["argv"])  # …and it is that site being created

    captured.clear()
    manager.create_site_dirs()

    assert captured[0]["env"] == {"MYSQL_HOME": db_tls.site_mysql_home(SITE)}
    assert SITE in " ".join(captured[0]["argv"])
