"""Shell completion for the `BENCH[/SITE]` positional address.

A bench serves several sites now, so the address the operator types has two halves and the
shell has to be able to fill in either one. `bench_site_autocompletion_callback` is what
answers TAB: bench names before the separator, that bench's own sites after it.

Two properties matter more than the suggestions themselves, and both are asserted here rather
than assumed:

* It NEVER raises. This runs on every TAB, in the middle of a half-typed command line, against
  whatever is on disk -- including a bench that does not exist and a `bench_config.toml` that
  will not parse. A traceback there is worse than no suggestion.
* It reads only files. No Docker, no network, no `Bench` object: the config file and the sites
  directory, and nothing else.

The bench here is called `shop` and its sites are `shop.localhost` and `b.example.com`. The
bench name and the site names DIFFER on purpose: a fixture that gives one string to both roles
cannot catch code that reaches for a site under the bench's name.
"""

from types import SimpleNamespace

import pytest

from frappe_manager.utils import callbacks
from frappe_manager.utils.callbacks import bench_site_autocompletion_callback

BENCH = "shop"
PRIMARY_SITE = "shop.localhost"
SECOND_SITE = "b.example.com"


@pytest.fixture
def benches(tmp_path, monkeypatch):
    """An isolated benches directory; nothing here touches ~/frappe."""
    benches_dir = tmp_path / "sites"
    benches_dir.mkdir()
    monkeypatch.setattr(callbacks, "CLI_BENCHES_DIRECTORY", benches_dir)
    return benches_dir


def make_bench(benches_dir, name: str):
    """A directory the completer counts as a bench: it has a `bench_config.toml`.

    The config is the registry marker, so a directory without one is not offered at all. The
    compose file goes in too because a real bench has both, and no test should depend on a shape
    that never occurs on disk.
    """
    bench = benches_dir / name
    bench.mkdir()
    (bench / "docker-compose.yml").write_text("services: {}\n")
    (bench / "bench_config.toml").write_text(f'name = "{name}"\n')
    return bench


def record_sites(bench, *sites: str, aliases: dict[str, list[str]] | None = None) -> None:
    """Write the bench's own `bench_config.toml`, recording `sites` under `[sites]`.

    `aliases` maps a site to its extra hostnames, which is what tells the DOMAIN completion apart
    from the SITE completion: an alias is a served hostname and is not a site name.
    """
    aliases = aliases or {}
    body = f'name = "{bench.name}"\ndeveloper_mode = false\nadmin_tools = false\nenvironment = "prod"\n'
    for site in sites:
        body += f'\n[sites."{site}"]\n'
        if aliases.get(site):
            listed = ", ".join(f'"{a}"' for a in aliases[site])
            body += f"alias_domains = [{listed}]\n"
    (bench / "bench_config.toml").write_text(body)


def make_site_dir(bench, site: str) -> None:
    """A site as it exists on disk: a directory under the workspace holding a site_config.json."""
    site_dir = bench / "workspace" / "frappe-bench" / "sites" / site
    site_dir.mkdir(parents=True)
    (site_dir / "site_config.json").write_text("{}")


@pytest.fixture
def shop(benches):
    """The bench `shop`, recording two sites and carrying both on disk."""
    bench = make_bench(benches, BENCH)
    record_sites(bench, PRIMARY_SITE, SECOND_SITE)
    make_site_dir(bench, PRIMARY_SITE)
    make_site_dir(bench, SECOND_SITE)
    return bench


class TestBeforeTheSeparator:
    """No `/` typed yet: the operator is still naming a bench, so offer bench names."""

    def test_nothing_typed_offers_every_bench(self, benches):
        make_bench(benches, BENCH)
        make_bench(benches, "warehouse")

        assert bench_site_autocompletion_callback("") == [BENCH, "warehouse"]

    def test_a_prefix_offers_only_the_benches_that_start_with_it(self, benches):
        make_bench(benches, BENCH)
        make_bench(benches, "warehouse")

        assert bench_site_autocompletion_callback("sh") == [BENCH]

    def test_a_directory_without_a_config_is_not_a_bench(self, benches):
        # The config is the registry marker, and this is the shape that tells it apart from the
        # compose file: a bench torn down mid-delete, or a hand-made directory, has one and not
        # the other. Offering it completes the operator into a command that cannot run.
        make_bench(benches, BENCH)
        halfbaked = benches / "halfbaked"
        halfbaked.mkdir()
        (halfbaked / "docker-compose.yml").write_text("services: {}\n")

        assert bench_site_autocompletion_callback("") == [BENCH]

    def test_the_reserved_word_is_offered_where_it_means_something(self, benches):
        # `all` completes for the commands that accept it, and does NOT leak into the ones that
        # do not: a word the shell suggests and the command refuses is worse than no suggestion.
        make_bench(benches, BENCH)

        assert callbacks.bench_all_autocompletion_callback("") == [BENCH, "all"]
        assert callbacks.bench_all_autocompletion_callback("a") == ["all"]
        assert bench_site_autocompletion_callback("") == [BENCH]

    def test_a_bench_name_is_never_offered_with_a_site_appended(self, shop):
        # Before the separator the operator has not asked for a site yet, and completing
        # straight to `shop/shop.localhost` would take the choice away.
        assert bench_site_autocompletion_callback("sho") == [BENCH]


