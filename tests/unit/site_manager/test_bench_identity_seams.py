"""`Bench.name` is three identities, and these are the seams that keep them apart.

A bench holds exactly one site today, and its name is simultaneously the bench identity (the
directory under `~/frappe/sites/`, the compose project, the address a user types), the Frappe
site name (the schema, `sites/<name>/`, the `--site` argument) and the served domain (nginx
`VIRTUAL_HOST`, the certificate subject, an HTTP `Host:` header). All three are the same string,
which is why nothing has ever had to distinguish them.

`site_name` and `primary_domain` exist so that callers say which one they mean. Their VALUES are
uninteresting today and deliberately so; what these tests defend is that each has exactly ONE
source, because the whole point is that decoupling the names later is a change in one place rather
than a hunt through 176 call sites. A test that only asserted `bench.site_name == bench.name`
would pass just as well if a caller went back to reading `bench.name` directly, so the assertions
below are about the seam, not the string.
"""

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from frappe_manager.site_manager.bench_config import BenchConfig
from frappe_manager.site_manager.site import Bench

BENCH = "shop"
SITE = "shop.localhost"


def _bench(name: str, *, alias_domains: list[str] | None = None) -> Bench:
    bench = Bench.__new__(Bench)  # bypass __init__: no docker, no compose, no services
    bench.name = name
    bench.bench_config = SimpleNamespace(alias_domains=alias_domains or [])
    return bench


# --------------------------------------------------------------------------- the site seam


def test_the_site_comes_from_the_bench_directory_not_the_config():
    """The config's `name` is the BENCH. The target shape is `name = "shop"` beside
    `[sites."shop.localhost"]`, so reading the site off the config would be wrong the moment the
    two differ, and it is the bench directory that production writes `sites/<name>/` from."""
    bench = _bench(SITE)
    bench.bench_config.name = "something-else-entirely"

    assert bench.site_name == SITE


# ------------------------------------------------------------------------- the domain seam


def test_the_primary_domain_comes_from_the_bench_and_not_the_config():
    """Same reasoning as the site: after decoupling the primary domain is the site's name, not
    `BenchConfig.name`, so sourcing it from the config would have to be undone."""
    bench = _bench(SITE)
    bench.bench_config.name = "something-else-entirely"

    assert bench.primary_domain == SITE


def test_the_domain_list_is_the_primary_then_the_aliases_in_order():
    """Order is load-bearing: `export_to_compose_inputs` joins this into `VIRTUAL_HOST`, and
    nginx-proxy treats the first host as the canonical one."""
    bench = _bench(SITE, alias_domains=["www.shop.example.com", "shop.example.com"])

    assert bench.domains == [SITE, "www.shop.example.com", "shop.example.com"]


def test_the_domain_list_is_built_from_the_single_domain_accessor():
    bench = _bench(SITE, alias_domains=["alias.localhost"])
    assert bench.domains[0] == bench.primary_domain


def test_a_bench_with_no_aliases_serves_only_its_primary_domain():
    assert _bench(SITE).domains == [SITE]


def test_a_none_alias_list_is_treated_as_empty():
    """`alias_domains` defaults to `[]` on a real config, but a partially built one and older
    configs can carry None, and this list is spread into compose material."""
    bench = _bench(SITE)
    bench.bench_config.alias_domains = None

    assert bench.domains == [SITE]


# ------------------------------------------------- the config-side accessors, for callers holding one


@pytest.fixture
def config(tmp_path):
    """A real BenchConfig, because `get_site_mappings` is what reaches the nginx entrypoint."""
    toml = tmp_path / "bench_config.toml"
    toml.write_text(
        f'name = "{SITE}"\ndeveloper_mode = false\nadmin_tools = false\nenvironment = "prod"\n'
        'alias_domains = ["www.shop.example.com"]\n'
    )
    return BenchConfig.import_from_toml(toml)


def test_the_config_serves_its_name_then_its_aliases(config):
    assert config.domains == [SITE, "www.shop.example.com"]


def test_the_config_domain_list_starts_at_its_primary_domain(config):
    assert config.domains[0] == config.primary_domain


def test_site_mappings_map_every_served_domain(config):
    """The nginx entrypoint routes by Host, so a domain missing from this map is a domain that
    404s. Every alias must appear, not just the primary."""
    assert set(config.get_site_mappings()) == set(config.domains)


