"""Every status surface answers machines as well as humans.

Four of them did not: `fm maintenance status`, `fm auth status`, `fm ssl ca status` and
`fm services trusted-proxies status`. The first is the one a deploy script most wants, since
"is this site in maintenance" could only be answered by parsing prose.

This walks the live Typer app, so a new status command that forgets `--json` fails here rather
than being discovered by whoever tries to script it.
"""

from collections.abc import Iterator

import pytest
import typer.main
from click import Command, Group

from frappe_manager.commands import app

# Commands whose whole purpose is reporting state. A verb that CHANGES something is not here: its
# output is a progress narrative, and `fm --events json` is the machine channel for that.
STATUS_COMMANDS = {
    "info",
    "list",
    "ssl list",
    "ssl ca status",
    "auth status",
    "tools status",
    "telemetry status",
    "maintenance status",
    "domain list",
    "apps list",
    "services info",
    "services trusted-proxies status",
}


def _commands(group: Group, prefix: str = "") -> Iterator[tuple[str, Command]]:
    for name, command in group.commands.items():
        full = f"{prefix} {name}".strip()
        if isinstance(command, Group):
            yield from _commands(command, full)
        else:
            yield full, command


@pytest.fixture(scope="module")
def discovered() -> dict[str, Command]:
    root = typer.main.get_command(app)
    assert isinstance(root, Group)
    return dict(_commands(root))


def test_every_status_command_exists(discovered: dict[str, Command]):
    """Guards the set itself: a renamed command must be renamed here too, not silently dropped."""
    assert not (STATUS_COMMANDS - set(discovered))


def test_every_status_command_offers_json(discovered: dict[str, Command]):
    """A status surface a script cannot read forces prose parsing, which breaks on any wording
    change -- including the wording fixes that make the human output better."""
    without = sorted(
        name
        for name in STATUS_COMMANDS
        if not any("--json" in (param.opts or []) for param in discovered[name].params)
    )

    assert without == []
