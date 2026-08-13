"""Pure-core tests for the compose shape model (strategy/factory/renderer).

The specs are plain data: decisions are asserted without Docker. The renderer
is exercised against a real ComposeFile to prove idempotency and passthrough.

`database` and `redis` are the two external-service switches: absent (None) is
every bench that never asked for the feature, and the projection must be a no-op
for those, since it runs on every create, update and re-pin.
"""

from types import SimpleNamespace

import pytest

from frappe_manager.docker.compose_file import ComposeFile
from frappe_manager.site_manager.bench_config import BenchRuntime, DatabaseConfig, RedisConfig
from frappe_manager.site_manager.modules import db_tls
from frappe_manager.site_manager.modules.compose_shape import (
    BENCH_REDIS_SERVICES,
    RenderContext,
    apply_specs,
    bench_service_specs,
    bind_strings,
    default_code_image,
    default_nginx_image,
    runtime_shape,
    validate_redis_endpoints,
    worker_service_specs,
)

EXTERNAL_REDIS = RedisConfig(cache="redis://r.example:6379/0", queue="redis://r.example:6379/1")


def _cfg(runtime, name="s.localhost", tag=None, base_image=None, database=None, redis=None):
    return SimpleNamespace(
        runtime=runtime,
        name=name,
        base_image=base_image,
        deploy_state=SimpleNamespace(current_tag=tag) if tag else None,
        database=database,
        redis=redis,
    )


def _external_db(name="s.localhost"):
    return {name: DatabaseConfig(host="mydb.abc.rds.amazonaws.com", name="app_prod")}


# ------------------------------------------------------------------ strategy


def test_mount_shape_defaults():
    shape = runtime_shape(_cfg(BenchRuntime.mount))
    # Explicit stock pins (not template defaults) so a runtime flip re-points
    # services off the app image.
    assert shape.image("frappe") == default_code_image()
    assert shape.image("nginx") == default_nginx_image()
    assert [(b.host, b.container) for b in shape.binds()] == [("./workspace", "/workspace")]


def test_mount_shape_base_image_override():
    shape = runtime_shape(_cfg(BenchRuntime.mount, base_image="custom/frappe:x1"))
    assert shape.image("frappe") == "custom/frappe:x1"
    assert shape.image("nginx") == default_nginx_image()  # nginx keeps the stock image in mount


def test_image_shape_tags_and_binds():
    shape = runtime_shape(_cfg(BenchRuntime.image, tag="ghcr.io/acme/erp:jun01"))
    assert shape.image("frappe") == "ghcr.io/acme/erp:jun01"
    assert shape.image("nginx") == "ghcr.io/acme/erp-nginx:jun01"  # paired assets image
    targets = [b.container for b in shape.binds()]
    assert "/workspace" not in targets
    assert "/workspace/frappe-bench/sites/s.localhost" in targets


def test_image_runtime_without_tag_yields_no_shape():
    assert runtime_shape(_cfg(BenchRuntime.image)) is None
    assert bench_service_specs(_cfg(BenchRuntime.image)) == ()


def test_deploy_tag_context_overrides_recorded_tag():
    cfg = _cfg(BenchRuntime.image, tag="repo:old")
    shape = runtime_shape(cfg, RenderContext(deploy_tag="repo:new"))
    assert shape.image("frappe") == "repo:new"


# ------------------------------------------------------------------- factory


def test_bench_specs_cover_code_services_with_rolling_roles():
    specs = {s.name: s for s in bench_service_specs(_cfg(BenchRuntime.image, tag="r:t"))}
    # The redis services ride in the same batch so every writer of the bench compose
    # agrees on whether fm starts them; only the code services carry a runtime shape.
    assert set(specs) == {"frappe", "nginx", "socketio", "schedule", *BENCH_REDIS_SERVICES}
    assert all(specs[name].image is None and specs[name].managed_binds == () for name in BENCH_REDIS_SERVICES)
    assert specs["frappe"].rolling
    assert specs["nginx"].rolling
    assert not specs["socketio"].rolling
    assert not specs["schedule"].rolling
    assert all(s.enabled for s in specs.values())


