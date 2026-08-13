"""Contract tests for the external-database config model and its two payload builders.

`[database]` is keyed by site name and the presence of an entry for a site is the only
switch between "this site is external" and "this site lives on the `global-db`
container". `[redis]` is per bench, not per site. Nothing secret is persisted: the admin
credentials, the site password and the encryption key are create-time inputs only, so no
*later* fm run can provision on someone's shared server.

The two payload builders split along the same line: the database endpoint belongs in
`sites/<site>/site_config.json` and never appears in `common_site_config.json`, which is
all `bench worker` and `bench schedule` (no `--site`) ever read.
"""

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from frappe_manager.site_manager.bench_config import (
    BenchConfig,
    DatabaseConfig,
    FMBenchEnvType,
    RedisConfig,
)

_SITE = "x.localhost"
_OTHER = "other.localhost"

# Every field that is create-time only: excluded from the model dump AND from
# export_to_toml's exclude set, so neither route can leak it to disk.
_RUNTIME_ONLY = (
    "db_admin_user",
    "db_admin_password",
    "db_password",
    "db_password_generated",
    "attach_existing_site",
    "encryption_key",
)


def _bc(tmp_path: Path, **kwargs) -> BenchConfig:
    """A minimal bench named `x.localhost`; kwargs carry the external-db surface."""
    return BenchConfig(
        name=_SITE,
        developer_mode=False,
        admin_tools=False,
        environment_type=FMBenchEnvType.prod,
        root_path=tmp_path / "bench_config.toml",
        **kwargs,
    )


def _db(**kwargs) -> DatabaseConfig:
    return DatabaseConfig(host="db.example", name="app_prod", **kwargs)


# --------------------------------------------------------------- keyed by site


def test_no_database_table_means_the_global_db_container(tmp_path):
    assert _bc(tmp_path).get_database_config() is None


def test_get_database_config_defaults_to_the_benchs_own_name(tmp_path):
    entry = _db()
    bc = _bc(tmp_path, database={_SITE: entry})
    assert bc.get_database_config() is entry
    assert bc.get_database_config(_SITE) is entry


def test_an_entry_for_another_site_leaves_this_bench_internal(tmp_path):
    # One bench holding a `global-db` site plus an external one. The switch is the
    # presence of *that site's own* entry, which is what the delete guard leans on:
    # it must not refuse to drop `x.localhost` just because a sibling is external.
    other = DatabaseConfig(host="rds.example", name="other_prod")
    bc = _bc(tmp_path, database={_OTHER: other})
    assert bc.get_database_config() is None
    assert bc.get_database_config(_OTHER) is other


# ------------------------------------------------------------------- toml round-trip


def test_toml_roundtrip_preserves_the_database_entry_and_redis(tmp_path):
    path = tmp_path / "bench_config.toml"
    bc = _bc(
        tmp_path,
        database={_SITE: _db(user="app_svc", ca="/host/rds-bundle.pem", check_hostname=False)},
        redis=RedisConfig(cache="redis://r.example:6379/0", queue="redis://r.example:6379/1"),
    )
    assert bc.export_to_toml(path) is True

    # Site names carry dots, so the table key has to survive quoting.
    assert '[database."x.localhost"]' in path.read_text()

    back = BenchConfig.import_from_toml(path)
    entry = back.get_database_config()
    assert entry is not None
    assert (entry.host, entry.port, entry.name) == ("db.example", 3306, "app_prod")
    assert entry.user == "app_svc"
    assert entry.ca == "/host/rds-bundle.pem"
    assert entry.check_hostname is False
    assert back.redis is not None
    assert back.redis.cache == "redis://r.example:6379/0"
    assert back.redis.queue == "redis://r.example:6379/1"


def test_absent_user_is_not_written_and_login_user_falls_back_to_name(tmp_path):
    path = tmp_path / "bench_config.toml"
    bc = _bc(tmp_path, database={_SITE: _db()})
    assert bc.export_to_toml(path) is True
    assert "user =" not in path.read_text()

    entry = BenchConfig.import_from_toml(path).get_database_config()
    assert entry is not None
    assert entry.user is None
    assert entry.login_user == "app_prod"


