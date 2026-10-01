"""A bench's `client_max_body_size` moves from the flat `custom/upload-limit.conf` to one file
per site, by the 1.0.0 migration, once its nginx conf can serve per-site drop-ins.

`custom/*.conf` is included in every site's server block, and a second `client_max_body_size`
directive in one nginx context is fatal even when the two copies arrive through different
`include`d files (measured on a live proxy) -- so the flat file and the per-site files can never
coexist. This mirrors the probe and the sweep `fm auth` already uses for per-site `auth_basic`
(`Bench.nginx_conf_serves_per_site()` / `ensure_fm_nginx_confs()`, site.py).
"""

from unittest.mock import MagicMock

import pytest

from frappe_manager.migration_manager.migrations.migrate_1_0_0 import MigrationV100

# The per-site `include` every modern nginx image template renders into default.conf; its
# presence is the only thing `nginx_conf_serves_per_site()` checks for.
PER_SITE_DEFAULT_CONF = """server {
    listen 80;
    include /etc/nginx/custom/shop.localhost/*.conf;
    include /etc/nginx/custom/*.conf;
}
"""

# A conf rendered before per-site drop-in directories existed: only the bench-wide `custom/*.conf`
# wildcard.
FLAT_DEFAULT_CONF = """server {
    listen 80;
    include /etc/nginx/custom/*.conf;
}
"""

BENCH_CONFIG = """upload_limit = "50M"

[sites."shop.localhost"]
"""


@pytest.fixture
def step():
    migration = MigrationV100.__new__(MigrationV100)  # bypass __init__: no executor, no backups
    migration.output = MagicMock()
    migration.backup_manager = MagicMock()
    return migration


def _bench(tmp_path, default_conf: str | None, config: str = BENCH_CONFIG, site_names=("shop.localhost",)):
    conf_d = tmp_path / "configs" / "nginx" / "conf" / "conf.d"
    conf_d.mkdir(parents=True)
    if default_conf is not None:
        (conf_d / "default.conf").write_text(default_conf)

    (tmp_path / "bench_config.toml").write_text(config)

    bench = MagicMock()
    bench.path = tmp_path
    bench.name = "shop.localhost"
    bench.site_names = list(site_names)
    return bench


def _custom_dir(tmp_path):
    return tmp_path / "configs" / "nginx" / "conf" / "custom"


def _flat(tmp_path):
    return _custom_dir(tmp_path) / "upload-limit.conf"


def _per_site(tmp_path, site):
    return _custom_dir(tmp_path) / site / "upload-limit.conf"


def test_a_per_site_capable_bench_gets_one_file_per_site(step, tmp_path):
    """The gap: a conf that already supports per-site blocks, but still carries the old flat
    file because nothing had swept it yet."""
    bench = _bench(tmp_path, PER_SITE_DEFAULT_CONF)
    _flat(tmp_path).parent.mkdir(parents=True, exist_ok=True)
    _flat(tmp_path).write_text("client_max_body_size 50m;\n")

    step._migrate_upload_limit_layout(bench)

    assert _per_site(tmp_path, "shop.localhost").read_text() == "client_max_body_size 50m;\n"
    assert not _flat(tmp_path).exists()


def test_a_bench_that_cannot_serve_per_site_keeps_the_flat_file(step, tmp_path):
    """Do not force a per-site layout onto a conf that does not `include` per-site directories:
    the per-site file would be written and never read, serving the site unprotected."""
    bench = _bench(tmp_path, FLAT_DEFAULT_CONF)

    step._migrate_upload_limit_layout(bench)

    assert _flat(tmp_path).read_text() == "client_max_body_size 50m;\n"
    assert not _per_site(tmp_path, "shop.localhost").exists()


def test_a_bench_with_no_rendered_conf_yet_keeps_the_flat_file(step, tmp_path):
    """An absent conf counts as NOT supporting per-site: nothing is being served yet, and the
    flat file is correct under either template."""
    bench = _bench(tmp_path, default_conf=None)

    step._migrate_upload_limit_layout(bench)

    assert _flat(tmp_path).read_text() == "client_max_body_size 50m;\n"
    assert not _per_site(tmp_path, "shop.localhost").exists()