def test_site_mappings_point_every_domain_at_the_site(config):
    """domain -> site. Both halves are the bench name today, which is the identity collapse in one
    expression; when `[sites]` lands the value comes from the site owning each domain."""
    assert set(config.get_site_mappings().values()) == {SITE}


def test_the_config_does_not_offer_a_site_accessor():
    """Deliberate absence, not an oversight. A `primary_site` on the config model would return
    `self.name`, look authoritative, and be wrong as soon as a bench named `shop` serves
    `shop.localhost`. The site question belongs to `Bench`, which reads the directory."""
    assert not hasattr(BenchConfig, "primary_site")


# --------------------------------------------------------------- the roles are not interchangeable


def test_the_site_and_the_domain_are_separate_accessors():
    """They return the same string today and are read by different call sites for different
    reasons: 30 mean the site, 9 mean the domain. Collapsing them back into one accessor is what
    this asserts against, because that is the state the seams exist to leave behind."""
    assert Bench.site_name is not Bench.primary_domain


def test_a_mock_bench_that_sets_only_name_does_not_satisfy_the_seams():
    """Guards the test suite itself. Several fixtures build a bench with `MagicMock()`; one that
    sets `name` alone hands a MagicMock to any caller that correctly asks for the site, which is a
    silent pass rather than a failure. This is the shape those fixtures must avoid."""
    mock = MagicMock()
    mock.name = SITE

    assert not isinstance(mock.site_name, str)


# ------------------------------------------------- the refusal that keeps a bench directory
# `orphaned_database_error` had no test at all, which is how a site-meaning read of `bench.name`
# survived the first sweep through this file. It is a data-safety path: it fires when a schema
# could not be dropped, and it keeps the bench directory precisely because that directory holds
# the only record of the schema's name and password.


def _orphaned(tmp_path, db_info: dict) -> str:
    from frappe_manager.site_manager.site import orphaned_database_error

    # A duck-typed stand-in rather than a real Bench, and deliberately so: this is a module-level
    # function, so a stand-in can carry a bench name that DIFFERS from its site name. A real Bench
    # cannot today (`site_name` is a property over `name`), which means a real one could not tell
    # a site-meaning read from a bench-meaning one and the assertions below would prove nothing.
    bench = SimpleNamespace(
        name=BENCH,
        site_name=SITE,
        path=tmp_path / BENCH,
        get_db_connection_info=lambda: db_info,
    )
    return orphaned_database_error(bench, RuntimeError("global-db refused the drop")).message


def test_the_refusal_hands_over_the_statements_when_the_schema_is_known(tmp_path):
    message = _orphaned(tmp_path, {"name": "fm_shop_abc123", "user": "fm_shop_abc123"})

    assert "DROP DATABASE IF EXISTS `fm_shop_abc123`;" in message
    assert "DROP USER IF EXISTS 'fm_shop_abc123'@'%';" in message


def test_the_refusal_points_at_the_site_config_when_the_schema_is_unreadable(tmp_path):
    """The fallback names the file to read the schema out of. That file sits under the SITE
    directory, while the `fm delete` command beside it takes the BENCH: one statement, both
    identities. With the two names distinct, reading the wrong one fails here."""
    message = _orphaned(tmp_path, {}).replace("\\", "/")

    assert f"sites/{SITE}/site_config.json" in message
    assert f"sites/{BENCH}/site_config.json" not in message
    assert f"fm delete {BENCH} --yes --no-delete-db-from-global-db" in message


def test_the_refusal_keeps_the_bench_directory_and_says_so(tmp_path):
    """Removing it would destroy the only record of the schema, so the message must not read as
    though the bench is already gone."""
    message = _orphaned(tmp_path, {})

    assert str(tmp_path / BENCH) in message
    assert "kept at" in message


def test_an_unreadable_connection_does_not_break_the_refusal(tmp_path):
    """The drop has already failed; a second failure while composing the explanation would replace
    a useful message with a traceback."""
    from frappe_manager.site_manager.site import orphaned_database_error

    bench = _bench(SITE)
    bench.path = tmp_path / SITE

    def explode():
        raise OSError("site_config.json is unreadable")

    bench.get_db_connection_info = explode

    assert "site_config.json" in orphaned_database_error(bench, RuntimeError("boom")).message
