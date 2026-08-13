"""Characterization of `BenchDatabase` (`site_manager/modules/bench_database.py`).

This module had no test referencing it anywhere in the repo, and its `__init__` is a
near-twin of `BenchInfo.__init__`; merging the two is only safe once the observable
contract below is nailed down. Everything here passes against today's unmodified code.

Contracts pinned:

1. **Constructor wiring.** Every collaborator is stored verbatim, the callable arrives
   as `set_common_bench_config_fn` but is stored as `self.set_common_bench_config`, and
   `output_handler=None` means a fresh `RichOutputHandler`. Construction is inert: it
   touches neither the filesystem nor `services`. Unlike `BenchInfo`, there is **no**
   `docker_client` parameter, so this module can never reach docker directly.
2. **How connection details are resolved.** `get_connection_info` is a pure delegation
   to `get_bench_db_connection_info(bench_name, bench_path)`; the resolution rules that
   delegation inherits (common config supplies host/port, `sites/<bench>/site_config.json`
   supplies name/user/password and *overrides* host/port, `user` is the db *name*,
   `password` is always present and `None` by default) are pinned against a real
   `tmp_path` tree because `remove_database_and_user` branches on that dict's shape.
3. **The destructive guards.** `remove_database_and_user` refuses to do anything without
   a `name` key; it refuses to drop a database that `check_db_exists` denies, and a user
   that `check_user_exists` denies, warning instead. Database and user are decided
   independently (no early return), the database is dropped before the user, and the
   user drop is always `remove_all_host=True` (every host grant, not just `%`).
4. **What `sync_common_site_config` writes.** Redis only, config driven: an external
   `[redis]` is used verbatim, otherwise the per-bench redis container addresses are
   minted from the container prefix; `redis_socketio` always aliases the cache;
   `socketio_port` is the *string* `"80"`; and **no `db_*` key is ever minted**, since
   that would clobber an external database back to container names.

Docker and the filesystem are never reached: `services.database_manager`, the output
handler and the config setter are mocks, and every path lives under `tmp_path`.
"""

import json
from types import SimpleNamespace
from unittest.mock import MagicMock, call, patch

import pytest

from frappe_manager.output_manager.rich_output import RichOutputHandler
from frappe_manager.site_manager.bench_config import RedisConfig
from frappe_manager.site_manager.modules.bench_database import BenchDatabase

MODULE = "frappe_manager.site_manager.modules.bench_database"

BENCH_NAME = "site1.local"
# get_container_name_prefix("site1.local") -> "fm__site1_local"
MANAGED_CACHE = "redis://fm__site1_local__redis-cache:6379"
MANAGED_QUEUE = "redis://fm__site1_local__redis-queue:6379"


class _Harness:
    """A `BenchDatabase` wired to mocks that share one parent, so call order is visible."""

    def __init__(self, tmp_path, bench_name=BENCH_NAME, redis=None, output_handler=MagicMock):
        self.recorder = MagicMock()
        self.output = self.recorder.output if output_handler is MagicMock else output_handler
        self.db = self.recorder.db
        self.set_config = self.recorder.set_config
        self.bench_path = tmp_path / "benches" / bench_name
        self.bench_config = SimpleNamespace(redis=redis)
        self.services = SimpleNamespace(database_manager=self.db)
        self.database = BenchDatabase(
            bench_name=bench_name,
            bench_path=self.bench_path,
            bench_config=self.bench_config,
            services=self.services,
            set_common_bench_config_fn=self.set_config,
            output_handler=self.output,
        )

    @property
    def warnings(self):
        return [c.args[0] for c in self.output.warning.call_args_list]

    @property
    def printed(self):
        return [c.args[0] for c in self.output.print.call_args_list]

    def synced_config(self):
        assert self.set_config.call_count == 1
        return self.set_config.call_args.args[0]


def _write_json(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data))


def _calls(mock):
    """Recorded calls minus protocol noise (`bool(output_handler)` in the constructor)."""
    return [c for c in mock.mock_calls if not c[0].rsplit(".", 1)[-1].startswith("__")]


def _sites_dir(bench_path):
    return bench_path / "workspace" / "frappe-bench" / "sites"


def _write_site_config(bench_path, bench_name, data):
    _write_json(_sites_dir(bench_path) / bench_name / "site_config.json", data)


