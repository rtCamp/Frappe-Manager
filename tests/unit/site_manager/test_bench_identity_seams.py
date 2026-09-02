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

import json
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from frappe_manager.site_manager.bench_config import BenchConfig
from frappe_manager.site_manager.exceptions import BenchException
from frappe_manager.site_manager.site import Bench

BENCH = "shop"
SITE = "shop.localhost"


def _bench(name: str = BENCH, *, site: str | None = SITE, domains: list[str] | None = None) -> Bench:
    """A bench whose name and site are DIFFERENT strings by default.

    This is what the `[sites]` table bought. While a bench was its own site, no fixture here could
    tell a site-meaning read from a bench-meaning one, and these tests could only pre-wire the
    roles. Now the site is recorded separately, so `shop` versus `shop.localhost` makes reading the
    wrong one fail.

    `domains` is the list the config publishes, not a bench-level alias list to be composed with the
    primary: `Bench.domains` reads it whole, so the stand-in has to carry the finished list. Tests
    about what that list CONTAINS belong on the config, which is where it is now built.
    """
    bench = Bench.__new__(Bench)  # bypass __init__: no docker, no compose, no services
    bench.name = name
    bench.bench_config = SimpleNamespace(
        name=name,
        domains=domains if domains is not None else ([site] if site else [name]),
        sites={site: SimpleNamespace(database=None, alias_domains=[])} if site else None,
    )
    return bench


def _bench_over(config: BenchConfig) -> Bench:
    """The same bypass, over a REAL config. `Bench.domains` delegates, so the only way to test what
    it publishes for a multi-site bench is to let the real property build the list."""
    bench = Bench.__new__(Bench)
    bench.name = config.name
    bench.bench_config = config
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


def test_the_domain_list_is_the_configs_own_and_is_not_recomposed():
    """`Bench.domains` reads ONE accessor. It used to compose `primary_domain` with a bench-level
    alias list, which made enumeration depend on selection: it omitted every non-primary site's
    domain and raised outright on a bench whose primary is ambiguous (both pinned under "routing N
    sites" below). The seam that keeps that from coming back is that the bench contributes nothing
    of its own to the list, so what the list CONTAINS is tested on the config, where it is built."""
    published = [SITE, "www.shop.example.com", "b.example.com"]
    bench = _bench(domains=published)

    assert bench.domains == published


def test_a_bench_with_one_plain_site_serves_only_that_domain():
    assert _bench().domains == [SITE]


# ------------------------------------------------- the config-side accessors, for callers holding one


@pytest.fixture
def config(tmp_path):
    """A real BenchConfig whose bench name and site name DIFFER, because that is the shape now.

    The alias is recorded under the SITE it serves. A bench-level list could not say which site an
    alias reached, so there is no longer a top-level key to write it to.
    """
    toml = tmp_path / "bench_config.toml"
    toml.write_text(
        f'name = "{BENCH}"\ndeveloper_mode = false\nadmin_tools = false\nenvironment = "prod"\n'
        f'\n[sites."{SITE}"]\n'
        'alias_domains = ["www.shop.example.com"]\n'
    )
    return BenchConfig.import_from_toml(toml)


def test_the_config_serves_its_site_then_its_aliases(config):
    """Order is load-bearing: `export_to_compose_inputs` joins this into `VIRTUAL_HOST` and
    nginx-proxy treats the first host as the canonical one, so the site's own name leads and its
    alternates follow it."""
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


