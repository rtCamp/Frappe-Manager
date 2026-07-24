"""Output STYLE profiles: the shape of fm's rendered views.

Orthogonal to the color theme (``output_manager.theme``): the theme decides
what semantic tokens look like; the style decides the LAYOUT and glyphs a
view renders with (rail cards, boxed panels, flat indentation, pure ascii).

Selection: ``FM_STYLE`` env var wins over ``fm_config.toml [output] style``
wins over "rail". Views build ``railcard.Card`` objects and never branch on
the style themselves.
"""

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class OutputStyle:
    name: str
    card: str  # "rail" | "box" | "flat"
    rail_active: str
    rail_inactive: str
    dot_ok: str
    dot_bad: str
    label_width: int = 9


STYLES: dict[str, OutputStyle] = {
    # Status-colored left rail (charm school) -- the default.
    "rail": OutputStyle(name="rail", card="rail", rail_active="┃", rail_inactive="│", dot_ok="●", dot_bad="●"),
    # Rounded bordered panel per card.
    "box": OutputStyle(name="box", card="box", rail_active="┃", rail_inactive="│", dot_ok="●", dot_bad="●"),
    # No rail/border; indentation only.
    "flat": OutputStyle(name="flat", card="flat", rail_active="", rail_inactive="", dot_ok="●", dot_bad="●"),
    # 7-bit terminals / logs: ascii glyphs on the rail layout.
    "ascii": OutputStyle(name="ascii", card="rail", rail_active="|", rail_inactive="|", dot_ok="*", dot_bad="x"),
}

_active: OutputStyle | None = None


def set_output_style(name: str | None = None) -> None:
    """Resolve and pin the process-wide style (env > config > default)."""
    global _active  # noqa: PLW0603 -- process-wide render mode, set at bootstrap

    resolved = os.environ.get("FM_STYLE") or name or "rail"
    if resolved not in STYLES:
        raise ValueError(f"Unknown output style '{resolved}'. Available: {', '.join(sorted(STYLES))}.")
    _active = STYLES[resolved]


def get_output_style() -> OutputStyle:
    if _active is None:
        set_output_style()
    assert _active is not None
    return _active