def test_exactly_one_directive_applies_per_site(step, tmp_path):
    """Two sites, each getting their own effective limit; never both a flat file and per-site
    files at once."""
    config = """upload_limit = "50M"

[sites."shop.localhost"]
upload_limit = "200M"

[sites."blog.localhost"]
"""
    bench = _bench(tmp_path, PER_SITE_DEFAULT_CONF, config=config, site_names=["shop.localhost", "blog.localhost"])
    _flat(tmp_path).parent.mkdir(parents=True, exist_ok=True)
    _flat(tmp_path).write_text("client_max_body_size 50m;\n")

    step._migrate_upload_limit_layout(bench)

    assert _per_site(tmp_path, "shop.localhost").read_text() == "client_max_body_size 200m;\n"
    assert _per_site(tmp_path, "blog.localhost").read_text() == "client_max_body_size 50m;\n"
    assert not _flat(tmp_path).exists()


def test_an_operators_hand_written_flat_conf_is_not_destroyed(step, tmp_path):
    """fm only sweeps a flat file in its own exact shape; anything else is the operator's call,
    same rule `_add_nginx_depends_on` already applies to a hand-written compose key."""
    bench = _bench(tmp_path, PER_SITE_DEFAULT_CONF)
    _flat(tmp_path).parent.mkdir(parents=True, exist_ok=True)
    _flat(tmp_path).write_text("client_max_body_size 999m; # pinned by ops, do not touch\n")

    step._migrate_upload_limit_layout(bench)

    assert _flat(tmp_path).read_text() == "client_max_body_size 999m; # pinned by ops, do not touch\n"
    # The per-site file is still written: the operator's flat copy is simply not read by a
    # per-site-capable conf, so leaving it behind would not be serving anything.
    assert _per_site(tmp_path, "shop.localhost").read_text() == "client_max_body_size 50m;\n"


def test_an_operators_hand_written_per_site_conf_is_not_destroyed(step, tmp_path):
    bench = _bench(tmp_path, FLAT_DEFAULT_CONF)
    per_site_path = _per_site(tmp_path, "shop.localhost")
    per_site_path.parent.mkdir(parents=True, exist_ok=True)
    per_site_path.write_text("client_max_body_size 999m; # pinned by ops\n")

    step._migrate_upload_limit_layout(bench)

    assert per_site_path.read_text() == "client_max_body_size 999m; # pinned by ops\n"


def test_the_step_is_idempotent(step, tmp_path):
    bench = _bench(tmp_path, PER_SITE_DEFAULT_CONF)
    _flat(tmp_path).parent.mkdir(parents=True, exist_ok=True)
    _flat(tmp_path).write_text("client_max_body_size 50m;\n")

    step._migrate_upload_limit_layout(bench)
    first = _per_site(tmp_path, "shop.localhost").read_text()
    step._migrate_upload_limit_layout(bench)

    assert _per_site(tmp_path, "shop.localhost").read_text() == first
    assert not _flat(tmp_path).exists()


def test_idempotent_when_the_bench_stays_flat(step, tmp_path):
    bench = _bench(tmp_path, FLAT_DEFAULT_CONF)

    step._migrate_upload_limit_layout(bench)
    first = _flat(tmp_path).read_text()
    step._migrate_upload_limit_layout(bench)

    assert _flat(tmp_path).read_text() == first


def test_a_legacy_bench_with_no_sites_table_falls_back_to_its_own_name(step, tmp_path):
    """Pre-decoupling benches have no `[sites]` table; the bench name IS the site name, the same
    fallback `MigrationBench.site_names` uses everywhere else in this migration."""
    bench = _bench(tmp_path, PER_SITE_DEFAULT_CONF, config='upload_limit = "50M"\n')

    step._migrate_upload_limit_layout(bench)

    assert _per_site(tmp_path, "shop.localhost").read_text() == "client_max_body_size 50m;\n"


def test_a_warning_names_overridden_sites_stuck_on_the_bench_wide_limit(step, tmp_path):
    config = """upload_limit = "50M"

[sites."shop.localhost"]
upload_limit = "200M"
"""
    bench = _bench(tmp_path, FLAT_DEFAULT_CONF, config=config)

    step._migrate_upload_limit_layout(bench)

    assert step.output.warning.called
    assert "shop.localhost" in step.output.warning.call_args[0][0]
