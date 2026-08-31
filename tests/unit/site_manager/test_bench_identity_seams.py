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
from frappe_manager.site_manager.exceptions import BenchException
from frappe_manager.site_manager.site import Bench

BENCH = "shop"
SITE = "shop.localhost"


def _bench(name: str = BENCH, *, site: str | None = SITE, alias_domains: list[str] | None = None) -> Bench:
    """A bench whose name and site are DIFFERENT strings by default.

    This is what the `[sites]` table bought. While a bench was its own site, no fixture here could
    tell a site-meaning read from a bench-meaning one, and these tests could only pre-wire the
    roles. Now the site is recorded separately, so `shop` versus `shop.localhost` makes reading the
    wrong one fail.
    """
    bench = Bench.__new__(Bench)  # bypass __init__: no docker, no compose, no services
    bench.name = name
    bench.bench_config = SimpleNamespace(
        name=name,
        alias_domains=alias_domains or [],
        sites={site: SimpleNamespace(database=None)} if site else None,
    )
    return bench


# --------------------------------------------------------------------------- the site seam


def test_the_site_is_the_recorded_one_and_not_the_bench_name():
    """The whole point of recording it: the bench directory says `shop` and the Frappe site is
    `shop.localhost`, so anything that reads the directory where it means the site is now wrong."""
    bench = _bench()

    assert bench.name == BENCH
    assert bench.site_name == SITE


def test_an_entry_named_after_the_bench_wins():
    """A config describing several sites still resolves to the bench's own, which is the tie-break
    a bench-scoped command needs."""
    bench = _bench(BENCH, site=None)
    bench.bench_config.sites = {"other.localhost": SimpleNamespace(database=None), BENCH: SimpleNamespace(database=None)}

    assert bench.site_name == BENCH


def test_nothing_recorded_falls_back_to_the_bench_name():
    """Not a compatibility branch: `Bench` objects exist mid-create, before the config is
    assembled, and during a migration part-way through writing the table."""
    assert _bench(BENCH, site=None).site_name == BENCH


def test_several_sites_none_matching_is_refused_rather_than_guessed():
    """Guessing would point a bench-scoped command at another site's schema."""
    bench = _bench(BENCH, site=None)
    bench.bench_config.sites = {
        "a.example.com": SimpleNamespace(database=None),
        "b.example.com": SimpleNamespace(database=None),
    }

    with pytest.raises(BenchException, match="cannot tell which one"):
        _ = bench.site_name


# ------------------------------------------------------------------------- the domain seam


def test_the_served_domain_is_the_site_and_not_the_bench():
    """A site is a schema addressed by hostname, so the domain follows the site. Returning the bench
    name would put an unroutable host into `VIRTUAL_HOST` and a `Host:` header the readiness probe
    cannot match."""
    assert _bench().primary_domain == SITE


def test_the_domain_list_is_the_primary_then_the_aliases_in_order():
    """Order is load-bearing: `export_to_compose_inputs` joins this into `VIRTUAL_HOST`, and
    nginx-proxy treats the first host as the canonical one."""
    bench = _bench(alias_domains=["www.shop.example.com", "shop.example.com"])

    assert bench.domains == [SITE, "www.shop.example.com", "shop.example.com"]


def test_the_domain_list_is_built_from_the_single_domain_accessor():
    bench = _bench(alias_domains=["alias.localhost"])
    assert bench.domains[0] == bench.primary_domain


def test_a_bench_with_no_aliases_serves_only_its_primary_domain():
    assert _bench().domains == [SITE]


def test_a_none_alias_list_is_treated_as_empty():
    """`alias_domains` defaults to `[]` on a real config, but a partially built one and older
    configs can carry None, and this list is spread into compose material."""
    bench = _bench()
    bench.bench_config.alias_domains = None

    assert bench.domains == [SITE]


# ------------------------------------------------- the config-side accessors, for callers holding one


@pytest.fixture
def config(tmp_path):
    """A real BenchConfig whose bench name and site name DIFFER, because that is the shape now."""
    toml = tmp_path / "bench_config.toml"
    toml.write_text(
        f'name = "{BENCH}"\ndeveloper_mode = false\nadmin_tools = false\nenvironment = "prod"\n'
        'alias_domains = ["www.shop.example.com"]\n'
        f'\n[sites."{SITE}"]\n'
    )
    return BenchConfig.import_from_toml(toml)


