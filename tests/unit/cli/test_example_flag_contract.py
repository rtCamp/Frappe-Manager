"""Every `@example` names flags its own command actually has.

`typer_examples` renders `code=` as typed, with no parsing and no validation (see
`.venv/lib/python3.13/site-packages/typer_examples/_renderer.py`): a typo'd flag renders wrong in
`fm <cmd> --help` and, via `scripts/update_cli_docs.py:44-67` `load_examples()`, wrong in the
generated `docs/commands/*.md` page too. `scripts/docslint.py` cannot catch it: `hand_written()`
(`docslint.py:97-101`) excludes `docs/commands/` from the flag check by name, on purpose, because
generated pages are exempt from the *style* checks that function also runs. Nothing else in the
repo parses `@example` code strings against the live Click app. This file is that check.

Walks examples the same way `load_examples()` does -- through `typer_examples.get_all_examples`,
the one function both the doc generator and this test call -- so the two can never disagree about
what counts as an example. Collects each command's real flags the way `docslint.py:104-121`
`fm_flags()` does (`param.opts | param.secondary_opts`), but scoped to ONE command's own
`.params` rather than the whole app: a flat, app-wide flag set would let a typo that happens to
match some OTHER command's real flag slip through silently, which defeats the point.

Passthrough commands forward a foreign tool's own flags through `ctx.args`
(`context_settings={"allow_extra_args": True}`), and their examples legitimately name flags `fm`
itself does not declare. `FOREIGN` is a per-command allowlist for exactly those, mirroring
`docslint.py`'s own `FOREIGN` set (`docslint.py:36-81`) but keyed by command so an allowance
granted to one passthrough command can never cover a typo in an unrelated one.
"""

import re

import click
import pytest
import typer.main
from typer_examples import get_all_examples

from frappe_manager.commands import app

# Same shape as `docslint.py:215`'s inline pattern: long flags only. Short flags (`-c`, `-f`, `-d`)
# are cheap to typo-check by eye and easy to false-positive on (a domain example's `-d` reads the
# same as a flag), so both this test and docslint draw the line at `--`.
_FLAG_RE = re.compile(r"(?<![\w-])(--[a-z][a-z0-9-]{2,})")

# Per-command allowlist for flags a passthrough command's OWN examples legitimately name, that
# belong to the tool it forwards to rather than to `fm`. Keyed by the same path tuple
# `get_all_examples` uses, so an entry here can only ever widen the check for the one command it
# names -- adding a foreign flag anywhere is a deliberate, reviewable edit to this dict.
#
# `fm shell` and `fm ssl acme-sh` need entries; `fm compose` and `fm services shell` were
# considered and deliberately do NOT get one:
#
# - `fm compose` (`commands/compose.py`) is a real `ctx.args` passthrough to `docker compose`
#   (its registration in `commands/__init__.py` sets `allow_extra_args`), but its four examples
#   (`commands/compose.py:13-32`) only use bare docker subcommands and short flags (`ps`,
#   `logs -f frappe`, `exec frappe bash`, `restart frappe`) -- nothing `_FLAG_RE` matches. No
#   entry is needed today; the first example naming a long docker flag (e.g. `--profile`) fails
#   this test until someone adds one here on purpose.
# - `fm services shell` (`services/shell.py`) LOOKS like part of the same passthrough family but
#   is not one: `shell_services` takes a typed `service_name: ServicesEnum` argument and its own
#   `--user` option, and its registration (`services/__init__.py:19`) sets no
#   `allow_extra_args`/`ctx.args` forwarding at all. Its two examples ("global-db",
#   "global-nginx-proxy") are enum VALUES, not flags. Nothing foreign can ever reach it through
#   this mechanism, so it gets no entry.
FOREIGN: dict[tuple[str, ...], frozenset[str]] = {
    # `bench`'s own flag, forwarded through the heredoc example's `bench build --app frappe`
    # (shell.py:100). `fm shell` itself has no `--app`.
    ("shell",): frozenset({"--app"}),
    # acme.sh's own flags. `acmesh_passthrough` (ssl/acme_sh.py:30-32) declares no Click options
    # of its own beyond the implicit `--help`; everything else is `ctx.args` handed to the
    # bundled `acme.sh` binary untouched (ssl/acme_sh.py:40-61).
    ("ssl", "acme-sh"): frozenset({"--list", "--info", "--renew", "--force"}),
}


def _group() -> click.Group:
    """Duplicated from `test_option_scope_panels.py`/`test_command_scope_panels.py` rather than
    imported: no test module in this repo imports another as a plain module, and the helper is
    three lines."""
    group = typer.main.get_command(app)
    assert isinstance(group, click.Group)
    return group


def _resolve(path: tuple[str, ...]) -> click.Command:
    """The live `click.Command` a `get_all_examples` path tuple names, walking sub-apps by name."""
    node: click.Group | click.Command = _group()
    for segment in path[:-1]:
        assert isinstance(node, click.Group)
        node = node.commands[segment]
    assert isinstance(node, click.Group)
    return node.commands[path[-1]]


def _real_flags(command: click.Command) -> set[str]:
    """A command's own flag names, collected the way `docslint.py:104-121` `fm_flags()` does."""
    flags: set[str] = set()
    for param in command.params:
        if isinstance(param, click.Option):
            flags |= set(param.opts) | set(param.secondary_opts)
    return flags


_EXAMPLES = get_all_examples(app)


def test_the_scan_actually_reaches_examples():
    # A broken traversal returning {} would make every check below vacuously pass.
    assert len(_EXAMPLES) >= 30


def test_every_foreign_allowlist_entry_is_a_real_command():
    """A renamed or removed passthrough command would otherwise leave a dead, silently unused
    entry in `FOREIGN` -- caught here instead of just going stale."""
    for path in FOREIGN:
        _resolve(path)  # raises KeyError if the command no longer exists at this path


@pytest.mark.parametrize("path", sorted(_EXAMPLES), ids=lambda p: "fm " + " ".join(p))
def test_every_example_flag_exists_on_its_own_command(path: tuple[str, ...]):
    command = _resolve(path)
    allowed = _real_flags(command) | FOREIGN.get(path, frozenset())

    violations = []
    for ex in _EXAMPLES[path]:
        unknown = sorted(set(_FLAG_RE.findall(ex.code)) - allowed)
        if unknown:
            violations.append(f"{ex.desc!r}: {unknown}")

    assert not violations, f"fm {' '.join(path)}: flags named in an example that the command does not have: {violations}"
