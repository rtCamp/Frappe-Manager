"""`--redis-cache`/`--redis-queue` accept only `redis://` and `rediss://`.

Anything else is written verbatim into `bench_config.toml` and then into
`common_site_config.json` (`bench_database.py`'s `sync_common_site_config`), where
nothing downstream can read it: redis-py's `from_url` raises `ValueError` at connect
time and node-redis (socketio) raises `TypeError('Invalid protocol')`. fm's own
create-time readiness probe does not catch it either -- a sentinel or proxy still
answers on its TCP port -- so the failure used to surface as a bare traceback mid
`bench migrate` instead of here, at the command line, before anything exists.

`unix://` is refused too, even though redis-py itself accepts it: fm's own readiness
probe (`bench_site.py`'s `BenchSiteManager._redis_endpoint`) raises for any URL with
no hostname, and a unix socket URL never has one, so accepting `unix://` here would
only defer the exact same failure by a few seconds.

The check is CLI-only (`_refuse_unsupported_redis_scheme`, called from
`_resolve_redis`), not a `RedisConfig` model validator: `collect_from_data` builds a
`RedisConfig` on every load of `bench_config.toml`, including from `fm list`, `fm
bake`, `fm switch` and `fm maintenance`, which skip the migration gate precisely so
one bench's bad file cannot take the rest of the host down with it. Raising there
would reopen that hole for a hand-edited `[redis]` table.
"""

import pytest
import typer

from frappe_manager.commands.create import _refuse_unsupported_redis_scheme, _resolve_redis
from frappe_manager.site_manager.bench_config import RedisConfig


def test_a_redis_pair_on_distinct_databases_is_accepted():
    cfg = _resolve_redis("redis://r.example:6379/0", "redis://r.example:6379/1")
    assert cfg == RedisConfig(cache="redis://r.example:6379/0", queue="redis://r.example:6379/1")


def test_rediss_is_accepted():
    cfg = _resolve_redis("rediss://r.example:6379/0", "rediss://r.example:6379/1")
    assert cfg.cache.startswith("rediss://")


def test_sentinel_is_refused_and_says_sentinel_is_not_a_url_form():
    with pytest.raises(typer.BadParameter) as exc:
        _resolve_redis("redis+sentinel://host:26379/mymaster/0", "redis://q.example:6379/1")
    said = str(exc.value)
    assert "--redis-cache" in said
    assert "redis:// and rediss://" in said
    # The message must not leave "unsupported scheme" standing alone: an operator reading
    # only that would reasonably conclude fm cannot talk to their sentinel setup at all.
    assert "sentinel" in said.lower()
    assert "never through a URL scheme" in said


def test_unix_socket_urls_are_refused_not_silently_accepted():
    """Pinned: redis-py itself supports unix://, but fm's readiness probe raises for any
    URL with no host, and every unix:// URL is hostless -- accepting it here would only
    defer that same failure to a few seconds into `create`."""
    with pytest.raises(typer.BadParameter) as exc:
        _resolve_redis("unix:///var/run/redis/redis.sock?db=0", "redis://q.example:6379/1")
    assert "redis:// and rediss://" in str(exc.value)


def test_an_unsupported_scheme_on_either_flag_is_caught():
    with pytest.raises(typer.BadParameter) as exc:
        _refuse_unsupported_redis_scheme("redis://c.example:6379/0", "redis+sentinel://q:26379/m/1")
    assert "--redis-queue" in str(exc.value)


def test_a_url_with_no_scheme_at_all_is_refused():
    with pytest.raises(typer.BadParameter) as exc:
        _refuse_unsupported_redis_scheme("//c.example:6379/0", "redis://q.example:6379/1")
    assert "--redis-cache" in str(exc.value)
