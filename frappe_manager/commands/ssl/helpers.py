"""Helper functions for SSL commands."""

import typer

from frappe_manager.output_manager import OutputHandler, get_global_output_handler


def get_output_handler(ctx: typer.Context) -> OutputHandler:
    return get_global_output_handler()


# Shared by the bench and external certificate cards so both grammars agree on what each row's
# `status` reads as: text carries the state (mono-theme safe), the theme token only enhances it.
_CERT_STATUS_WORDS: dict[str, tuple[str, str]] = {
    "issued": ("fm.ok", "issued"),
    "not_issued": ("fm.error", "not issued"),
    "none": ("fm.muted", "no ssl"),
    "no_ssl": ("fm.muted", "no ssl"),
    "unknown": ("fm.warn", "unknown"),
    "missing": ("fm.error", "missing"),
    "orphan": ("fm.warn", "orphan"),
}


def cert_status_word(status: str) -> str:
    """A certificate row's `status` as a themed word for a card's headline meta."""
    token, word = _CERT_STATUS_WORDS[status]
    return f"[{token}]{word}[/{token}]"


def cert_expiry_words(iso_expiry: str) -> str:
    """A row's ISO 8601 `expiry` as a date a person reads.

    The row carries ISO because `--json` consumers parse it; a card is for a human, and
    `2026-11-15T12:00:37+00:00` is the machine's spelling of `2026-11-15 12:00`. Anything that
    will not parse is shown as written rather than dropped: a date fm cannot read is still the
    only expiry it has.
    """
    from datetime import datetime

    try:
        return datetime.fromisoformat(iso_expiry).strftime("%Y-%m-%d %H:%M")
    except ValueError:
        return iso_expiry