def _write_common_site_config(bench_path, data):
    _write_json(_sites_dir(bench_path) / "common_site_config.json", data)


@pytest.fixture
def conn_info():
    """Patch the connection-info seam so the destructive paths need no filesystem."""
    with patch(f"{MODULE}.get_bench_db_connection_info") as helper:
        helper.return_value = {"name": "site1_local_db", "user": "site1_local_user"}
        yield helper


# --------------------------------------------------------------------------------------
# 1. constructor wiring (the region that twins BenchInfo.__init__)
# --------------------------------------------------------------------------------------


def test_init_stores_every_collaborator_verbatim(tmp_path):
    h = _Harness(tmp_path)

    assert h.database.bench_name == BENCH_NAME
    assert h.database.bench_path is h.bench_path
    assert h.database.bench_config is h.bench_config
    assert h.database.services is h.services
    assert h.database.output is h.output


def test_init_renames_the_config_setter_callable(tmp_path):
    """The parameter is `set_common_bench_config_fn`; the attribute drops the `_fn`."""
    h = _Harness(tmp_path)

    assert h.database.set_common_bench_config is h.set_config
    assert not hasattr(h.database, "set_common_bench_config_fn")


def test_init_defaults_to_a_fresh_rich_output_handler(tmp_path):
    database = BenchDatabase(
        bench_name=BENCH_NAME,
        bench_path=tmp_path,
        bench_config=SimpleNamespace(redis=None),
        services=SimpleNamespace(database_manager=MagicMock()),
        set_common_bench_config_fn=MagicMock(),
    )

    assert isinstance(database.output, RichOutputHandler)


def test_init_takes_no_docker_client(tmp_path):
    """Unlike BenchInfo, this module is never handed a docker client."""
    with pytest.raises(TypeError):
        BenchDatabase(
            bench_name=BENCH_NAME,
            bench_path=tmp_path,
            bench_config=SimpleNamespace(redis=None),
            services=SimpleNamespace(database_manager=MagicMock()),
            set_common_bench_config_fn=MagicMock(),
            docker_client=MagicMock(),
        )


def test_init_is_inert(tmp_path):
    """Constructing must not touch the database service, the setter or the disk."""
    h = _Harness(tmp_path)

    assert _calls(h.recorder) == []
    assert not h.bench_path.exists()


# --------------------------------------------------------------------------------------
# 2. how connection details are resolved
# --------------------------------------------------------------------------------------


def test_get_connection_info_delegates_with_bench_name_and_path(tmp_path, conn_info):
    h = _Harness(tmp_path)

    result = h.database.get_connection_info()

    assert conn_info.call_args == call(BENCH_NAME, h.bench_path)
    assert result is conn_info.return_value


def test_connection_info_merges_common_and_site_config(tmp_path):
    h = _Harness(tmp_path)
    _write_common_site_config(h.bench_path, {"db_host": "global-db", "db_port": 3306})
    _write_site_config(
        h.bench_path,
        BENCH_NAME,
        {"db_name": "site1_local_db", "db_password": "s3cret"},
    )

    assert h.database.get_connection_info() == {
        "password": "s3cret",
        "host": "global-db",
        "port": 3306,
        "name": "site1_local_db",
        "user": "site1_local_db",
    }


def test_site_config_endpoint_overrides_the_common_one(tmp_path):
    h = _Harness(tmp_path)
    _write_common_site_config(h.bench_path, {"db_host": "global-db", "db_port": 3306})
    _write_site_config(
        h.bench_path,
        BENCH_NAME,
        {"db_name": "db", "db_password": "p", "db_host": "external.example", "db_port": 5432},
    )

    info = h.database.get_connection_info()

    assert (info["host"], info["port"]) == ("external.example", 5432)


def test_connection_info_without_any_config_file_is_password_only(tmp_path):
    h = _Harness(tmp_path)

    assert h.database.get_connection_info() == {"password": None}


def test_connection_info_from_common_config_alone_has_no_name(tmp_path):
    """No site config means no `name`, which is exactly what disarms the removal path."""
    h = _Harness(tmp_path)
    _write_common_site_config(h.bench_path, {"db_host": "global-db", "db_port": 3306})

    info = h.database.get_connection_info()

    assert "name" not in info
    assert "user" not in info
    assert info == {"password": None, "host": "global-db", "port": 3306}


