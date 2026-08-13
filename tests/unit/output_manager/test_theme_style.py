"""Theme (colors) + style (layout) contract for fm's output architecture.

Defends: token resolution, env-over-config precedence, loud failure on
unknown names/invalid styles, the mono theme's no-color guarantee (color-
blind safety), idempotent theme application (re-applying replaces the
pushed theme instead of stacking a second one), and that the Card
component renders under every style with style-appropriate separation.
"""

from unittest.mock import MagicMock, patch

import pytest
from rich.console import Console
from rich.errors import StyleSyntaxError
from rich.style import Style

from frappe_manager.output_manager import theme as theme_module
from frappe_manager.output_manager.railcard import Card, bench_meta, cards, status_dot
from frappe_manager.output_manager.style import STYLES, get_output_style, set_output_style
from frappe_manager.output_manager.theme import DEFAULT_TOKENS, THEMES, build_theme


@pytest.fixture(autouse=True)
def _reset(monkeypatch):
    monkeypatch.delenv("FM_THEME", raising=False)
    monkeypatch.delenv("FM_STYLE", raising=False)
    set_output_style("rail")
    yield
    set_output_style("rail")


# ---------------------------------------------------------------- theme


def test_default_theme_resolves_every_token():
    theme = build_theme()
    for token in DEFAULT_TOKENS:
        assert token in theme.styles


def test_env_theme_wins_over_config(monkeypatch):
    monkeypatch.setenv("FM_THEME", "mono")
    theme = build_theme("high-contrast")
    color = theme.styles["fm.status.running"].color
    assert color is None or color.name == "default"  # mono won


def test_unknown_theme_fails_loudly():
    with pytest.raises(ValueError, match="Unknown output theme"):
        build_theme("solarized-disco")


def test_color_override_wins_and_invalid_fails():
    theme = build_theme("default", {"fm.env.prod": "bold magenta"})
    assert theme.styles["fm.env.prod"] == Style.parse("bold magenta")
    with pytest.raises(StyleSyntaxError):
        build_theme("default", {"fm.env.prod": "not-a-style-###"})


def test_mono_theme_has_no_colors_anywhere():
    # Color-blind safety: mono keeps emphasis, never color.
    for value in THEMES["mono"].values():
        parsed = Style.parse(value)
        assert parsed.color is None or parsed.color.name == "default"
        assert parsed.bgcolor is None


# ------------------------------------------------- theme application (consoles)


def _apply_with_fake_consoles(applies: int):
    """Run apply_output_theme() N times against two fake consoles."""
    stdout, stderr = MagicMock(name="stdout"), MagicMock(name="stderr")
    with (
        patch.object(theme_module, "_pushed", new=False),
        patch(
            "frappe_manager.output_manager.console_singleton.get_stdout_console",
            return_value=stdout,
        ),
        patch(
            "frappe_manager.output_manager.console_singleton.get_stderr_console",
            return_value=stderr,
        ),
    ):
        for _ in range(applies):
            theme_module.apply_output_theme("default")
    return stdout, stderr


def test_first_theme_apply_pushes_without_popping():
    for console in _apply_with_fake_consoles(1):
        console.pop_theme.assert_not_called()  # nothing of ours on the stack yet
        assert console.push_theme.call_count == 1


def test_reapplying_theme_replaces_instead_of_stacking():
    # `fm` applies the default theme at bootstrap, then re-applies the configured
    # one once fm_config is read. Without remembering the first push, every
    # re-apply would bury another theme on the console stack.
    for console in _apply_with_fake_consoles(3):
        assert console.push_theme.call_count == 3
        assert console.pop_theme.call_count == 2  # one pop per re-apply


# ---------------------------------------------------------------- style


def test_unknown_style_fails_loudly():
    with pytest.raises(ValueError, match="Unknown output style"):
        set_output_style("hologram")


def test_env_style_wins(monkeypatch):
    monkeypatch.setenv("FM_STYLE", "ascii")
    set_output_style("box")
    assert get_output_style().name == "ascii"


def test_ascii_style_is_seven_bit_clean():
    ascii_style = STYLES["ascii"]
    for glyph in (ascii_style.rail_active, ascii_style.rail_inactive, ascii_style.dot_ok, ascii_style.dot_bad):
        assert glyph.isascii()


# ---------------------------------------------------------------- card component


def _render(card_or_group) -> str:
    console = Console(theme=build_theme(), width=120, force_terminal=False, legacy_windows=False)
    with console.capture() as cap:
        console.print(card_or_group)
    return cap.get()


def _sample_card(active=True) -> Card:
    card = Card("bench.example", bench_meta(active, "image", "prod", "always"), active, link="http://bench.example")
    card.fact("apps", "frappe, erpnext")
    card.section("access")
    card.fact("db", "user / pass")
    return card


@pytest.mark.parametrize("style_name", sorted(STYLES))
def test_card_renders_under_every_style(style_name):
    set_output_style(style_name)
    out = _render(_sample_card())
    # Content survives regardless of layout.
    assert "bench.example" in out
    assert "running" in out  # status carried by TEXT in every style/theme
    assert "frappe, erpnext" in out
    assert "access" in out


def test_box_style_draws_a_panel():
    set_output_style("box")
    assert "╭" in _render(_sample_card())


def test_rail_style_marks_inactive_with_light_rail():
    set_output_style("rail")
    out = _render(_sample_card(active=False))
    assert "│" in out
    assert "stopped" in out


def test_rail_cards_are_separated_by_exactly_one_blank_line():
    set_output_style("rail")
    lines = _render(cards([_sample_card(), _sample_card()])).splitlines()
    assert sum(1 for line in lines if not line.strip()) == 1  # the separator, nothing else
    headlines = [i for i, line in enumerate(lines) if "bench.example" in line]
    blank = next(i for i, line in enumerate(lines) if not line.strip())
    assert len(headlines) == 2
    assert headlines[0] < blank < headlines[1]  # between the cards, not around them
    # A lone card gets no leading separator.
    assert all(line.strip() for line in _render(cards([_sample_card()])).splitlines())


def test_box_cards_get_no_separator_because_panels_separate_themselves():
    set_output_style("box")
    lines = _render(cards([_sample_card(), _sample_card()])).splitlines()
    assert [line for line in lines if not line.strip()] == []
    assert sum(1 for line in lines if "╭" in line) == 2


def test_status_dot_carries_state_as_text_when_not_running():
    set_output_style("rail")
    assert "exited:" in status_dot("exited")
    assert "●" in status_dot("running")
