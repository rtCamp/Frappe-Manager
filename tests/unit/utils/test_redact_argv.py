"""fm.log records the invocation; it must not record the secrets in it.

`~/frappe/logs/fm.log` is what an operator pastes into a bug report, and the first line of every
run is the command line as typed. A `--github-token` landed there in cleartext, and so did
`--db-password`, `--db-admin-password` and `--admin-pass`.
"""

import pytest

from frappe_manager.utils.helpers import SECRET_OPTIONS, redact_argv

pytestmark = pytest.mark.timeout(15)


@pytest.mark.parametrize("option", sorted(SECRET_OPTIONS))
def test_every_secret_option_has_its_value_redacted(option):
    """Separate-argument spelling, for each option declared secret."""
    assert "s3cret" not in redact_argv(["create", "mybench", option, "s3cret"])


@pytest.mark.parametrize("option", sorted(SECRET_OPTIONS))
def test_the_equals_spelling_is_redacted_too(option):
    """`--db-password=x` is one argv entry, so a scan that only looks at the NEXT one misses it."""
    assert "s3cret" not in redact_argv(["create", "mybench", f"{option}=s3cret"])


def test_a_stdin_marker_is_kept():
    """`-` means "read it from stdin", so it names no secret and hiding it loses information."""
    assert redact_argv(["create", "x", "--db-password", "-"]) == "create x --db-password -"


def test_nothing_else_is_touched():
    """Redaction must not mangle the rest of the line, or the log stops answering what was run."""
    argv = ["create", "mybench", "--apps", "erpnext", "--db-host", "db.example.com"]
    assert redact_argv(argv) == " ".join(argv)


def test_a_value_that_looks_like_an_option_is_still_redacted():
    """Matched on the option NAME, never on the value's shape: a password can look like anything."""
    assert "--not-a-flag" not in redact_argv(["create", "x", "--admin-pass", "--not-a-flag"])


def test_no_secret_bearing_option_is_missing_from_the_set():
    """The set is hand-written, so it can fall behind a newly added flag -- which is how a
    Cloudflare `--api-token` reached fm.log in cleartext. Walk the LIVE CLI instead of trusting it:
    any value-taking option whose name says it carries a credential must be redacted.

    `--key` is the one deliberate exclusion: it names a PEM file path, not a secret."""
    import click
    import typer.main

    from frappe_manager.commands import app

    NAMES_A_SECRET = ("token", "pass", "secret", "key", "cred")
    ALLOWED_VISIBLE = {"--key"}

    found: set[str] = set()

    def walk(command):
        if isinstance(command, click.Group):
            for child in command.commands.values():
                walk(child)
            return
        for param in command.params:
            if isinstance(param, click.Option) and not param.is_flag:
                name = param.opts[0]
                if any(word in name for word in NAMES_A_SECRET):
                    # Every spelling, not just the long one: `fm ngrok -t <token>` carries the
                    # same secret and is what an operator actually types.
                    found.update(param.opts)

    walk(typer.main.get_command(app))

    missing = found - SECRET_OPTIONS - ALLOWED_VISIBLE
    assert not missing, f"secret-bearing options not redacted from fm.log: {sorted(missing)}"
