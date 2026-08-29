"""`parse_address` is the one place a `BENCH[/SITE]` positional is split.

It is deliberately pure: no output handler, no filesystem, no `validate_sitename`
(which reports through the global handler). These tests construct nothing, which is
the property that keeps the parser cheap to reason about and is worth defending --
if a fixture ever becomes necessary here, purity has been lost.

The messages are asserted, not just the exception type. They are what the operator
reads, and the CLI layer passes them through verbatim as `typer.BadParameter`.
"""

import pytest

from frappe_manager.utils.address import Address, parse_address


def test_a_bare_name_is_a_bench_with_no_site():
    assert parse_address("shop") == Address("shop", None)


def test_a_slash_splits_bench_from_site():
    assert parse_address("shop/a.localhost") == Address("shop", "a.localhost")


def test_the_site_half_keeps_its_dots():
    """The site is a domain; splitting must not touch it beyond the first separator."""
    assert parse_address("shop/a.b.example.com").site == "a.b.example.com"


def test_an_empty_address_is_refused():
    with pytest.raises(ValueError, match="an address cannot be empty"):
        parse_address("")


def test_a_trailing_slash_is_refused_and_names_both_valid_forms():
    with pytest.raises(ValueError, match="has an empty site: write BENCH/SITE or just BENCH"):
        parse_address("shop/")


def test_a_leading_slash_is_refused():
    with pytest.raises(ValueError, match="has an empty bench: write BENCH/SITE"):
        parse_address("/a.localhost")


def test_more_than_one_separator_is_refused():
    with pytest.raises(ValueError, match="has more than one '/'"):
        parse_address("a/b/c")


def test_a_lone_separator_is_refused_as_an_empty_bench():
    """`/` has both halves empty; the bench is reported because it is read first."""
    with pytest.raises(ValueError, match="has an empty bench"):
        parse_address("/")


def test_the_refusal_names_the_offending_input():
    """The operator needs to see what they typed, not a generic complaint."""
    with pytest.raises(ValueError, match="'shop/a/b'"):
        parse_address("shop/a/b")


def test_the_address_is_frozen():
    """It is passed around as a value; a caller must not be able to rewrite the target."""
    address = parse_address("shop/a.localhost")
    with pytest.raises(Exception, match=r"(?i)frozen|cannot assign"):
        address.bench = "other"  # type: ignore[misc]