def test_runtime_only_credentials_are_never_persisted(tmp_path):
    path = tmp_path / "bench_config.toml"
    bc = _bc(
        tmp_path,
        database={_SITE: _db()},
        db_admin_user="rds_master",
        db_admin_password="admin-secret-9f3",
        db_password="site-secret-4b1",
        db_password_generated=True,
        attach_existing_site=True,
        encryption_key="enc-key-77c",
    )
    assert bc.export_to_toml(path) is True
    text = path.read_text()

    for field in _RUNTIME_ONLY:
        assert field not in text
    for secret in ("rds_master", "admin-secret-9f3", "site-secret-4b1", "enc-key-77c"):
        assert secret not in text

    # And nothing reconstitutes them: a later fm run holds no admin credentials at all.
    back = BenchConfig.import_from_toml(path)
    assert back.db_admin_user is None
    assert back.db_admin_password is None
    assert back.db_password is None
    assert back.db_password_generated is False
    assert back.attach_existing_site is False
    assert back.encryption_key is None


# ------------------------------------------------------------------------ extra=forbid


def test_database_entry_rejects_an_unknown_key():
    with pytest.raises(ValidationError):
        DatabaseConfig(host="db.example", name="app_prod", require_tls=True)


def test_redis_rejects_an_unknown_key():
    with pytest.raises(ValidationError):
        RedisConfig(cache="redis://r.example:6379/0", queue="redis://r.example:6379/1", socketio="redis://r:6379/2")


# ------------------------------------------------------------------ site_config.json


def test_site_config_is_empty_for_a_site_on_global_db(tmp_path):
    # Frappe's own make_site_config writes that file during new-site; fm must not
    # pre-empt it, because a pre-written file also trips _new_site's "already exists".
    bc = _bc(tmp_path, database={_OTHER: _db()})
    assert bc.get_site_config_data() == {}


def test_site_config_carries_the_endpoint(tmp_path):
    bc = _bc(tmp_path, database={_SITE: _db(user="app_svc")})
    data = bc.get_site_config_data()
    assert data["db_type"] == "mariadb"
    assert data["db_name"] == "app_prod"
    assert data["db_user"] == "app_svc"
    assert data["db_host"] == "db.example"
    assert data["db_port"] == 3306


def test_site_config_db_user_falls_back_to_the_schema_name(tmp_path):
    bc = _bc(tmp_path, database={_SITE: _db()})
    assert bc.get_site_config_data()["db_user"] == "app_prod"


def test_site_config_has_no_tls_keys_without_a_ca(tmp_path):
    # The whole Frappe TLS block is gated on db_ssl_ca, and db_ssl_check_hostname
    # alone would be inert; emitting it would suggest a guarantee that is not there.
    data = _bc(tmp_path, database={_SITE: _db()}).get_site_config_data()
    assert "db_ssl_ca" not in data
    assert "db_ssl_check_hostname" not in data


def test_site_config_tls_points_at_the_container_path_not_the_host_path(tmp_path):
    bc = _bc(tmp_path, database={_SITE: _db(ca="/host/rds-bundle.pem")})
    data = bc.get_site_config_data()
    assert data["db_ssl_ca"] == "/workspace/frappe-bench/config/tls/x.localhost/db-ca.pem"
    assert data["db_ssl_check_hostname"] is True


def test_site_config_check_hostname_follows_the_entry(tmp_path):
    bc = _bc(tmp_path, database={_SITE: _db(ca="/host/rds-bundle.pem", check_hostname=False)})
    assert bc.get_site_config_data()["db_ssl_check_hostname"] is False


def test_rds_db_appears_only_on_the_provisioning_path(tmp_path):
    # rds_db is read in exactly one place, grant_all_privileges, reachable only from
    # setup_database. On adopt-empty and attach that call never happens, so the key
    # would imply behaviour it does not have.
    bc = _bc(tmp_path, database={_SITE: _db()})
    assert bc.get_site_config_data(provisioning=True)["rds_db"] == 1

    # adopt-empty: the schema was pre-made, fm provisions nothing
    assert "rds_db" not in bc.get_site_config_data()

    # attach: new-site is not used in any form
    attached = _bc(tmp_path, database={_SITE: _db()}, attach_existing_site=True)
    assert "rds_db" not in attached.get_site_config_data()


