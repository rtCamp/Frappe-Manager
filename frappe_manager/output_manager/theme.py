"""Semantic style tokens for fm's terminal output.

Code declares WHAT something is (``fm.status.running``); the active theme
decides HOW it looks. Change the look in ONE place instead of editing
markup at call sites:

- ``fm_config.toml``::

    [output]
    theme = "mono"

    [output.styles]
    "fm.env.prod" = "bold magenta"

- environment: ``FM_THEME=mono fm list`` (wins over config)
- ``NO_COLOR`` is honored by rich automatically.

Rules for call sites: use tokens (``[fm.status.running]running[/]``), never
raw colors; state MUST also be carried by text/shape (color-blind safety) --
the ``mono`` theme strips all color while keeping emphasis, and everything
must stay readable under it.
"""

import os

from rich.style import Style
from rich.theme import Theme

# ---------------------------------------------------------------- tokens

DEFAULT_TOKENS: dict[str, str] = {
    # intents
    "fm.ok": "green",
    "fm.error": "red",
    "fm.warn": "yellow",
    "fm.info": "blue",
    "fm.accent": "bold blue",
    "fm.muted": "dim",
    # identity / structure
    "fm.name": "bold",
    "fm.name.inactive": "bold dim",
    "fm.label": "dim",
    "fm.section": "bold dim",
    "fm.secret": "dim",
    # domain states
    "fm.status.running": "green",
    "fm.status.stopped": "red",
    "fm.rail.active": "green",
    "fm.rail.inactive": "dim",
    "fm.env.prod": "red",
    "fm.env.dev": "default",
}


def _strip_colors(style_value: str) -> str:
    """Keep emphasis (bold/dim/italic/underline/reverse), drop colors."""
    kept = [w for w in style_value.split() if w in ("bold", "dim", "italic", "underline", "reverse", "blink")]
    return " ".join(kept) or "default"


THEMES: dict[str, dict[str, str]] = {
    "default": DEFAULT_TOKENS,
    # Color-blind safe: no colors at all; emphasis + the text itself carry state.
    "mono": {token: _strip_colors(value) for token, value in DEFAULT_TOKENS.items()},
    "high-contrast": {
        **DEFAULT_TOKENS,
        "fm.ok": "bright_green bold",
        "fm.error": "bright_red bold",
        "fm.warn": "bright_yellow bold",
        "fm.status.running": "bright_green bold",
        "fm.status.stopped": "bright_red bold reverse",
        "fm.rail.active": "bright_green",
        "fm.env.prod": "bright_red bold underline",
        "fm.muted": "default",
        "fm.label": "default",
    },
}


def build_theme(name: str | None = None, overrides: dict[str, str] | None = None) -> Theme:
    """Resolve a named theme + per-token overrides into a rich Theme.

    ``FM_THEME`` (env) wins over ``name`` (config) wins over "default".
    Unknown theme names or invalid style strings fail loudly -- a silently
    wrong theme is a debugging trap.
    """
    resolved_name = os.environ.get("FM_THEME") or name or "default"
    if resolved_name not in THEMES:
        raise ValueError(f"Unknown output theme '{resolved_name}'. Available: {', '.join(sorted(THEMES))}.")

    tokens = dict(THEMES[resolved_name])
    for token, value in (overrides or {}).items():
        Style.parse(value)  # validate loudly
        tokens[token] = value
    return Theme(tokens)


_pushed = False


def apply_output_theme(name: str | None = None, overrides: dict[str, str] | None = None) -> None:
    """Push the theme onto both singleton consoles (idempotent re-apply)."""
    global _pushed

    from frappe_manager.output_manager.console_singleton import get_stderr_console, get_stdout_console

    theme = build_theme(name, overrides)
    for console in (get_stdout_console(), get_stderr_console()):
        if _pushed:
            console.pop_theme()
        console.push_theme(theme)
    _pushed = True
