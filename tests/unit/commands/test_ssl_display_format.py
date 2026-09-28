"""A certificate row carries one expiry; the card and `--json` spell it differently.

The row is ISO 8601 because `--json` consumers parse it. A card is read by a person, and
`2026-11-15T12:00:37+00:00` is the machine's spelling of the same instant.
"""

import pytest

from frappe_manager.commands.ssl.helpers import cert_expiry_words


def test_an_iso_expiry_is_shown_to_a_person_as_a_date_and_time():
    assert cert_expiry_words("2026-11-15T12:00:37+00:00") == "2026-11-15 12:00"


def test_an_expiry_without_a_timezone_still_reads_as_a_date():
    assert cert_expiry_words("2026-11-15T12:00:37") == "2026-11-15 12:00"


@pytest.mark.parametrize("value", ["", "soon", "15/11/2026"])
def test_an_unparseable_expiry_is_shown_as_written_rather_than_dropped(value):
    # A date fm cannot read is still the only expiry it has; hiding it would be worse than
    # showing it in whatever form it arrived.
    assert cert_expiry_words(value) == value