def _multi(tmp_path, *sites: str, aliases: dict[str, list[str]] | None = None) -> BenchConfig:
    """N recorded sites, each carrying ITS OWN alternates: `alias_domains` sits under the site table
    now, so there is no bench-wide list a fixture could write."""
    per_site = aliases or {}
    body = ""
    for site in sites:
        body += f'\n[sites."{site}"]\n'
        if per_site.get(site):
            body += f"alias_domains = {json.dumps(per_site[site])}\n"
    toml = tmp_path / "bench_config.toml"
    toml.write_text(
        f'name = "{BENCH}"\ndeveloper_mode = false\nadmin_tools = false\nenvironment = "prod"\n{body}'
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


def test_an_alias_reaches_the_site_it_is_recorded_under(tmp_path):
    """The reason the list moved. Bench-level, an alias had no site to belong to, so the mapping had
    to send every one of them to the PRIMARY: an alternate of the second site answered with the
    first site's data -- a wrong answer, not a missing one. Each alias now maps to its own site."""
    config = _multi(
        tmp_path, SITE, SECOND, aliases={SITE: ["www.shop.example.com"], SECOND: ["www.b.example.com"]}
    )

    mappings = config.get_site_mappings()
    assert mappings["www.shop.example.com"] == SITE
    assert mappings["www.b.example.com"] == SECOND
    # Each site's own name, then that site's alternates: the list reads as the routing table.
    assert config.domains == [SITE, "www.shop.example.com", SECOND, "www.b.example.com"]


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


# The same two facts read through `Bench`, because that is the object every consumer holds and both
# of these were live bugs while it composed the list itself instead of delegating.


def test_the_bench_publishes_every_site_even_when_no_primary_can_be_chosen(tmp_path):
    """It RAISED. Composing from `primary_domain` made enumeration depend on selection, so a bench
    called `shop` recording only `a.example.com` and `b.example.com` could not name its hostnames at
    all -- and the consumers are worker `extra_hosts` and the per-domain upload caps, neither of
    which is choosing a site. Only a caller asking which site a command means may refuse."""
    bench = _bench_over(_multi(tmp_path, "a.example.com", SECOND))

    assert bench.domains == ["a.example.com", SECOND]
    with pytest.raises(BenchException, match="none is named after"):
        _ = bench.primary_domain


def test_the_bench_publishes_a_non_primary_sites_domain_and_its_aliases(tmp_path):
    """The other half: on a bench that resolves fine, composing from `primary_domain` returned
    `['shop.localhost']` and nothing else, so `b.example.com` was absent from worker `extra_hosts`
    (unreachable from a background job) and from vhostd (pinned to nginx-proxy's 1M upload default
    whatever `upload_limit` said)."""
    bench = _bench_over(_multi(tmp_path, SITE, SECOND, aliases={SECOND: ["www.b.example.com"]}))

    assert bench.domains == [SITE, SECOND, "www.b.example.com"]
    assert bench.primary_domain == SITE


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
# the only record of the schema's name and password. With N sites it is also the partial-failure
# report, so it takes the per-site outstanding list rather than one exception.

SECOND_SCHEMA = "fm_b_def456"
SCHEMA = "fm_shop_abc123"


def _write_site(bench_path, site: str, db_name: str | None = None, *, raw: str | None = None):
    """Put one site on disk at `<bench>/workspace/frappe-bench/sites/<site>/`.

    The refusal counts and names the sites `Bench.site_schemas()` enumerates. That enumeration is
    driven by `[sites]` in the config, and each schema is then read off the site's own file here:
    fm only ever destroys what it wrote down, so a site present on disk but absent from the config
    is reported by `unmanaged_site_dirs()` and never acted on. Callers therefore have to RECORD the
    site as well as write it, which `_bench_on_disk` does.
    """
    site_dir = bench_path / "workspace" / "frappe-bench" / "sites" / site
    site_dir.mkdir(parents=True, exist_ok=True)
    (site_dir / "site_config.json").write_text(raw if raw is not None else json.dumps({"db_name": db_name}))


def _bench_on_disk(tmp_path, *sites: tuple[str, str | None]) -> Bench:
    """A real `Bench` over a real sites tree, whose name DIFFERS from every site it serves."""
    bench = Bench.__new__(Bench)
    bench.name = BENCH
    bench.path = tmp_path / BENCH
    # Recorded in `[sites]` AND written to disk: the config says which sites exist, the disk says
    # what schema each one uses.
    bench.bench_config = SimpleNamespace(
        get_database_config=lambda site: None,
        sites=dict.fromkeys(site for site, _ in sites),
    )
    for site, db_name in sites:
        _write_site(bench.path, site, db_name)
    return bench


def _refusal(bench: Bench, *failed: tuple[str, str]) -> str:
    from frappe_manager.site_manager.site import orphaned_database_error

    by_site = {entry.site: entry for entry in bench.site_schemas()}
    outstanding = [(by_site[site], why) for site, why in failed]
    return orphaned_database_error(bench, outstanding).message


def test_the_refusal_hands_over_the_statements_when_the_schema_is_known(tmp_path):
    bench = _bench_on_disk(tmp_path, (SITE, SCHEMA))

    message = _refusal(bench, (SITE, "global-db refused the drop"))

    assert f"  {SITE}: global-db refused the drop" in message
    assert f"DROP DATABASE IF EXISTS `{SCHEMA}`;" in message
    assert f"DROP USER IF EXISTS '{SCHEMA}'@'%';" in message


def test_the_refusal_points_at_the_site_config_when_the_schema_is_unreadable(tmp_path):
    """The fallback names the file to read the schema out of. That file sits under the SITE
    directory, while the `fm delete` command beside it takes the BENCH: one message, both
    identities. With the two names distinct, reading the wrong one fails here."""
    bench = _bench_on_disk(tmp_path, (SITE, None))

    message = _refusal(bench, (SITE, "schema name could not be read from its site config")).replace("\\", "/")

    assert f"sites/{SITE}/site_config.json" in message
    assert f"sites/{BENCH}/site_config.json" not in message
    assert f"fm delete {BENCH} --yes --no-delete-db-from-global-db" in message


def test_the_refusal_keeps_the_bench_directory_and_says_so(tmp_path):
    """Removing it would destroy the only record of the schema, so the message must not read as
    though the bench is already gone."""
    bench = _bench_on_disk(tmp_path, (SITE, None))

    message = _refusal(bench, (SITE, "schema name could not be read from its site config"))

    assert str(tmp_path / BENCH) in message
    assert "kept at" in message


def test_a_corrupt_site_config_does_not_break_the_refusal(tmp_path):
    """The drop has already failed; a second failure while reading the config to explain it would
    replace a useful message with a traceback. Unparseable reads as unreadable."""
    bench = _bench_on_disk(tmp_path, (SITE, None))
    _write_site(bench.path, SITE, raw="{not json")

    message = _refusal(bench, (SITE, "schema name could not be read from its site config"))

    assert "site_config.json" in message
    assert "DROP DATABASE" not in message


def test_the_refusal_names_only_the_site_that_failed(tmp_path):
    """A partial failure across N sites. One site dropped cleanly and one did not, so the
    directory survives for the failed site alone: naming the other would send the operator
    hunting a schema that is already gone."""
    bench = _bench_on_disk(tmp_path, (SITE, SCHEMA), (SECOND, SECOND_SCHEMA))

    message = _refusal(bench, (SECOND, "global-db refused the drop"))

    assert "Database deletion failed for 1 of 2 site(s)." in message
    assert f"DROP DATABASE IF EXISTS `{SECOND_SCHEMA}`;" in message
    assert f"DROP USER IF EXISTS '{SECOND_SCHEMA}'@'%';" in message
    assert SCHEMA not in message
    assert SITE not in message


def test_every_site_failing_is_counted_against_the_whole_bench(tmp_path):
    """The count is outstanding against total, so the operator can tell a single stuck site from
    a global-db that refused everything."""
    bench = _bench_on_disk(tmp_path, (SITE, SCHEMA), (SECOND, SECOND_SCHEMA))

    message = _refusal(bench, (SITE, "global-db refused the drop"), (SECOND, "global-db refused the drop"))

    assert "Database deletion failed for 2 of 2 site(s)." in message
    assert f"DROP DATABASE IF EXISTS `{SCHEMA}`;" in message
    assert f"DROP DATABASE IF EXISTS `{SECOND_SCHEMA}`;" in message


# ------------------------------------------------- one certificate per served hostname
# `create_individual_certificates` had no test against the real model: the only reference was a
# MagicMock standing in for it. It built the primary entry from the BENCH name and then walked a
# bench-level alias list, so on a bench named `shop` serving `shop.localhost` it minted a
# certificate for `shop` (a hostname nothing resolves) and none for the site actually served.


def _template() -> "SSLCertificate":
    from frappe_manager.ssl_manager import SUPPORTED_SSL_TYPES
    from frappe_manager.ssl_manager.certificate import SSLCertificate

    return SSLCertificate(domain="placeholder.example.com", ssl_type=SUPPORTED_SSL_TYPES.le)


def test_a_certificate_is_minted_for_every_hostname_the_bench_serves(tmp_path):
    config = _multi(tmp_path, SITE, SECOND, aliases={SITE: ["www.shop.localhost"], SECOND: ["www.b.example.com"]})

    config.create_individual_certificates(_template())

    assert [c.domain for c in config.ssl_certificates] == config.domains


def test_the_bench_name_gets_no_certificate_when_it_is_not_a_served_hostname(tmp_path):
    """The bench is `shop` and nothing answers on `shop`; a certificate for it would be issued
    against a name that does not resolve, and the site actually served would have none."""
    config = _multi(tmp_path, SITE)

    config.create_individual_certificates(_template())

    minted = [c.domain for c in config.ssl_certificates]
    assert minted == [SITE]
    assert BENCH not in minted


def test_every_minted_certificate_copies_the_template_settings(tmp_path):
    """Only `domain` may differ between entries; sharing one object would make a later edit to one
    certificate silently change the rest."""
    config = _multi(tmp_path, SITE, SECOND)
    template = _template()

    config.create_individual_certificates(template)

    assert {c.ssl_type for c in config.ssl_certificates} == {template.ssl_type}
    assert len({id(c) for c in config.ssl_certificates}) == len(config.ssl_certificates)


def test_minting_replaces_the_previous_certificate_set(tmp_path):
    config = _multi(tmp_path, SITE)
    config.ssl_certificates = [_template()]

    config.create_individual_certificates(_template())

    assert [c.domain for c in config.ssl_certificates] == [SITE]


# ---------------------------------------------------- default_site outranks the guesses


"""`default_site` is the recorded answer, and it beats every rule that guesses from a name.

Frappe writes it (`bench use`), fm writes it when it creates a bench's first site, and frappe's
CLI reads it to resolve a bare `bench` command. Nothing in frappe's HTTP path reads it, so it means
exactly "which site is meant when none is named", which is the question `resolve_primary_site`
answers for fm. Two answers to one question in two files is how they drift.

The rules below it reconstruct fm's CREATION CONVENTION from string shapes. That is guessing, and
on a real bench it guessed wrong: a bench named `shop` recording a phantom site also named `shop`
resolved to it over `shop.localhost`, and `bench --site shop` could not open the result.
"""


def _write_default_site(bench_root, value):
    sites_dir = bench_root / "workspace" / "frappe-bench" / "sites"
    sites_dir.mkdir(parents=True, exist_ok=True)
    (sites_dir / "common_site_config.json").write_text(json.dumps({"default_site": value}))


def test_the_recorded_default_beats_a_site_named_after_the_bench(tmp_path):
    # The exact live failure: bench `shop` recording BOTH a phantom `shop` and the real
    # `shop.localhost`. Rule 1 matched the phantom; the recorded default names the one that works.
    bench = _bench(site=None)
    bench.path = tmp_path
    bench.bench_config = SimpleNamespace(
        name=BENCH,
        domains=[SITE],
        sites={
            BENCH: SimpleNamespace(database=None, alias_domains=[]),
            SITE: SimpleNamespace(database=None, alias_domains=[]),
        },
    )
    _write_default_site(tmp_path, SITE)

    assert bench.site_name == SITE


def test_without_a_recorded_default_the_name_rule_still_answers(tmp_path):
    # The fallback has to keep working: a bench created before fm wrote the key, or one whose
    # common_site_config is missing, still resolves rather than refusing.
    bench = _bench()
    bench.path = tmp_path / "no-such-dir"

    assert bench.site_name == SITE


def test_a_default_naming_an_unrecorded_site_is_not_followed(tmp_path):
    # Drift the other way: frappe names a site fm has no record of. Following it would act on a
    # schema fm never provisioned, which is the rule `fm delete` already holds to.
    bench = _bench()
    bench.path = tmp_path
    _write_default_site(tmp_path, "stranger.localhost")

    assert bench.site_name == SITE


def test_an_unreadable_common_site_config_falls_through_silently(tmp_path):
    # Read from parameter callbacks, so a hand-mangled file must not take the command down before
    # it starts.
    bench = _bench()
    bench.path = tmp_path
    sites_dir = tmp_path / "workspace" / "frappe-bench" / "sites"
    sites_dir.mkdir(parents=True)
    (sites_dir / "common_site_config.json").write_text("{not json")

    assert bench.site_name == SITE


def test_the_default_resolves_a_bench_the_name_rules_would_refuse(tmp_path):
    # Two sites, neither named after the bench: the name rules give None and every bench-scoped
    # command refuses. A recorded default is exactly the operator saying which one they meant.
    bench = _bench(site=None)
    bench.path = tmp_path
    bench.bench_config = SimpleNamespace(
        name="acme",
        domains=["a.example.com"],
        sites={
            "a.example.com": SimpleNamespace(database=None, alias_domains=[]),
            "b.example.com": SimpleNamespace(database=None, alias_domains=[]),
        },
    )
    bench.name = "acme"
    with pytest.raises(BenchException):
        _ = bench.site_name

    _write_default_site(tmp_path, "b.example.com")
    assert bench.site_name == "b.example.com"


def test_the_config_reads_the_default_from_beside_its_own_file(tmp_path):
    """`BenchConfig.primary_site` has to find `common_site_config.json` too, not just `Bench`.

    `root_path` is the bench_config.toml FILE despite its name, so the config-side caller has to
    pass its parent. Handing the file path straight through built a path under the .toml, silently
    read nothing, and fell back to the name rules: the resolver looked wired and was not. Caught on
    a real bench, not here, which is why this test exists.
    """
    bench_dir = tmp_path / BENCH
    bench_dir.mkdir()
    toml = bench_dir / "bench_config.toml"
    toml.write_text(
        f'name = "{BENCH}"\ndeveloper_mode = false\nadmin_tools = false\nenvironment = "prod"\n'
        f'\n[sites."{BENCH}"]\n\n[sites."{SITE}"]\n',
    )
    _write_default_site(bench_dir, SITE)

    config = BenchConfig.import_from_toml(toml)

    # Without the fix this is BENCH: rule 1 matches the site named after the bench.
    assert config.primary_site == SITE