def test_the_config_serves_its_site_then_its_aliases(config):
    assert config.domains == [SITE, "www.shop.example.com"]


def test_the_config_site_lookup_defaults_to_the_recorded_site(config):
    """`get_site()` with no argument must reach the recorded entry. Defaulting to `name` would find
    nothing the moment the bench is called `shop` and its site `shop.localhost`."""
    assert config.get_site() is not None
    assert config.get_site(SITE) is not None
    assert config.get_site(BENCH) is None


def test_the_config_domain_list_starts_at_its_primary_domain(config):
    assert config.domains[0] == config.primary_domain


def test_site_mappings_map_every_served_domain(config):
    """The nginx entrypoint routes by Host, so a domain missing from this map is a domain that
    404s. Every alias must appear, not just the primary."""
    assert set(config.get_site_mappings()) == set(config.domains)


def test_site_mappings_point_every_domain_at_the_site(config):
    """domain -> site, and the value is the SITE not the bench: nginx hands it to Frappe as the site
    to serve, so a bench called `shop` sending `shop` would name a `sites/` directory that does not
    exist. With the two names distinct this fails if the wrong one is used."""
    assert set(config.get_site_mappings().values()) == {SITE}
    assert BENCH not in config.get_site_mappings().values()



# ------------------------------------------------------- what create records, and under which name


def test_the_site_is_recorded_under_the_site_name():
    """Keyed by the SITE. Keying it by the bench would put the entry under `shop` while everything
    that reads it asks for `shop.localhost`, so the record would never be found."""
    from frappe_manager.commands.create import record_site

    recorded = record_site(None, SITE, None)

    assert list(recorded) == [SITE]
    assert BENCH not in recorded


def test_recording_a_site_carries_its_database():
    from frappe_manager.commands.create import record_site
    from frappe_manager.site_manager.bench_config import DatabaseConfig

    database = DatabaseConfig(host="rds.internal", name="app_prod")
    recorded = record_site(None, SITE, database)

    assert recorded[SITE].database is not None
    assert recorded[SITE].database.host == "rds.internal"


def test_recording_a_site_that_a_config_overlay_already_described_updates_it():
    """A `--config` file can declare the site; the database is merged into that entry rather than
    replacing it, so anything else the overlay set survives."""
    from frappe_manager.commands.create import record_site
    from frappe_manager.site_manager.bench_config import DatabaseConfig, SiteConfig

    database = DatabaseConfig(host="rds.internal", name="app_prod")
    recorded = record_site({SITE: SiteConfig()}, SITE, database)

    assert list(recorded) == [SITE]
    assert recorded[SITE].database.host == "rds.internal"


def test_a_bench_with_no_external_database_still_records_its_site():
    """The global-db case, which is most benches. Without the entry the site would have no name of
    its own anywhere on disk."""
    from frappe_manager.commands.create import record_site

    recorded = record_site(None, SITE, None)

    assert recorded[SITE].database is None


def test_the_global_db_schema_is_minted_from_the_site_not_the_bench():
    """The schema belongs to the site, so two benches serving differently-named sites must not be
    able to collide, and renaming a bench must not imply a different schema."""
    from frappe_manager.commands.create import mint_global_db_schema_name

    minted = mint_global_db_schema_name(SITE)

    assert minted.startswith("fm_shop_localhost_")
    assert not minted.startswith("fm_shop_f")  # i.e. not minted from the bare bench name


def test_the_minted_schema_name_is_a_legal_identifier():
    """Dots and hyphens are illegal in a MariaDB schema name unquoted, and this one is interpolated
    into `bench new-site --db-name`."""
    from frappe_manager.commands.create import mint_global_db_schema_name

    minted = mint_global_db_schema_name("a-b.example.com")

    assert "." not in minted
    assert "-" not in minted


def test_two_mints_for_one_site_still_differ():
    """The random suffix is what actually guarantees uniqueness; the prefix is for a human reading
    `SHOW DATABASES`."""
    from frappe_manager.commands.create import mint_global_db_schema_name

    assert mint_global_db_schema_name(SITE) != mint_global_db_schema_name(SITE)