def test_db_user_is_the_db_name(tmp_path):
    """`site_config.json` has no user key; the db name doubles as the user."""
    h = _Harness(tmp_path)
    _write_site_config(h.bench_path, BENCH_NAME, {"db_name": "the_db", "db_password": "p"})

    info = h.database.get_connection_info()

    assert info["user"] == "the_db" == info["name"]


# --------------------------------------------------------------------------------------
# 3. remove_database_and_user: guards, ordering, command shape
# --------------------------------------------------------------------------------------


@pytest.mark.usefixtures("conn_info")
def test_removal_announces_before_asking_the_database_anything(tmp_path):
    h = _Harness(tmp_path)

    h.database.remove_database_and_user()

    assert _calls(h.recorder)[0] == call.output.change_head("Removing bench db and db users from global-db")


def test_removal_without_a_name_refuses_all_database_work(tmp_path, conn_info):
    """The only guard that can stop the whole operation: no `name` in the info dict."""
    conn_info.return_value = {"password": None, "host": "global-db", "port": 3306}
    h = _Harness(tmp_path)

    h.database.remove_database_and_user()

    assert h.db.mock_calls == []
    assert h.warnings == []
    assert h.printed == []
    h.output.change_head.assert_called_once()


@pytest.mark.usefixtures("conn_info")
def test_missing_database_is_warned_about_and_never_dropped(tmp_path):
    h = _Harness(tmp_path)
    h.db.check_db_exists.return_value = False
    h.db.check_user_exists.return_value = False

    h.database.remove_database_and_user()

    h.db.check_db_exists.assert_called_once_with("site1_local_db")
    h.db.remove_db.assert_not_called()
    assert "global-db: Bench db [fm.info]site1_local_db[/fm.info] not found. Skipping.." in h.warnings


@pytest.mark.usefixtures("conn_info")
def test_existing_database_is_dropped_and_reported(tmp_path):
    h = _Harness(tmp_path)
    h.db.check_db_exists.return_value = True
    h.db.check_user_exists.return_value = False

    h.database.remove_database_and_user()

    h.db.remove_db.assert_called_once_with("site1_local_db")
    assert "global-db: Removed bench db [fm.info]site1_local_db[/fm.info]" in h.printed


@pytest.mark.usefixtures("conn_info")
def test_missing_user_is_warned_about_and_never_dropped(tmp_path):
    h = _Harness(tmp_path)
    h.db.check_db_exists.return_value = True
    h.db.check_user_exists.return_value = False

    h.database.remove_database_and_user()

    h.db.check_user_exists.assert_called_once_with("site1_local_user")
    h.db.remove_user.assert_not_called()
    assert "global-db: Bench db user [fm.info]site1_local_user[/fm.info] not found. Skipping.." in h.warnings


@pytest.mark.usefixtures("conn_info")
def test_existing_user_is_dropped_on_every_host(tmp_path):
    """`remove_all_host=True` is the destructive shape: all host grants, not just `%`."""
    h = _Harness(tmp_path)
    h.db.check_db_exists.return_value = True
    h.db.check_user_exists.return_value = True

    h.database.remove_database_and_user()

    assert h.db.remove_user.call_args == call("site1_local_user", remove_all_host=True)
    assert "global-db: Removed bench db users [fm.info]site1_local_user[/fm.info]" in h.printed


@pytest.mark.usefixtures("conn_info")
def test_user_removal_does_not_depend_on_the_database_existing(tmp_path):
    """A missing database must not short-circuit the orphaned user's removal."""
    h = _Harness(tmp_path)
    h.db.check_db_exists.return_value = False
    h.db.check_user_exists.return_value = True

    h.database.remove_database_and_user()

    h.db.remove_db.assert_not_called()
    h.db.remove_user.assert_called_once_with("site1_local_user", remove_all_host=True)


@pytest.mark.usefixtures("conn_info")
def test_database_is_dropped_before_the_user(tmp_path):
    h = _Harness(tmp_path)
    h.db.check_db_exists.return_value = True
    h.db.check_user_exists.return_value = True

    h.database.remove_database_and_user()

    assert h.db.mock_calls == [
        call.check_db_exists("site1_local_db"),
        call.remove_db("site1_local_db"),
        call.check_user_exists("site1_local_user"),
        call.remove_user("site1_local_user", remove_all_host=True),
    ]