def test_worker_specs_and_bind_strings():
    (spec,) = worker_service_specs(_cfg(BenchRuntime.mount), ["long-worker"])
    assert spec.image == default_code_image()
    assert bind_strings(spec) == ["./workspace:/workspace"]
    (ispec,) = worker_service_specs(_cfg(BenchRuntime.image, tag="r:t"), ["long-worker"])
    assert ispec.image == "r:t"
    assert "./workspace/frappe-bench/logs:/workspace/frappe-bench/logs" in bind_strings(ispec)


# ------------------------------------------------------------------ renderer

COMPOSE = """\
services:
  frappe:
    image: base:1
    volumes:
      - fm-sockets:/fm-sockets
      - ./workspace:/workspace
  nginx:
    image: nginx:1
    volumes:
      - ./configs/nginx/conf:/etc/nginx
      - ./workspace:/workspace
  redis-cache:
    image: redis:alpine
  redis-queue:
    image: redis:alpine
volumes:
  fm-sockets:
"""


def _cfm(tmp_path):
    p = tmp_path / "docker-compose.yml"
    p.write_text(COMPOSE)
    return ComposeFile(p)


def test_apply_specs_image_mode_projects_and_preserves(tmp_path):
    cfm = _cfm(tmp_path)
    cfg = _cfg(BenchRuntime.image, tag="ghcr.io/acme/erp:jun01")
    apply_specs(cfm, bench_service_specs(cfg), cfg.name)

    fr = [str(v) for v in cfm.get_service_volumes("frappe")]
    assert "fm-sockets:/fm-sockets" in fr  # named volume preserved
    assert not any(v.endswith(":/workspace") for v in fr)  # wholesale bind gone
    assert "./workspace/frappe-bench/logs:/workspace/frappe-bench/logs" in fr
    ng = [str(v) for v in cfm.get_service_volumes("nginx")]
    assert "./configs/nginx/conf:/etc/nginx" in ng  # nginx conf binds preserved
    assert cfm.yml["services"]["frappe"]["image"] == "ghcr.io/acme/erp:jun01"
    assert cfm.yml["services"]["nginx"]["image"] == "ghcr.io/acme/erp-nginx:jun01"


def test_apply_specs_idempotent_no_duplicates(tmp_path):
    cfm = _cfm(tmp_path)
    cfg = _cfg(BenchRuntime.image, tag="r:t1")
    apply_specs(cfm, bench_service_specs(cfg), cfg.name)
    apply_specs(cfm, bench_service_specs(cfg), cfg.name)  # re-render, same tag
    apply_specs(cfm, bench_service_specs(cfg, RenderContext(deploy_tag="r:t2")), cfg.name)  # re-pin
    raw = list(cfm.yml["services"]["frappe"]["volumes"])
    assert len(raw) == len(set(raw))  # never duplicates
    assert cfm.yml["services"]["frappe"]["image"] == "r:t2"


def test_apply_specs_mount_mode_round_trip(tmp_path):
    cfm = _cfm(tmp_path)
    cfg = _cfg(BenchRuntime.mount)
    apply_specs(cfm, bench_service_specs(cfg), cfg.name)
    fr = [str(v) for v in cfm.get_service_volumes("frappe")]
    assert "./workspace:/workspace" in fr  # mount keeps live workspace
    assert cfm.yml["services"]["frappe"]["image"] == default_code_image()  # stock pin applied


