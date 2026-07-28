"""Help text is never hard-wrapped.

Rich renders docstring newlines verbatim: a paragraph wrapped at the source's
120-char style displays frozen at that width instead of reflowing to the
terminal. Contract: every paragraph in every registered command's docstring
(incl. sub-apps and the top-level callback) is ONE line; structured lines
(lists, tables, pipelines, prompts) keep their newlines.

Internal helpers are exempt by construction -- we walk the live typer app
tree, so only text that actually renders in --help is checked.
"""

import inspect

import typer

from frappe_manager.commands import app

# Lines that legitimately own a newline: lists, tables, pipelines, shell, headings.
_STRUCTURE_PREFIXES = ("-", "*", "|", ">", "$", "#")


def wrapped_paragraphs(doc: str) -> list[str]:
    """The hard-wrapped (frozen) paragraphs of a docstring, [] when compliant."""
    frozen = []
    for para in doc.split("\n\n"):
        lines = [line.strip() for line in para.strip().splitlines() if line.strip()]
        if len(lines) > 1 and not any(
            line.startswith(_STRUCTURE_PREFIXES) or line[0].isdigit() for line in lines
        ):
            frozen.append(" / ".join(lines[:2])[:80] + "...")
    return frozen


def iter_help_callbacks(typer_app: typer.Typer, prefix: str = "fm"):
    """(qualified name, callback) for everything whose docstring renders in help."""
    if typer_app.registered_callback and typer_app.registered_callback.callback:
        yield prefix, typer_app.registered_callback.callback
    for cmd in typer_app.registered_commands:
        callback = cmd.callback
        name = cmd.name or (callback.__name__ if callback else "?")
        if callback:
            yield f"{prefix} {name}", callback
    for group in typer_app.registered_groups:
        sub_app = group.typer_instance
        if sub_app is not None:
            yield from iter_help_callbacks(sub_app, f"{prefix} {group.name or ''}".strip())


def test_detector_catches_a_frozen_paragraph():
    # The checker must itself fail on a plausible violation, else this file is decoration.
    frozen = "First half of a sentence that was\nwrapped at some arbitrary column."
    assert wrapped_paragraphs(frozen)
    assert wrapped_paragraphs("One long single-line paragraph that reflows fine.") == []
    assert wrapped_paragraphs("Two modes:\n\n- bullet one\n- bullet two") == []


def test_no_command_help_text_is_hard_wrapped():
    violations = []
    for name, callback in iter_help_callbacks(app):
        doc = inspect.getdoc(callback)
        if not doc:
            continue
        for para in wrapped_paragraphs(doc):
            violations.append(f"{name} ({callback.__module__}.{callback.__name__}): {para}")

    assert not violations, (
        "Hard-wrapped help paragraphs found -- rich renders these frozen at the source "
        "wrap width instead of reflowing. Write each paragraph as ONE source line "
        ":\n  " + "\n  ".join(violations)
    )
