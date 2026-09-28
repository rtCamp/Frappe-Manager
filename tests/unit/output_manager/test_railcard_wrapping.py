"""A card's rail must survive wrapping.

The rail used to be concatenated onto a plain string handed to rich, which wraps with no knowledge
that the first cells are a gutter, so every continuation row started at column 0 and fell out of
the card. 80 columns is the default terminal width, so this was the common case.
"""

from rich.console import Console

from frappe_manager.output_manager.railcard import Card

DIGEST = "127.0.0.1:5000/acmeapp@sha256:a65f4eb59910b6462cb0ea271811721815de020b1f8641e9c6d7fc35448c26b3"
SERVICES = "● frappe   ● nginx   ● redis-cache   ● redis-queue   ● schedule   ● socketio"


def _render(card: Card, width: int) -> list[str]:
    console = Console(width=width, force_terminal=False, no_color=True)
    with console.capture() as captured:
        console.print(card.render())
    return [line for line in captured.get().splitlines() if line.strip()]


def _fact_rows(lines: list[str]) -> list[str]:
    """Every line below the headline, which is the only line without a rail by design."""
    return lines[1:]


def test_a_value_too_long_for_the_terminal_keeps_its_rail_on_every_row():
    card = Card("bench", "running").fact("bench", SERVICES)

    rows = _fact_rows(_render(card, 80))

    assert len(rows) > 1, "the value must actually have wrapped for this test to mean anything"
    assert all(row.startswith("┃") for row in rows)


def test_an_unbreakable_digest_folds_inside_the_card_instead_of_escaping_it():
    # An image reference carrying a sha256 digest is one ~100 character word with nowhere to break.
    # Folding keeps it in the card; truncating would corrupt a value written to be copied.
    card = Card("bench", "running").fact("image", DIGEST)

    rows = _fact_rows(_render(card, 100))

    assert len(rows) > 1
    assert all(row.startswith("┃") for row in rows)
    rebuilt = "".join(row.lstrip("┃ ").removeprefix("image").strip() for row in rows)
    assert rebuilt == DIGEST, "folding must not lose or truncate any character of the digest"

def test_a_wrapped_value_hangs_under_the_value_not_the_label():
    card = Card("bench", "running").fact("bench", SERVICES)

    rows = _fact_rows(_render(card, 80))

    label_column = rows[0].index("bench", 1)
    value_column = rows[0].index("●")
    assert rows[1].index("●") == value_column, "a continuation must line up under the value"
    assert rows[1][label_column:value_column].strip() == "", "the label column stays empty"


def test_nothing_wraps_when_the_terminal_is_wide_enough():
    card = Card("bench", "running").fact("url", "http://bench.localhost")

    rows = _fact_rows(_render(card, 120))

    assert len(rows) == 1


def test_a_stopped_card_keeps_its_own_rail_glyph_on_continuations():
    # The inactive rail is a different glyph; a continuation must not silently borrow the active one.
    card = Card("bench", "stopped", active=False).fact("bench", SERVICES)

    rows = _fact_rows(_render(card, 80))

    assert len(rows) > 1
    assert len({row[0] for row in rows}) == 1
