"""The destructive-path guards from the design's "## Guards" section.

Things fm must never do on a database it does not own, each asserted at the seam that would do
it. The refusals are policy, not incapacity: the site's own grant carries `DROP` at schema scope
and its password sits in `site_config.json` where fm reads it routinely, so nothing except these
guards stands between `fm delete` and someone's production schema.

The `--force`/`--no-setup-db` pairing and the root password's absence from the external `new-site`
argv are covered in `test_bench_site_force.py`; this file covers only what that one does not --
the forced (image-runtime) external invocation, and the commands that follow `new-site`.
"""

import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from frappe_manager.site_manager.bench_config import BenchConfig
from frappe_manager.site_manager.bench_service import BenchService
from frappe_manager.site_manager.modules.bench_site import BenchSiteManager
from frappe_manager.site_manager.site import Bench

GLOBAL_DB_SITE = "local.localhost"
EXTERNAL_SITE = "app.example.com"
# The bench holding that site, deliberately NOT the same string. `[database]` is keyed by SITE
# (`bench_config.py:1374`, `self.database.get(site or self.name)`) and the toml's `name` is the
# bench, so a fixture that uses one string for both cannot tell a site-keyed lookup from a
# bench-keyed one: the guard would still fire if the code read the wrong identity. Keeping them
# distinct is what makes these tests fail when the `[database]` lookup stops being site-keyed.
EXTERNAL_BENCH = "shop"
EXTERNAL_HOST = "mydb.abc.rds.amazonaws.com"
SCHEMA = "app_prod"
ROOT_PASSWORD = "global-db-root-secret"


def _config(tmp_path: Path, *, name: str, external_site: str | None = None, ca: str | None = None) -> BenchConfig:
    """A bench config recording its own site, plus `external_site` on an external database.

    Every site the config describes gets a `[sites."<site>"]` entry, because that is now the
    invariant: create and the migration both write one per bench, and it is how a bench-scoped
    command knows which site it means. `external_site` differs from `name` in the test that proves
    the guard resolves per site rather than per bench, and there the config legitimately describes
    two: the bench's own on global-db, and the external one.
    """
    toml = f'name = "{name}"\ndeveloper_mode = false\nadmin_tools = false\nenvironment = "prod"\n'
    toml += f'\n[sites."{name}"]\n'
    if external_site and external_site != name:
        toml += f'\n[sites."{external_site}"]\n'
    if external_site:
        toml += f'\n[sites."{external_site}".database]\nhost = "{EXTERNAL_HOST}"\nname = "{SCHEMA}"\n'
        if ca:
            toml += f'ca = "{ca}"\n'
    path = tmp_path / f"{name}.toml"
    path.write_text(toml)
    return BenchConfig.import_from_toml(path)


def _bench(config: BenchConfig, name: str) -> Bench:
    bench = Bench.__new__(Bench)  # bypass __init__: no Docker, no compose, no services
    bench.name = name
    bench.bench_config = config
    bench.logger = MagicMock()
    bench.output = MagicMock()
    # The real drop path: Bench.remove_database_and_user() delegates here. Asserting on this
    # rather than on the Bench method keeps the whole chain under test.
    bench.database = MagicMock()
    return bench


def _printed(output: MagicMock) -> str:
    return "\n".join(str(call.args[0]) for call in output.print.call_args_list if call.args)


# --------------------------------------------------------------------------- fm delete


@pytest.mark.parametrize("preference", [None, True, False])
def test_delete_never_drops_an_external_schema(tmp_path, preference):
    """Not even when the operator passed --delete-db-from-global-db: it is not fm's schema."""
    bench = _bench(_config(tmp_path, name=EXTERNAL_BENCH, external_site=EXTERNAL_SITE), EXTERNAL_SITE)

    bench._handle_database_deletion(preference)

    assert bench.database.remove_database_and_user.called is False
    assert bench.output.prompt_ask.called is False  # no prompt either: there is nothing to decide
    message = _printed(bench.output)
    assert EXTERNAL_HOST in message  # the operator has to be told where the data was left
    assert SCHEMA in message


def test_delete_prompts_and_drops_on_global_db(tmp_path):
    """Unchanged behaviour for a bench on the container fm owns."""
    bench = _bench(_config(tmp_path, name=GLOBAL_DB_SITE), GLOBAL_DB_SITE)
    bench.output.prompt_ask.return_value = "yes"

    bench._handle_database_deletion(None)

    assert bench.output.prompt_ask.call_count == 1
    assert bench.database.remove_database_and_user.call_count == 1


@pytest.mark.parametrize(("preference", "dropped"), [(True, 1), (False, 0)])
def test_delete_honours_an_explicit_preference_on_global_db(tmp_path, preference, dropped):
    bench = _bench(_config(tmp_path, name=GLOBAL_DB_SITE), GLOBAL_DB_SITE)

    bench._handle_database_deletion(preference)

    assert bench.output.prompt_ask.called is False
    assert bench.database.remove_database_and_user.call_count == dropped


def test_the_guard_resolves_per_site_not_per_bench(tmp_path):
    """One bench, two sites: the `global-db` one is dropped, the external one is refused.

    The switch is the presence of *that site's own* `[database]` entry. A bench-level test
    (`if config.database:`) would refuse both and quietly leak a `global-db` schema on every
    delete; the mirror bug drops the external one.
    """
    config = _config(tmp_path, name=GLOBAL_DB_SITE, external_site=EXTERNAL_SITE)

    internal = _bench(config, GLOBAL_DB_SITE)
    internal.output.prompt_ask.return_value = "yes"
    internal._handle_database_deletion(None)

    external = _bench(config, EXTERNAL_SITE)
    external._handle_database_deletion(None)

    assert internal.database.remove_database_and_user.call_count == 1
    assert external.database.remove_database_and_user.called is False
    assert EXTERNAL_HOST in _printed(external.output)


