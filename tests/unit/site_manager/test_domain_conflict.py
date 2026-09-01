"""Two benches must never claim the same hostname.

nginx routes by Host, so a domain served by two benches resolves to whichever container the proxy
happens to pick. This check is what stops that at create/update time, and it is only as good as the
map it builds: a domain absent from the map is a domain nobody guards.

That was the bug these tests exist for. The map used to be the bench NAME plus a bench-level alias
list, so on a bench serving several sites every non-primary site's own domain was missing from it
entirely, and a second bench could claim `b.example.com` with no conflict reported. It is now built
from `get_site_mappings()`, the same table the nginx entrypoint routes by, so the check and the
routing agree by construction.
"""

from pathlib import Path

import pytest

from frappe_manager.site_manager.domain_conflict import (
    DomainConflictError,
    check_domain_conflicts,
    validate_domains_unique,
)

_BASE = 'name = "{name}"\ndeveloper_mode = false\nadmin_tools = false\nenvironment = "prod"\n'


def _bench(root: Path, name: str, sites: dict[str, list[str]] | None = None) -> None:
    """Write a bench on disk with `sites` mapping each site name to its own aliases."""
    directory = root / name
    directory.mkdir(parents=True, exist_ok=True)
    toml = _BASE.format(name=name)
    for site, aliases in (sites or {}).items():
        toml += f'\n[sites."{site}"]\n'
        if aliases:
            rendered = ", ".join(f'"{alias}"' for alias in aliases)
            toml += f"alias_domains = [{rendered}]\n"
    (directory / "bench_config.toml").write_text(toml)


# --------------------------------------------------------------------------- nothing to clash with


def test_an_empty_benches_root_conflicts_with_nothing(tmp_path):
    assert check_domain_conflicts(["example.com"], benches_root=tmp_path) == []


def test_a_domain_no_bench_serves_is_free(tmp_path):
    _bench(tmp_path, "shop", {"shop.localhost": ["www.shop.example.com"]})

    assert check_domain_conflicts(["unrelated.example.com"], benches_root=tmp_path) == []


def test_skip_check_allows_a_domain_that_would_otherwise_clash(tmp_path):
    _bench(tmp_path, "shop", {"shop.localhost": []})

    validate_domains_unique(["shop.localhost"], benches_root=tmp_path, skip_check=True)


# --------------------------------------------------------------------------- real clashes


def test_a_sites_own_domain_is_claimed(tmp_path):
    _bench(tmp_path, "shop", {"shop.localhost": []})

    conflicts = check_domain_conflicts(["shop.localhost"], benches_root=tmp_path)

    assert [c.domain for c in conflicts] == ["shop.localhost"]
    assert conflicts[0].owner_bench == "shop"
    assert conflicts[0].owner_site == "shop.localhost"
    assert conflicts[0].is_site_name


def test_an_alias_is_claimed_and_names_the_site_it_serves(tmp_path):
    """The owning SITE, not just the bench: a bench can serve several, so "which bench has it" no
    longer locates the clash."""
    _bench(tmp_path, "shop", {"shop.localhost": ["www.shop.example.com"]})

    conflicts = check_domain_conflicts(["www.shop.example.com"], benches_root=tmp_path)

    assert conflicts[0].owner_site == "shop.localhost"
    assert not conflicts[0].is_site_name
    assert "an alias of site 'shop.localhost'" in str(conflicts[0])


def test_a_non_primary_sites_own_domain_is_claimed_too(tmp_path):
    """The regression this file was rewritten for. `b.example.com` is the bench's SECOND site, so
    the old map (bench name plus bench-level aliases) never registered it and another bench could
    take it silently."""
    _bench(tmp_path, "shop", {"shop.localhost": [], "b.example.com": []})

    conflicts = check_domain_conflicts(["b.example.com"], benches_root=tmp_path)

    assert [c.domain for c in conflicts] == ["b.example.com"]
    assert conflicts[0].owner_site == "b.example.com"


def test_a_non_primary_sites_alias_is_claimed_and_attributed_to_that_site(tmp_path):
    """Attribution matters as much as detection: the operator has to know which site to look at."""
    _bench(tmp_path, "shop", {"shop.localhost": ["www.shop.localhost"], "b.example.com": ["www.b.example.com"]})

    conflicts = check_domain_conflicts(["www.b.example.com"], benches_root=tmp_path)

    assert conflicts[0].owner_site == "b.example.com"
    assert "site 'b.example.com'" in str(conflicts[0])


def test_every_clashing_domain_is_reported_not_just_the_first(tmp_path):
    _bench(tmp_path, "shop", {"shop.localhost": ["www.shop.localhost"], "b.example.com": []})

    conflicts = check_domain_conflicts(
        ["shop.localhost", "www.shop.localhost", "b.example.com", "free.example.com"],
        benches_root=tmp_path,
    )

    assert sorted(c.domain for c in conflicts) == ["b.example.com", "shop.localhost", "www.shop.localhost"]


# --------------------------------------------------------------------------- matching rules


def test_matching_ignores_case(tmp_path):
    """Hostnames are case-insensitive, so a bench holding `shop.localhost` must block `SHOP.LOCALHOST`.
    This replaced a test that asserted against a dict literal it built itself and never reached
    production code at all."""
    _bench(tmp_path, "shop", {"shop.localhost": ["www.shop.example.com"]})

    assert check_domain_conflicts(["SHOP.LOCALHOST"], benches_root=tmp_path)
    assert check_domain_conflicts(["WWW.Shop.Example.COM"], benches_root=tmp_path)


def test_a_bench_does_not_conflict_with_itself(tmp_path):
    """`fm update` re-validates the domains a bench already serves, so without the exclusion every
    such run would refuse its own hostnames."""
    _bench(tmp_path, "shop", {"shop.localhost": ["www.shop.example.com"]})

    assert check_domain_conflicts(
        ["shop.localhost", "www.shop.example.com"], benches_root=tmp_path, exclude_bench="shop"
    ) == []


def test_a_directory_with_no_config_is_skipped(tmp_path):
    (tmp_path / "half-made").mkdir()

    assert check_domain_conflicts(["anything.example.com"], benches_root=tmp_path) == []


def test_an_unreadable_config_does_not_hide_the_other_benches(tmp_path):
    """One broken bench must not turn the whole check into a silent pass."""
    _bench(tmp_path, "shop", {"shop.localhost": []})
    broken = tmp_path / "broken"
    broken.mkdir()
    (broken / "bench_config.toml").write_text("this is not = valid = toml")

    assert check_domain_conflicts(["shop.localhost"], benches_root=tmp_path)


# --------------------------------------------------------------------------- the raising wrapper


def test_validate_passes_when_nothing_clashes(tmp_path):
    _bench(tmp_path, "shop", {"shop.localhost": []})

    validate_domains_unique(["free.example.com"], benches_root=tmp_path)


def test_validate_raises_and_the_message_names_every_clash(tmp_path):
    _bench(tmp_path, "shop", {"shop.localhost": ["www.shop.localhost"]})

    with pytest.raises(DomainConflictError) as exc:
        validate_domains_unique(["shop.localhost", "www.shop.localhost"], benches_root=tmp_path)

    message = str(exc.value)
    assert "shop.localhost" in message
    assert "www.shop.localhost" in message
    assert "the domain of site" in message
    assert "an alias of site" in message