def test_encryption_key_only_when_supplied(tmp_path):
    bc = _bc(tmp_path, database={_SITE: _db()})
    assert "encryption_key" not in bc.get_site_config_data()

    keyed = _bc(tmp_path, database={_SITE: _db()}, encryption_key="enc-key-77c")
    assert keyed.get_site_config_data()["encryption_key"] == "enc-key-77c"


# ---------------------------------------------------------- common_site_config.json


def test_common_site_config_never_carries_a_database_endpoint(tmp_path):
    # True for every bench, not just external ones: the site file is the source of
    # truth and a hand-made site must not silently inherit someone's production host.
    external = _bc(tmp_path, database={_SITE: _db()})
    plain = _bc(tmp_path)
    for bc in (external, plain):
        common = bc.get_commmon_site_config_data()
        assert "db_host" not in common
        assert "db_port" not in common


def test_common_site_config_redis_defaults_to_the_bench_containers(tmp_path):
    common = _bc(tmp_path).get_commmon_site_config_data()
    assert common["redis_cache"] == "redis://fm__x_localhost__redis-cache:6379"
    assert common["redis_queue"] == "redis://fm__x_localhost__redis-queue:6379"
    assert common["redis_socketio"] == common["redis_cache"]


def test_common_site_config_uses_the_given_redis_urls_verbatim(tmp_path):
    bc = _bc(
        tmp_path,
        redis=RedisConfig(cache="redis://r.example:6379/0", queue="redis://r.example:6379/1"),
    )
    common = bc.get_commmon_site_config_data()
    assert common["redis_cache"] == "redis://r.example:6379/0"
    assert common["redis_queue"] == "redis://r.example:6379/1"
    # redis_socketio has no reader on v16 but bench tooling expects the key; it mirrors cache.
    assert common["redis_socketio"] == common["redis_cache"]


# ------------------------------------------------------------------ redis endpoints


@pytest.mark.parametrize(
    ("cache", "queue"),
    [
        ("redis://r.example:6379/0", "redis://r.example:6379/0"),
        # No index at all and an explicit /0 are the same logical database.
        ("redis://r.example:6379", "redis://r.example:6379/0"),
        ("redis://r.example:6379/0", "redis://r.example:6379"),
    ],
)
def test_redis_refuses_a_shared_logical_index(cache, queue):
    # A restore calls frappe.cache.delete_keys(""), a mass delete over the cache
    # connection, so a shared index would take the queue with it.
    with pytest.raises(ValidationError, match="same host, port and database index"):
        RedisConfig(cache=cache, queue=queue)


@pytest.mark.parametrize(
    ("cache", "queue"),
    [
        ("redis://r.example:6379/0", "redis://r.example:6379/1"),
        ("redis://a.example:6379/0", "redis://b.example:6379/0"),
        ("redis://r.example:6379/0", "redis://r.example:6380/0"),
    ],
)
def test_redis_accepts_separate_endpoints(cache, queue):
    cfg = RedisConfig(cache=cache, queue=queue)
    assert (cfg.cache, cfg.queue) == (cache, queue)


def test_create_bench_site_config_writes_a_file_that_does_not_exist_yet(tmp_path):
    """The external flow's FIRST write of sites/<site>/site_config.json.

    `save_dict_to_file` merges, so it reads before writing and raises FileNotFoundError on a
    path that is not there yet. Every other caller edits a file Frappe already wrote; this one
    runs before anything has connected, which is the whole point of writing TLS keys per site.
    Regression: this path failed live against a real external server.
    """
    from frappe_manager.site_manager.site import Bench

    bench = object.__new__(Bench)
    bench.path = tmp_path
    bench.name = "shop.localhost"

    Bench.create_bench_site_config(bench, {"db_name": "app_prod", "db_host": "mydb.example.com"})

    written = tmp_path / "workspace/frappe-bench/sites/shop.localhost/site_config.json"
    assert json.loads(written.read_text()) == {"db_name": "app_prod", "db_host": "mydb.example.com"}

    # second call merges rather than replacing, so an earlier key survives
    Bench.create_bench_site_config(bench, {"db_port": 3306})
    assert json.loads(written.read_text())["db_name"] == "app_prod"
    assert json.loads(written.read_text())["db_port"] == 3306
