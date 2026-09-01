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
from frappe_manager.site_manager.bench_config import BenchRuntime, DatabaseConfig, RedisConfig, SiteConfig
from frappe_manager.site_manager.modules import db_tls
from frappe_manager.site_manager.modules.compose_shape import (
    BENCH_REDIS_SERVICES,
    RenderContext,
    apply_specs,
    bench_service_specs,
    bind_strings,
    data_binds,
    default_code_image,
    default_nginx_image,
    runtime_shape,
    validate_redis_endpoints,
    worker_service_specs,
)

EXTERNAL_REDIS = RedisConfig(cache="redis://r.example:6379/0", queue="redis://r.example:6379/1")


def _cfg(runtime, name="s.localhost", tag=None, base_image=None, database=None, redis=None, sites=None):
    """`database={site: cfg}` is translated into the `sites` shape the production code reads.

    Kept as a kwarg because what these tests assert is "the bench has an external database", not
    where the model files it.

    `sites` names the bench's sites for the image-mode binds, which are one per site. It defaults
    to `[name]`, matching a single-site bench, so a test that does not care is unaffected.
    """
    recorded = list(sites) if sites else [name]
    return SimpleNamespace(
        runtime=runtime,
        name=name,
        base_image=base_image,
        deploy_state=SimpleNamespace(current_tag=tag) if tag else None,
        sites={site: SiteConfig(database=cfg) for site, cfg in (database or {}).items()} or None,
        site_names=recorded,
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


def test_image_shape_binds_one_site_directory_per_site():
    """Sites are data and the image is code, so the image carries no site directories at all:
    a site that is not bind-mounted does not exist inside the container and `bench --site X`
    answers "404 Not Found". The four shared entries stay single binds because they are
    per-bench, not per-site, and duplicating them would be a compose error.
    """
    shape = runtime_shape(_cfg(BenchRuntime.image, name="multi", tag="r:t", sites=["a.localhost", "b.localhost"]))
    binds = shape.binds()
    targets = [b.container for b in binds]

    assert targets.count("/workspace/frappe-bench/sites/a.localhost") == 1
    assert targets.count("/workspace/frappe-bench/sites/b.localhost") == 1
    for shared in (
        "/workspace/frappe-bench/sites/common_site_config.json",
        "/workspace/frappe-bench/sites/apps.txt",
        "/workspace/frappe-bench/logs",
        "/workspace/frappe-bench/config",
    ):
        assert targets.count(shared) == 1
    assert len(targets) == 6  # two sites + the four shared entries, nothing else
    hosts = {b.container: b.host for b in binds}
    assert hosts["/workspace/frappe-bench/sites/b.localhost"] == "./workspace/frappe-bench/sites/b.localhost"


def test_runtime_shape_records_the_sites_never_the_bench_name():
    """Bench `shop`, site `shop.localhost`: deliberately different, because a bench whose name
    equals its site makes this bug invisible. Recording `config.name` here mounts a directory
    that does not exist while the real site stays unreachable inside the container.
    """
    shape = runtime_shape(_cfg(BenchRuntime.image, name="shop", tag="r:t", sites=["shop.localhost"]))

    assert shape.sites == ("shop.localhost",)
    targets = [b.container for b in shape.binds()]
    assert "/workspace/frappe-bench/sites/shop.localhost" in targets
    assert "/workspace/frappe-bench/sites/shop" not in targets  # the bench name is not a site


def test_data_binds_refuses_a_bare_site_string():
    """A `str` IS a `Sequence[str]`, so a single site name would iterate as CHARACTERS and mount
    `sites/s`, `sites/h`, `sites/o` ... The result is a valid compose file describing the wrong
    bench, which nothing downstream can detect, so the guard has to be the error.
    """
    with pytest.raises(TypeError, match=r"takes the bench's sites, not one site name"):
        data_binds("shop.localhost")

    with pytest.raises(TypeError, match=r"'shop\.localhost'"):  # the message names the value
        data_binds("shop.localhost")


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


def test_nothing_outside_the_web_services_is_marked_rolling():
    """`ServiceSpec.rolling` is the switch `render_image_compose` keys container_name shedding
    off, so it must be True for exactly the two scaled web services and nothing else.

    The redis suppression specs and every worker spec are built without naming the field at all.
    If a spec were rolling by accident, a rolling deploy would strip the container_name of a
    service it never scales and the canonical render would then hand it a `<bench>-<service>`
    name that was never meant to be managed here.
    """
    cfg = _cfg(BenchRuntime.image, tag="r:t")
    specs = bench_service_specs(cfg) + worker_service_specs(cfg, ["long-worker", "short-worker"])

    assert sorted(s.name for s in specs if s.rolling) == ["frappe", "nginx"]


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
    apply_specs(cfm, bench_service_specs(cfg), cfg.site_names)

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
    apply_specs(cfm, bench_service_specs(cfg), cfg.site_names)
    apply_specs(cfm, bench_service_specs(cfg), cfg.site_names)  # re-render, same tag
    apply_specs(cfm, bench_service_specs(cfg, RenderContext(deploy_tag="r:t2")), cfg.site_names)  # re-pin
    raw = list(cfm.yml["services"]["frappe"]["volumes"])
    assert len(raw) == len(set(raw))  # never duplicates
    assert cfm.yml["services"]["frappe"]["image"] == "r:t2"


def test_apply_specs_idempotent_on_a_two_site_bench(tmp_path):
    """The exact property that caught the bare-string bug: the strip set and the re-added binds
    are computed from the same sites, so a second pass has to be a no-op. Handed a site string
    instead of a list the two disagree, and every render piles more binds onto the service.
    """
    cfm = _cfm(tmp_path)
    cfg = _cfg(BenchRuntime.image, name="multi", tag="r:t1", sites=["a.localhost", "b.localhost"])
    apply_specs(cfm, bench_service_specs(cfg), cfg.site_names)
    first = list(cfm.yml["services"]["frappe"]["volumes"])

    apply_specs(cfm, bench_service_specs(cfg), cfg.site_names)  # re-render, same tag
    apply_specs(cfm, bench_service_specs(cfg, RenderContext(deploy_tag="r:t2")), cfg.site_names)  # re-pin

    raw = list(cfm.yml["services"]["frappe"]["volumes"])
    assert raw == first  # settled after the first pass, not merely free of duplicates
    assert len(raw) == len(set(raw))
    for site in cfg.site_names:
        assert sum(v.endswith(f":/workspace/frappe-bench/sites/{site}") for v in raw) == 1
    assert cfm.yml["services"]["frappe"]["image"] == "r:t2"


def test_apply_specs_mount_mode_round_trip(tmp_path):
    cfm = _cfm(tmp_path)
    cfg = _cfg(BenchRuntime.mount)
    apply_specs(cfm, bench_service_specs(cfg), cfg.site_names)
    fr = [str(v) for v in cfm.get_service_volumes("frappe")]
    assert "./workspace:/workspace" in fr  # mount keeps live workspace
    assert cfm.yml["services"]["frappe"]["image"] == default_code_image()  # stock pin applied


def test_apply_specs_switch_image_to_mount_restores_workspace(tmp_path):
    cfm = _cfm(tmp_path)
    apply_specs(cfm, bench_service_specs(_cfg(BenchRuntime.image, tag="r:t")), ["s.localhost"])
    apply_specs(cfm, bench_service_specs(_cfg(BenchRuntime.mount)), ["s.localhost"])
    fr = [str(v) for v in cfm.get_service_volumes("frappe")]
    assert "./workspace:/workspace" in fr
    assert not any("/workspace/frappe-bench/logs" in v for v in fr)  # data binds stripped
    ng = [str(v) for v in cfm.get_service_volumes("nginx")]
    # nginx serves assets from the workspace in mount mode; conf binds pass through.
    assert "./workspace:/workspace" in ng
    assert "./configs/nginx/conf:/etc/nginx" in ng
    assert cfm.yml["services"]["nginx"]["image"] == default_nginx_image()


def test_switch_two_site_bench_to_mount_strips_every_per_site_bind(tmp_path):
    """Mount mode serves the live workspace, so a leftover `sites/<site>` bind would shadow that
    site's directory with the image-mode data mount. Site 1 was always stripped; the strip set
    has to cover sites 2..N too, or the switch leaves an orphan behind.
    """
    cfm = _cfm(tmp_path)
    sites = ["a.localhost", "b.localhost"]
    apply_specs(cfm, bench_service_specs(_cfg(BenchRuntime.image, name="multi", tag="r:t", sites=sites)), sites)
    apply_specs(cfm, bench_service_specs(_cfg(BenchRuntime.mount, name="multi", sites=sites)), sites)

    fr = [str(v) for v in cfm.get_service_volumes("frappe")]
    assert "./workspace:/workspace" in fr
    assert not any("/workspace/frappe-bench/sites/" in v for v in fr)  # no orphan per-site bind
    assert not any("/workspace/frappe-bench/logs" in v for v in fr)
    assert "fm-sockets:/fm-sockets" in fr  # unmanaged mounts still pass through


def test_external_redis_disables_both_redis_services_and_dropping_it_re_enables(tmp_path):
    cfm = _cfm(tmp_path)
    cfg = _cfg(BenchRuntime.mount, redis=EXTERNAL_REDIS)
    apply_specs(cfm, bench_service_specs(cfg), cfg.site_names)

    for name in BENCH_REDIS_SERVICES:
        assert cfm.yml["services"][name]["profiles"] == ["disabled"]  # fm must not start them

    apply_specs(cfm, bench_service_specs(_cfg(BenchRuntime.mount)), cfg.site_names)  # [redis] removed
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
    apply_specs(control, tuple(s for s in specs if s.name not in BENCH_REDIS_SERVICES), cfg.site_names)
    control.write_to_file()

    rendered = _cfm(tmp_path)
    apply_specs(rendered, specs, cfg.site_names)
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


def test_default_render_context_is_not_a_rolling_swap():
    """Every renderer defaults to DEFAULT_CONTEXT, so its flags decide what a plain render does.

    `rolling=True` makes the renderer emit a rolling-swap shape (new replicas alongside the old
    ones). That is opt-in for `fm restart --rolling` on image benches; if the default ever flipped,
    every ordinary render would silently become a rolling swap. Nothing else asserted this, so
    mutation testing found `rolling: bool = False` could be inverted with the suite still green.
    """
    from frappe_manager.site_manager.modules.compose_shape import DEFAULT_CONTEXT

    assert DEFAULT_CONTEXT.rolling is False
    assert DEFAULT_CONTEXT.deploy_tag is None