def test_name_without_user_raises_before_anything_is_dropped(tmp_path, conn_info):
    """Pinned as-is: `user` is read unguarded, so a name-only dict explodes early."""
    conn_info.return_value = {"name": "site1_local_db"}
    h = _Harness(tmp_path)

    with pytest.raises(KeyError):
        h.database.remove_database_and_user()

    assert h.db.mock_calls == []


@pytest.mark.usefixtures("conn_info")
def test_removal_never_writes_the_common_site_config(tmp_path):
    h = _Harness(tmp_path)
    h.db.check_db_exists.return_value = True
    h.db.check_user_exists.return_value = True

    h.database.remove_database_and_user()

    h.set_config.assert_not_called()


# --------------------------------------------------------------------------------------
# 4. sync_common_site_config
# --------------------------------------------------------------------------------------


def test_sync_mints_the_per_bench_redis_containers_when_no_external_redis(tmp_path):
    h = _Harness(tmp_path, redis=None)

    h.database.sync_common_site_config()

    assert h.synced_config() == {
        "bench_id": "workspace-frappe-bench",
        "redis_cache": MANAGED_CACHE,
        "redis_queue": MANAGED_QUEUE,
        "redis_socketio": MANAGED_CACHE,
        "socketio_port": "80",
    }


def test_sync_uses_an_external_redis_verbatim(tmp_path):
    redis = RedisConfig(cache="redis://r.example:6379/0", queue="redis://r.example:6379/1")
    h = _Harness(tmp_path, redis=redis)

    h.database.sync_common_site_config()

    assert h.synced_config() == {
        "bench_id": "workspace-frappe-bench",
        "redis_cache": "redis://r.example:6379/0",
        "redis_queue": "redis://r.example:6379/1",
        "redis_socketio": "redis://r.example:6379/0",
        "socketio_port": "80",
    }


def test_sync_never_mints_a_database_key(tmp_path):
    """Minting db keys here would clobber an external db back to container names."""
    redis = RedisConfig(cache="redis://r.example:6379/0", queue="redis://r.example:6379/1")
    h = _Harness(tmp_path, redis=redis)

    h.database.sync_common_site_config()

    assert [key for key in h.synced_config() if key.startswith("db")] == []


def test_sync_socketio_port_is_the_string_80(tmp_path):
    h = _Harness(tmp_path)

    h.database.sync_common_site_config()

    assert h.synced_config()["socketio_port"] == "80"


def test_sync_derives_the_container_prefix_from_the_bench_name(tmp_path):
    h = _Harness(tmp_path, bench_name="a.b.c")

    h.database.sync_common_site_config()

    config = h.synced_config()
    assert config["redis_cache"] == "redis://fm__a_b_c__redis-cache:6379"
    assert config["redis_queue"] == "redis://fm__a_b_c__redis-queue:6379"


def test_sync_passes_prefix_and_config_urls_to_the_connection_config_helper(tmp_path):
    redis = RedisConfig(cache="redis://r.example:6379/0", queue="redis://r.example:6379/1")
    h = _Harness(tmp_path, redis=redis)

    with patch(f"{MODULE}.get_bench_connection_config") as helper:
        helper.return_value = {}
        h.database.sync_common_site_config()

    assert helper.call_args == call("fm__site1_local", "redis://r.example:6379/0", "redis://r.example:6379/1")


def test_sync_passes_none_for_both_urls_when_redis_is_unconfigured(tmp_path):
    h = _Harness(tmp_path, redis=None)

    with patch(f"{MODULE}.get_bench_connection_config") as helper:
        helper.return_value = {}
        h.database.sync_common_site_config()

    assert helper.call_args == call("fm__site1_local", None, None)


def test_sync_hands_the_helper_dict_straight_to_the_setter(tmp_path):
    h = _Harness(tmp_path)

    with patch(f"{MODULE}.get_bench_connection_config") as helper:
        helper.return_value = {"bench_id": "workspace-frappe-bench"}
        h.database.sync_common_site_config()

    assert h.synced_config() is helper.return_value


def test_sync_touches_neither_the_database_service_nor_the_disk(tmp_path):
    h = _Harness(tmp_path)

    h.database.sync_common_site_config()

    assert _calls(h.db) == []
    assert _calls(h.output) == []
    assert not h.bench_path.exists()
