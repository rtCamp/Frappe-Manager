"""The 'fm' name is deprecated in favour of 'fmx' and is removed in 1.0.0."""

from unittest.mock import patch

import pytest

from frappe_manager import main

NOTICE = "'fm' is being renamed to 'fmx'"


def run_as(argv0, monkeypatch, env=None):
    monkeypatch.setattr("sys.argv", [argv0, "list"])
    for key, value in (env or {}).items():
        monkeypatch.setenv(key, value)
    with patch.object(main, "app"), patch.object(main.atexit, "register"):
        main.cli_entrypoint()


@pytest.mark.parametrize("argv0", ["fm", "/home/u/.local/bin/fm"])
def test_running_as_fm_warns_on_stderr_only(argv0, monkeypatch, capsys):
    """Every run as fm warns, and only on stderr so piped stdout stays clean."""
    run_as(argv0, monkeypatch)
    out, err = capsys.readouterr()
    assert NOTICE in err
    assert NOTICE not in out


def test_running_as_fmx_does_not_warn(monkeypatch, capsys):
    """fmx is the new name: no warning."""
    run_as("/home/u/.local/bin/fmx", monkeypatch)
    out, err = capsys.readouterr()
    assert NOTICE not in err + out


def test_tab_completion_as_fm_stays_silent(monkeypatch, capsys):
    """A warning during completion would be read as candidates and corrupt them."""
    run_as("fm", monkeypatch, env={"_FM_COMPLETE": "complete_zsh"})
    out, err = capsys.readouterr()
    assert NOTICE not in err + out