class TestAfterTheSeparator:
    """`shop/` typed: the bench is named, so the offer switches to that bench's sites."""

    def test_the_separator_alone_offers_every_site_of_that_bench(self, shop):
        assert bench_site_autocompletion_callback("shop/") == [
            f"{BENCH}/{PRIMARY_SITE}",
            f"{BENCH}/{SECOND_SITE}",
        ]

    def test_a_site_prefix_narrows_to_the_matching_sites(self, shop):
        assert bench_site_autocompletion_callback("shop/b") == [f"{BENCH}/{SECOND_SITE}"]

    def test_every_suggestion_is_the_whole_address(self, shop):
        # A completion REPLACES the word being completed, so a bare `b.example.com` would turn
        # `shop/b` into `b.example.com` and lose the bench.
        for suggestion in bench_site_autocompletion_callback("shop/"):
            assert suggestion.startswith(f"{BENCH}/")

    def test_a_prefix_matching_no_site_offers_nothing(self, shop):
        assert bench_site_autocompletion_callback("shop/zzz") == []

    def test_the_primary_site_is_offered_first(self, shop):
        # `site_names` puts the bench's own site first, and completion order is what the shell
        # shows first. The alphabetical order would bury it under `b.example.com`.
        assert bench_site_autocompletion_callback("shop/")[0] == f"{BENCH}/{PRIMARY_SITE}"


class TestWhereTheSitesComeFrom:
    def test_the_record_is_what_is_offered(self, benches):
        # `[sites]` is the bench's own answer to what it serves, so a site recorded there is
        # completable even before anything of it exists on disk.
        bench = make_bench(benches, BENCH)
        record_sites(bench, PRIMARY_SITE, SECOND_SITE)

        assert bench_site_autocompletion_callback("shop/") == [
            f"{BENCH}/{PRIMARY_SITE}",
            f"{BENCH}/{SECOND_SITE}",
        ]

    def test_a_bench_with_no_config_falls_back_to_the_sites_on_disk(self, benches):
        # A bench mid-create, before its config is written. Distinct from the test below, where
        # the config exists and records nothing: this one has no file to read at all.
        bench = make_bench(benches, BENCH)
        (bench / "bench_config.toml").unlink()
        make_site_dir(bench, PRIMARY_SITE)
        make_site_dir(bench, SECOND_SITE)

        assert bench_site_autocompletion_callback("shop/") == [
            f"{BENCH}/{SECOND_SITE}",
            f"{BENCH}/{PRIMARY_SITE}",
        ]

    def test_a_config_recording_no_sites_falls_back_to_the_sites_on_disk(self, benches):
        # `site_names` answers `[<bench name>]` for a bench that records nothing, which is a
        # bench name and not a site. Offering `shop/shop` would be an address nothing accepts.
        bench = make_bench(benches, BENCH)
        record_sites(bench)
        make_site_dir(bench, SECOND_SITE)

        assert bench_site_autocompletion_callback("shop/") == [f"{BENCH}/{SECOND_SITE}"]

    def test_an_unreadable_config_falls_back_to_the_sites_on_disk(self, benches):
        bench = make_bench(benches, BENCH)
        (bench / "bench_config.toml").write_text("this is not = = toml\n")
        make_site_dir(bench, SECOND_SITE)

        assert bench_site_autocompletion_callback("shop/") == [f"{BENCH}/{SECOND_SITE}"]

    def test_bench_furniture_is_not_mistaken_for_a_site(self, benches):
        # The sites directory also holds `assets`, `apps.txt` and friends. A site is a directory
        # with a site_config.json in it, and nothing else counts.
        bench = make_bench(benches, BENCH)
        make_site_dir(bench, SECOND_SITE)
        sites_dir = bench / "workspace" / "frappe-bench" / "sites"
        (sites_dir / "assets").mkdir()
        (sites_dir / "apps.txt").write_text("frappe\n")

        assert bench_site_autocompletion_callback("shop/") == [f"{BENCH}/{SECOND_SITE}"]