def test_the_config_reads_its_site_from_the_recorded_table(tmp_path):
    """A `primary_site` on the config was deliberately absent while there was no `[sites]` table,
    because one returning `self.name` would look authoritative and be wrong as soon as a bench named
    `shop` served `shop.localhost`. The table records it now, so the accessor exists and the thing
    worth asserting is that it reads the RECORD and not the bench name."""
    toml = tmp_path / "bench_config.toml"
    toml.write_text(
        f'name = "{BENCH}"\ndeveloper_mode = false\nadmin_tools = false\nenvironment = "prod"\n\n[sites."{SITE}"]\n'
    )
    config = BenchConfig.import_from_toml(toml)

    assert config.name == BENCH
    assert config.primary_site == SITE


def test_the_config_domain_is_the_site_and_not_the_bench(tmp_path):
    """A site is a schema addressed by hostname, so its name IS its domain. Returning the bench name
    would put an unroutable host into `VIRTUAL_HOST` and a certificate subject nothing resolves."""
    toml = tmp_path / "bench_config.toml"
    toml.write_text(
        f'name = "{BENCH}"\ndeveloper_mode = false\nadmin_tools = false\nenvironment = "prod"\n\n[sites."{SITE}"]\n'
    )
    config = BenchConfig.import_from_toml(toml)

    assert config.primary_domain == SITE
    assert config.get_site_mappings() == {SITE: SITE}


# --------------------------------------------------------------------- routing N sites
# What makes a second site reachable at all: every site's own domain has to be published, and each
# has to map to ITSELF. Mapping both hostnames to one site would route them at the same schema, so
# the second site would answer with the first site's data rather than 404 -- a wrong answer, not a
# missing one.

SECOND = "b.example.com"


def _multi(tmp_path, *sites: str, aliases: str = "") -> BenchConfig:
    toml = tmp_path / "bench_config.toml"
    body = "".join(f'\n[sites."{s}"]\n' for s in sites)
    toml.write_text(
        f'name = "{BENCH}"\ndeveloper_mode = false\nadmin_tools = false\nenvironment = "prod"\n{aliases}{body}'
    )
    return BenchConfig.import_from_toml(toml)


def test_every_site_is_published_as_a_domain(tmp_path):
    config = _multi(tmp_path, SITE, SECOND)

    assert config.domains == [SITE, SECOND]


def test_each_site_maps_to_itself(tmp_path):
    config = _multi(tmp_path, SITE, SECOND)

    assert config.get_site_mappings() == {SITE: SITE, SECOND: SECOND}


def test_the_primary_sites_domain_leads(tmp_path):
    """nginx-proxy treats the first host in `VIRTUAL_HOST` as the canonical one, so the order is
    behaviour and not presentation."""
    config = _multi(tmp_path, SITE, SECOND)

    assert config.domains[0] == config.primary_site


def test_a_bench_alias_is_served_by_the_primary_site(tmp_path):
    """`alias_domains` is still bench-level, so an alias has no site of its own to belong to."""
    config = _multi(tmp_path, SITE, SECOND, aliases='alias_domains = ["www.shop.example.com"]\n')

    assert config.get_site_mappings()["www.shop.example.com"] == SITE
    assert "www.shop.example.com" in config.domains


def test_sites_can_be_enumerated_even_when_no_primary_can_be_chosen(tmp_path):
    """Enumeration is not selection. A bench whose recorded sites include none named after it cannot
    say which one a bench-scoped command means, and `primary_site` refuses; but routing still has to
    publish both, so listing them must not depend on that choice."""
    config = _multi(tmp_path, "a.example.com", SECOND)

    assert set(config.site_names) == {"a.example.com", SECOND}
    assert set(config.get_site_mappings()) == {"a.example.com", SECOND}
    with pytest.raises(ValueError, match="cannot tell which one"):
        _ = config.primary_site


def test_a_single_site_bench_routes_exactly_as_before(tmp_path):
    """The N-site change must not alter the one-site case, which is every existing bench."""
    config = _multi(tmp_path, SITE)

    assert config.domains == [SITE]
    assert config.get_site_mappings() == {SITE: SITE}


# --------------------------------------------------------------- the roles are not interchangeable


def test_the_site_and_the_domain_are_separate_accessors():
    """They resolve to the same string, because a site's name is its domain, but they are read by
    different call sites for different reasons: 30 mean the site, 9 mean the domain. Collapsing them
    into one accessor is what this asserts against."""
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