def test_apply_specs_switch_image_to_mount_restores_workspace(tmp_path):
    cfm = _cfm(tmp_path)
    apply_specs(cfm, bench_service_specs(_cfg(BenchRuntime.image, tag="r:t")), "s.localhost")
    apply_specs(cfm, bench_service_specs(_cfg(BenchRuntime.mount)), "s.localhost")
    fr = [str(v) for v in cfm.get_service_volumes("frappe")]
    assert "./workspace:/workspace" in fr
    assert not any("/workspace/frappe-bench/logs" in v for v in fr)  # data binds stripped
    ng = [str(v) for v in cfm.get_service_volumes("nginx")]
    # nginx serves assets from the workspace in mount mode; conf binds pass through.
    assert "./workspace:/workspace" in ng
    assert "./configs/nginx/conf:/etc/nginx" in ng
    assert cfm.yml["services"]["nginx"]["image"] == default_nginx_image()


def test_external_redis_disables_both_redis_services_and_dropping_it_re_enables(tmp_path):
    cfm = _cfm(tmp_path)
    cfg = _cfg(BenchRuntime.mount, redis=EXTERNAL_REDIS)
    apply_specs(cfm, bench_service_specs(cfg), cfg.name)

    for name in BENCH_REDIS_SERVICES:
        assert cfm.yml["services"][name]["profiles"] == ["disabled"]  # fm must not start them

    apply_specs(cfm, bench_service_specs(_cfg(BenchRuntime.mount)), cfg.name)  # [redis] removed
    for name in BENCH_REDIS_SERVICES:
        assert "profiles" not in cfm.yml["services"][name]


def test_internal_redis_render_is_byte_identical(tmp_path):
    # The redis specs ride along on every bench compose write, so on a bench with no
    # [redis] they must change nothing at all -- not even a profiles key.
    cfg = _cfg(BenchRuntime.mount)
    specs = bench_service_specs(cfg)
    assert {s.name for s in specs} >= set(BENCH_REDIS_SERVICES)  # they really are in the batch

    control_dir = tmp_path / "control"
    control_dir.mkdir()
    control = _cfm(control_dir)
    apply_specs(control, tuple(s for s in specs if s.name not in BENCH_REDIS_SERVICES), cfg.name)
    control.write_to_file()

    rendered = _cfm(tmp_path)
    apply_specs(rendered, specs, cfg.name)
    rendered.write_to_file()

    assert rendered.compose_path.read_text() == control.compose_path.read_text()


# --------------------------------------------------------------------- external database


def test_mysql_home_lands_only_on_the_db_cli_services():
    envs = {s.name: dict(s.env) for s in bench_service_specs(_cfg(BenchRuntime.mount, database=_external_db()))}
    bundle = db_tls.bench_mysql_home()
    # frappe and schedule shell out to the mariadb client (initial import, dump-based
    # backups), and get_command never reads db_ssl_*, so only MYSQL_HOME carries TLS.
    assert envs["frappe"] == {"MYSQL_HOME": bundle}
    assert envs["schedule"] == {"MYSQL_HOME": bundle}
    assert envs["nginx"] == {}
    assert envs["socketio"] == {}


def test_no_mysql_home_without_an_external_database():
    specs = bench_service_specs(_cfg(BenchRuntime.mount))
    assert all(s.env == () for s in specs)
    (worker,) = worker_service_specs(_cfg(BenchRuntime.mount), ["long-worker"])
    assert worker.env == ()


# ------------------------------------------------------------------------- redis urls


def test_validate_redis_endpoints_rejects_a_shared_logical_index():
    with pytest.raises(ValueError, match="same host, port and database index"):
        validate_redis_endpoints("redis://h:6379/0", "redis://h:6379/0")
    # No path and /0 are the same index, so the pair is the same logical database.
    with pytest.raises(ValueError, match="same host, port and database index"):
        validate_redis_endpoints("redis://h:6379", "redis://h:6379/0")


def test_validate_redis_endpoints_accepts_differing_endpoints():
    assert validate_redis_endpoints("redis://h:6379/0", "redis://h:6379/1") is None
    assert validate_redis_endpoints("redis://h:6379/0", "redis://other:6379/0") is None
    assert validate_redis_endpoints("redis://h:6379/0", "redis://h:6380/0") is None