class TestItNeverRaises:
    """Completion runs on every TAB. Knowing nothing is fine; a traceback is not."""

    def test_an_unreadable_config_and_an_empty_disk_offer_nothing(self, benches):
        bench = make_bench(benches, BENCH)
        (bench / "bench_config.toml").write_text("this is not = = toml\n")

        assert bench_site_autocompletion_callback("shop/") == []
        assert bench_site_autocompletion_callback("shop/b") == []

    def test_a_bench_that_does_not_exist_offers_nothing(self, benches):
        assert bench_site_autocompletion_callback("ghost/") == []
        assert bench_site_autocompletion_callback("ghost/b") == []

    def test_a_missing_benches_directory_offers_nothing(self, tmp_path, monkeypatch):
        # A fresh install, before anything has been created.
        monkeypatch.setattr(callbacks, "CLI_BENCHES_DIRECTORY", tmp_path / "absent")

        assert bench_site_autocompletion_callback("") == []
        assert bench_site_autocompletion_callback("shop/") == []

    def test_an_address_the_parser_would_refuse_offers_nothing(self, shop):
        # `/site`, `shop/a/b` and `` are all things the address parser raises on. The completer
        # does not parse, it splits, so they are simply words nothing matches.
        assert bench_site_autocompletion_callback("/b.example.com") == []
        assert bench_site_autocompletion_callback("shop/a/b") == []

    def test_a_none_incomplete_is_treated_as_nothing_typed(self, benches):
        # typer types the incomplete value as `Optional[str]` on its way in.
        make_bench(benches, BENCH)

        assert bench_site_autocompletion_callback(None) == [BENCH]


class TestTheDomainHalf:
    """`BENCH/DOMAIN` for the `ssl` commands, whose second segment is a served HOSTNAME.

    Kept apart from the site tests on purpose: these two completions answer different questions,
    and the whole reason the domain one exists is that a certificate is keyed by hostname, so a
    bench's aliases are addressable for `ssl` and are not addressable for `shell` or `delete`.
    """

    def test_an_alias_completes_for_a_certificate_and_not_for_a_site(self, benches):
        bench = make_bench(benches, BENCH)
        record_sites(bench, PRIMARY_SITE, aliases={PRIMARY_SITE: ["www.shop.example.com"]})

        assert callbacks.bench_domain_autocompletion_callback("shop/") == [
            f"{BENCH}/{PRIMARY_SITE}",
            f"{BENCH}/www.shop.example.com",
        ]
        # The same bench, the same prefix, through the site completion: the alias is absent,
        # because no site is named `www.shop.example.com` and no schema answers to it.
        assert bench_site_autocompletion_callback("shop/") == [f"{BENCH}/{PRIMARY_SITE}"]

    def test_a_prefix_matching_only_an_alias_still_completes(self, benches):
        # The case that fails if the domain half quietly falls back to the site list: `www` names
        # nothing at all among the sites.
        bench = make_bench(benches, BENCH)
        record_sites(bench, PRIMARY_SITE, aliases={PRIMARY_SITE: ["www.shop.example.com"]})

        assert callbacks.bench_domain_autocompletion_callback("shop/www") == [
            f"{BENCH}/www.shop.example.com"
        ]
        assert bench_site_autocompletion_callback("shop/www") == []

    def test_each_site_carries_its_own_aliases(self, benches):
        # Ordering is per site, name then that site's aliases, so the operator reads the list as
        # the grouping it is rather than as one flat pile of hostnames.
        bench = make_bench(benches, BENCH)
        record_sites(
            bench,
            PRIMARY_SITE,
            SECOND_SITE,
            aliases={PRIMARY_SITE: ["www.shop.example.com"], SECOND_SITE: ["www.b.example.com"]},
        )

        assert callbacks.bench_domain_autocompletion_callback("shop/") == [
            f"{BENCH}/{PRIMARY_SITE}",
            f"{BENCH}/www.shop.example.com",
            f"{BENCH}/{SECOND_SITE}",
            f"{BENCH}/www.b.example.com",
        ]

    def test_before_the_separator_it_is_still_just_bench_names(self, benches):
        make_bench(benches, BENCH)
        make_bench(benches, "warehouse")

        assert callbacks.bench_domain_autocompletion_callback("") == [BENCH, "warehouse"]


class TestTheRegistryOrder:
    def test_benches_are_offered_in_a_stable_order(self, benches, monkeypatch):
        # `iterdir` is filesystem order, which is arbitrary and differs between machines. Every
        # consumer of this list is either a completion an operator reads or the sequence `all`
        # acts in, and both need the same answer twice in a row.
        for name in ("warehouse", "shop", "atelier"):
            make_bench(benches, name)

        # Handed back deliberately out of order, because most filesystems happen to return
        # `iterdir` sorted and an assertion on real disk cannot tell a sort from luck.
        shuffled = SimpleNamespace(iterdir=lambda: sorted(benches.iterdir(), reverse=True))
        monkeypatch.setattr(callbacks, "CLI_BENCHES_DIRECTORY", shuffled)

        assert callbacks._bench_names() == ["atelier", "shop", "warehouse"]
        assert callbacks.resolve_bench_targets("all") == ["atelier", "shop", "warehouse"]
