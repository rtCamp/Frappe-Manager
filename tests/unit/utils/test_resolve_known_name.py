"""One address rule for the second segment of `BENCH/<name>`, wherever it is matched.

`fm update bench/shop` worked while `fm ssl remove bench/shop` did not: the site callbacks tried
the `.localhost` form and the domain ones took the segment verbatim. Same typing, different answer.
"""

from frappe_manager.utils.site import resolve_known_name

SITES = ["shop.localhost", "blog.localhost"]


def test_a_bare_label_addresses_the_localhost_form():
    """The shorthand people actually type, which half of fm used to reject."""
    assert resolve_known_name("shop", SITES) == "shop.localhost"


def test_an_exact_name_wins_over_the_localhost_form():
    """Load-bearing: on a bench serving both, `fm delete shop/shop` resolved to `shop.localhost`
    and offered to drop ITS database. fm never creates a bare-label name, so this only arises from
    a hand-written config -- exactly when acting on the wrong one is least excusable."""
    assert resolve_known_name("shop", ["shop", *SITES]) == "shop"


def test_a_name_that_matches_nothing_resolves_to_nothing():
    """The caller reports, naming what IS served; this never invents a name."""
    assert resolve_known_name("stranger", SITES) is None
    assert resolve_known_name("stranger.example.com", SITES) is None


def test_a_dotted_name_is_never_given_a_suffix():
    """An alias is a real hostname. Appending to `sub.example.com` would address nothing, and under
    `fm ssl --standalone` it would mangle a domain belonging to no bench at all."""
    assert resolve_known_name("sub.example.com", ["sub.example.com"]) == "sub.example.com"
    assert resolve_known_name("sub.example.com", SITES) is None
