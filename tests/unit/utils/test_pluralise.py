"""`pluralise` adds the "s" for every count except exactly one.

It is the wording of every certificate-expiry line fm prints ("29 days 4 hours 7 mins"),
so the singular/plural decision is user-visible text, not cosmetics. The interesting part
is the boundary: 1 is the only count that stays singular -- 0 and negative counts (an
already expired certificate) are plural.
"""

from frappe_manager.utils.helpers import pluralise


class TestPluralise:
    def test_exactly_one_is_singular(self):
        assert pluralise("day", 1) == "1 day"

    def test_two_is_plural(self):
        assert pluralise("day", 2) == "2 days"

    def test_zero_is_plural(self):
        assert pluralise("hour", 0) == "0 hours"

    def test_negative_count_is_plural(self):
        """An expired certificate reports negative days; it must not read "-1 day"."""
        assert pluralise("day", -1) == "-1 days"

    def test_singular_word_is_used_verbatim(self):
        assert pluralise("min", 1) == "1 min"
