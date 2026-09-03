"""Every top-level `fm` command says what kind of address it takes.

`fm --help` used to render every command in one flat "Commands" panel, so telling what a command
operates on meant reading its own help text one at a time. The panel now leads with the address
grammar the command's own positional argument advertises -- BENCH, BENCH/SITE, or BENCH/DOMAIN --
plus a fourth panel for the handful of commands and sub-apps that take no address at all.

Rich prints the panel a command is left in when `rich_help_panel` is unset ("Commands") BEFORE
every named panel (`typer/rich_utils.py`: the default panel is always rendered first, and only
suppressed entirely when empty). So a command added without an explicit panel does not quietly
join a leftover box -- it silently jumps to the top of `--help`, ahead of this grouping altogether.
`test_every_command_declares_a_panel` is the test that catches that.

The expected panel for an ordinary command is DERIVED from its own positional argument's metavar
(`frappe_manager/commands/arguments.py` builds every one of `BENCH`, `BENCH(/SITE...)`,
`BENCH(/DOMAIN...)` from a small family of `Annotated` aliases), not hand-listed, for the same
reason `test_option_scope_panels.py` derives its expectations instead of hardcoding them: a
hand-written list is a second place to update and the first one to rot. The three sub-apps
(`services`, `self`, `ssl`) have no positional of their own at the top level to derive a panel
from -- their bucket is a deliberate taxonomy decision, pinned by hand below.
"""

import click
import pytest
import typer

from frappe_manager.commands import _PANEL_BENCH, _PANEL_DOMAIN, _PANEL_GLOBAL, _PANEL_SITE, app


def _group() -> click.Group:
    """Duplicated from `test_option_scope_panels.py` rather than imported: no test module in this
    repo imports another as a plain module (pytest's default `prepend` import mode makes it work by
    accident, since `tests/unit/cli/` has no `__init__.py`, but nothing else here relies on that),
    and the helper is three lines."""
    group = typer.main.get_command(app)
    assert isinstance(group, click.Group)
    return group


# The 3 sub-apps have no address of their own to derive a panel from; hand-pinned because there
# are only three of them and their bucket is a taxonomy decision, not a derivable fact.
GROUP_PANELS = {
    "services": _PANEL_GLOBAL,
    "self": _PANEL_GLOBAL,
    "ssl": _PANEL_DOMAIN,
}


def _panel(command: click.Command) -> object:
    """The raw `rich_help_panel` attribute, NOT normalised: unset is a `typer.models.DefaultPlaceholder`
    whose `bool()` is `False`, not `None` and not a `str`. Both `is None` and `isinstance(_, str)`
    misread it -- the first says "set", the second says "unset but not falsy" -- so callers that
    need "was this actually set" must check truthiness, not identity or type."""
    return getattr(command, "rich_help_panel", None)


def _expected_panel(command: click.Command) -> str:
    """Bench / Site / Domain from the command's own positional metavar; Global when it has none."""
    arguments = [p for p in command.params if isinstance(p, click.Argument)]
    if not arguments:
        return _PANEL_GLOBAL
    metavar = arguments[0].metavar or ""
    if "/SITE" in metavar:
        return _PANEL_SITE
    if "/DOMAIN" in metavar:
        return _PANEL_DOMAIN
    return _PANEL_BENCH


def test_every_command_declares_a_panel():
    """The regression that costs the most and is invisible in review: a command registered without
    `rich_help_panel` does not join the default box quietly, it renders ABOVE every panel in this
    file. Every entry -- top-level command and sub-app alike -- must set one explicitly."""
    missing = [name for name, command in _group().commands.items() if not _panel(command)]
    assert not missing, f"commands with no rich_help_panel (render ahead of every named panel): {missing}"


@pytest.mark.parametrize("command_name", sorted(_group().commands))
def test_the_panel_matches_the_command_s_own_address_grammar(command_name):
    command = _group().commands[command_name]
    expected = GROUP_PANELS[command_name] if isinstance(command, click.Group) else _expected_panel(command)
    assert _panel(command) == expected


def test_every_sub_app_is_covered_by_the_hand_pinned_table():
    """Fails loudly, not silently, if a 4th sub-app is added and nobody updates GROUP_PANELS."""
    group_names = {name for name, command in _group().commands.items() if isinstance(command, click.Group)}
    assert group_names == set(GROUP_PANELS)