def _service(output: MagicMock) -> BenchService:
    service = BenchService.__new__(BenchService)  # bypass __init__: no docker client, no services
    service.output = output
    return service


def test_bench_service_delete_shares_the_guard(tmp_path):
    """`fm delete` on a broken bench goes through BenchService, which must refuse identically."""
    output = MagicMock()
    bench = _bench(_config(tmp_path, name=EXTERNAL_BENCH, external_site=EXTERNAL_SITE), EXTERNAL_SITE)

    _service(output)._handle_database_deletion(bench, None)

    assert bench.database.remove_database_and_user.called is False
    assert output.prompt_ask.called is False
    assert EXTERNAL_HOST in _printed(output)


def test_bench_service_delete_still_drops_a_global_db_schema(tmp_path):
    output = MagicMock()
    output.prompt_ask.return_value = "yes"
    bench = _bench(_config(tmp_path, name=GLOBAL_DB_SITE), GLOBAL_DB_SITE)

    _service(output)._handle_database_deletion(bench, None)

    assert output.prompt_ask.call_count == 1
    assert bench.database.remove_database_and_user.call_count == 1


# --------------------------------------------------------------------------- common_site_config


def test_common_site_config_carries_no_endpoint_key_at_all(tmp_path):
    """Not `db_host`, not `db_port`, and above all not `db_ssl_*`.

    `common_site_config.json` is bench-wide. A sibling site on another server reads these keys
    too, and a `db_ssl_ca` there was measured breaking a `global-db` sibling outright: it began
    failing with `TLS/SSL error: self-signed certificate` for as long as the key was present.
    """
    config = _config(tmp_path, name=EXTERNAL_BENCH, external_site=EXTERNAL_SITE, ca="/host/rds-bundle.pem")
    # The keys do exist -- in the per-site file, which is where they belong.
    site_config = config.get_site_config_data(EXTERNAL_SITE)
    assert site_config["db_host"] == EXTERNAL_HOST
    assert "db_ssl_ca" in site_config

    common = config.get_commmon_site_config_data()

    assert [key for key in common if key.startswith("db_")] == []
    serialized = json.dumps(common)
    assert EXTERNAL_HOST not in serialized
    assert "db-ca.pem" not in serialized


# --------------------------------------------------------------------------- new-site argv


def _site_manager(captured: list[tuple[str, dict]], config: BenchConfig) -> BenchSiteManager:
    manager = object.__new__(BenchSiteManager)  # bypass __init__ (no Docker/services setup)
    manager.bench_name = config.name
    manager.bench_cli_cmd = ["bench"]
    manager.bench_config = config
    manager.output = MagicMock()
    info = manager.services = MagicMock()
    info.database_manager.database_server_info.password = ROOT_PASSWORD
    info.database_manager.database_server_info.host = "global-db"
    info.database_manager.database_server_info.port = 3306

    def run(command, **kwargs):
        captured.append((command, kwargs))

    manager._container_run = run  # type: ignore[method-assign]
    return manager


def test_no_global_db_secret_or_endpoint_reaches_an_external_create(tmp_path):
    """Every command of the external create, not just `new-site`, plus the env they carry.

    `test_bench_site_force.py` asserts the root password is absent from the `new-site` argv; the
    `bench use` and `scheduler enable` calls that follow it are the part not covered there, and
    they take the same env.
    """
    captured: list[tuple[str, dict]] = []
    # `config.name` is the SITE here, not the bench: `create_bench_site` creates the site the
    # config names, and the external branch triggers on `[sites."<that site>".database]`. Splitting the
    # two the way the delete guards above do would describe a bench holding a `[database]` entry
    # for a site it is not creating, which is a misconfiguration rather than this scenario. It was
    # measured: with the names split, the argv came out as global-db, carrying the root password
    # and no `--no-setup-db`. That is the phase 3 failure mode, and it belongs in a test of its
    # own once a site has an identity separate from the bench.
    config = _config(tmp_path, name=EXTERNAL_SITE, external_site=EXTERNAL_SITE)

    _site_manager(captured, config).create_bench_site()

    assert captured, "the external create issued no commands at all"
    for command, kwargs in captured:
        assert ROOT_PASSWORD not in command
        assert "global-db" not in command  # the endpoint comes from site_config.json, not the argv
        assert ROOT_PASSWORD not in json.dumps(kwargs.get("env") or {})

    new_site_command = captured[0]
    assert "new-site" in new_site_command[0]
    # The endpoint flags belong to the global-db branch: on the external path Frappe reads host,
    # port and TLS out of the site file, which the create pipeline wrote before this ran.
    for flag in ("--db-host", "--db-port", "--db-name", "--db-root-username", "--db-root-password"):
        assert flag not in new_site_command[0]


def test_external_create_pairs_no_setup_db_with_force_even_when_forced(tmp_path):
    """The image runtime reaches `create_bench_site(force=True)`; the pairing must still hold.

    `--force` alone is inert, because `force` reaches only `setup_database` and `--no-setup-db`
    turns that off. The two together are what drops a schema, and this is the one call site that
    asks for `--force` explicitly rather than getting it from the external branch.
    """
    captured: list[tuple[str, dict]] = []
    config = _config(tmp_path, name=EXTERNAL_SITE, external_site=EXTERNAL_SITE)  # the site, per the note above

    _site_manager(captured, config).create_bench_site(force=True)

    new_site_command = captured[0][0]
    assert "--force" in new_site_command
    assert "--no-setup-db" in new_site_command
    assert new_site_command.count("--force") == 1  # the external branch must not force twice over
