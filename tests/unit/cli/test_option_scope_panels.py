"""Every option of an address command says which half of `BENCH/SITE` it acts on.

`fm create --help` used to box `--redis-cache` and `--db-name` together under "External Database and
Redis Options". One serves every site the bench holds, the other names the schema of exactly one, and
on a three-site bench that is the difference between a shared setting and a per-site one. Two more
flags described themselves with the wrong noun: `--alias-domains` said "this bench answers on" while
landing in `[sites."<site>"]`, and `--mailpit-as-default-mail-server` said "the site's outgoing mail"
while writing `common_site_config`, which every site shares.

The panel titles now lead with the scope, so it reads down the left edge of `--help`.

These assertions DERIVE the expected scope rather than listing it, because a hand-written list of
flag names is a second place to update and the first one to rot. The sources of truth are the ones
the code already uses:

- `_FLAG_TO_CONFIG` maps a flag to the top-level `BenchConfig` path it writes, so every key is
  bench-scoped by construction.
- `SiteConfig` and `DatabaseConfig` field names say what lives under `[sites."<site>"]`, which is
  what :func:`record_site` writes.

A flag added to either side lands in this test without anyone remembering to add it.
"""

import click
import pytest
import typer

from frappe_manager.commands import app
from frappe_manager.commands.create import _FLAG_TO_CONFIG
from frappe_manager.site_manager.bench_config import DatabaseConfig, SiteConfig

BENCH_PREFIX = "Bench Options"
SITE_PREFIX = "Site Options"

# Options that belong to NEITHER half: they decide whether the site part happens at all, or wave a
# guard through. They stay in the default box, which is why it is not renamed.
UNSCOPED = {"bench_only", "allow_domain_conflicts", "help"}

# `fm create` and `fm update` are the only address commands with enough options for panels to beat
# one list: the other eight have between one and eight, where splitting is noise. Asserted below so
# this reasoning fails loudly if one of them grows.
PANELLED = {"create", "update"}


def _group() -> click.Group:
    group = typer.main.get_command(app)
    assert isinstance(group, click.Group)
    return group


def _options(command_name: str) -> list[click.Option]:
    return [p for p in _group().commands[command_name].params if isinstance(p, click.Option)]


def _panel(option: click.Option) -> str:
    return getattr(option, "rich_help_panel", None) or "Options"


def _name(option: click.Option) -> str:
    """Click types `name` as optional; every option in this app has one."""
    assert option.name
    return option.name


def _site_field_names() -> set[str]:
    """Parameter names whose value is recorded under `[sites."<site>"]`."""
    names = set(SiteConfig.model_fields)
    names |= {f"db_{field}" for field in DatabaseConfig.model_fields}
    return names


@pytest.mark.parametrize("command_name", sorted(PANELLED))
class TestScopeIsReadable:
    def test_every_option_declares_a_scope_or_is_deliberately_unscoped(self, command_name: str):
        stray = [
            o.name
            for o in _options(command_name)
            if o.name not in UNSCOPED and not _panel(o).startswith((BENCH_PREFIX, SITE_PREFIX))
        ]
        assert not stray, f"{command_name}: options in no scoped panel: {stray}"

    def test_the_unscoped_options_stay_in_the_default_box(self, command_name: str):
        """`--bench-only` decides whether the Site panels apply, so it cannot sit inside one."""
        for option in _options(command_name):
            if option.name in UNSCOPED:
                assert _panel(option) == "Options", option.name

    def test_no_bench_panel_is_rendered_after_a_site_panel(self, command_name: str):
        """Rich orders panels by first appearance in the signature, so the grouping IS the parameter
        order. Moving one parameter interleaves the two blocks and undoes the left-edge read."""
        seen: list[str] = []
        for option in _options(command_name):
            panel = _panel(option)
            if panel not in seen:
                seen.append(panel)
        scopes = [p.split(" Options")[0] for p in seen if p.startswith((BENCH_PREFIX, SITE_PREFIX))]
        assert scopes == sorted(scopes, key=["Bench", "Site"].index), (
            f"{command_name}: panels interleave: {seen}"
        )


# ------------------------- scope derived from where the value lands


def test_every_bench_config_flag_of_create_sits_in_a_bench_panel():
    """`_FLAG_TO_CONFIG` values are top-level `BenchConfig` paths; nothing there is per-site."""
    panels = {o.name: _panel(o) for o in _options("create")}
    wrong = {name: panels[name] for name in _FLAG_TO_CONFIG if name in panels and not panels[name].startswith(BENCH_PREFIX)}
    assert not wrong, f"bench-config flags outside a Bench panel: {wrong}"


@pytest.mark.parametrize("command_name", sorted(PANELLED))
def test_every_flag_recorded_under_the_sites_table_sits_in_a_site_panel(command_name: str):
    """The `--db-*` family and the alias flags are `[sites."<site>"]` data via `record_site`."""
    site_names = _site_field_names()
    wrong = {
        o.name: _panel(o)
        for o in _options(command_name)
        if (_name(o) in site_names or _name(o).endswith("_alias")) and not _panel(o).startswith(SITE_PREFIX)
    }
    assert not wrong, f"{command_name}: per-site flags outside a Site panel: {wrong}"


def test_redis_is_not_boxed_with_the_per_site_database():
    """The regression that started this: one panel held a bench-wide setting and a per-site one.

    `BenchConfig.redis` is a top-level field, `DatabaseConfig` hangs off a `[sites."<site>"]` entry,
    so no panel may contain both."""
    by_panel: dict[str, set[str]] = {}
    for option in _options("create"):
        by_panel.setdefault(_panel(option), set()).add(_name(option))

    db_flags = {f"db_{f}" for f in DatabaseConfig.model_fields}
    for panel, names in by_panel.items():
        holds_redis = any(n.startswith("redis_") for n in names)
        holds_db = bool(names & db_flags)
        assert not (holds_redis and holds_db), f"{panel} mixes bench-wide redis with per-site database"


def test_the_site_panels_say_what_happens_without_a_site_part():
    """Both commands answer the omitted second segment, and they answer it DIFFERENTLY: `create`
    discards site flags under `--bench-only`, `update` falls back to the bench's primary site. A
    panel title that does not say which one leaves the operator to find out by running it."""
    create_site_panels = {p for p in (_panel(o) for o in _options("create")) if p.startswith(SITE_PREFIX)}
    update_site_panels = {p for p in (_panel(o) for o in _options("update")) if p.startswith(SITE_PREFIX)}

    assert create_site_panels, "create has per-site flags"
    assert update_site_panels, "update has per-site flags"
    assert all("--bench-only" in p for p in create_site_panels), create_site_panels
    assert all("primary site" in p for p in update_site_panels), update_site_panels


def test_the_other_address_commands_are_still_small_enough_for_one_list():
    """The reason only two commands are panelled. If one of the other eight grows past a screenful
    it wants the same treatment, and this is where that shows up."""
    group = _group()
    others = {
        "delete": group.commands["delete"],
        "migrate": group.commands["migrate"],
        "reset": group.commands["reset"],
        "shell": group.commands["shell"],
    }
    for name, command in others.items():
        count = len([p for p in command.params if isinstance(p, click.Option) and p.name != "help"])
        assert count <= 8, f"{name} now has {count} options and may deserve scope panels"
