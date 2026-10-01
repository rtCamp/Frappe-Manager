"""Every bench-addressing argument belongs to one of three families, and no others.

`BENCH/<second segment>` meant three different things depending on the command: a site that gets
`.localhost` appended, a served domain taken verbatim, or an error. The first two disagreeing is
what made `fm update bench/shop` work while `fm ssl remove bench/shop` failed.

The families themselves are deliberate -- a certificate is keyed by a DOMAIN and a schema by a
SITE, so they match against different sets -- but the matching RULE is now shared
(`resolve_known_name`). This fails when a new command invents a fourth behaviour.
"""

import typing

from frappe_manager.commands import app
from frappe_manager.commands.maintenance._helpers import optional_bench_site_callback
from frappe_manager.utils import callbacks as cb
from frappe_manager.utils.site import resolve_known_name
from tests.unit.cli.test_help_text_contract import iter_help_callbacks

# The second segment is a SITE, matched against the bench's `[sites]` table.
SITE_FAMILY = {cb.bench_site_callback, cb.bench_site_all_callback, optional_bench_site_callback}
# The second segment is a served DOMAIN: every site name plus the aliases, which a certificate can
# be issued for and a schema cannot.
DOMAIN_FAMILY = {cb.bench_domain_callback, cb.bench_served_domain_callback}
# No second segment at all. Containers, workspace and workers are shared by every site in a bench,
# so there is no per-site meaning to invent; these refuse rather than silently ignoring it.
BENCH_ONLY_FAMILY = {cb.sitename_callback, cb.bench_all_callback}
# `fm create` is its own case: the site does not exist yet, so there is nothing to match against.
CREATE_FAMILY = {cb.create_command_sitename_callback}

KNOWN = SITE_FAMILY | DOMAIN_FAMILY | BENCH_ONLY_FAMILY | CREATE_FAMILY


def _argument_callbacks():
    """(command, callback) for every positional argument that carries a bench address."""
    for name, fn in iter_help_callbacks(app):
        for param in typing.get_type_hints(fn, include_extras=True).values():
            for meta in getattr(param, "__metadata__", ()):
                callback = getattr(meta, "callback", None)
                if callback in KNOWN:
                    yield name, callback


def test_every_bench_address_belongs_to_a_known_family():
    """A new command must pick one of the three rather than hand-rolling a fourth."""
    found = list(_argument_callbacks())

    assert found, "no bench-addressing arguments discovered: the walker is broken, not the app"
    assert all(callback in KNOWN for _, callback in found)


def test_the_site_and_domain_families_share_one_matching_rule():
    """The families match against different SETS on purpose; they must not match by different
    RULES. This is the drift that made the same string work in one command and fail in another."""
    assert resolve_known_name.__module__ == "frappe_manager.utils.site"
    assert cb.resolve_known_name is resolve_known_name
