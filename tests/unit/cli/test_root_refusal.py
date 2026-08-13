"""fm refuses to run as root, before it writes anything.

Root is not a supported way to run fm, and it fails in ways worse than a refusal: Frappe's own
bench exits 1 as root unless ``frappe_user`` is set in the bench config (bench/cli.py
``change_uid``), which fm does not set, so web and workers land in FATAL while the bench still
looks created; and the shared service containers are named per-host (``fm_global-db``), so a root
fm fights the real user's containers.

The load-bearing part is the ORDER: the check fires before ``app()``, because ``app_callback``
creates ``CLI_DIR`` and a root-owned ``~/frappe`` is something the real user then cannot remove
without sudo. These tests pin the refusal, its wording, and that ordering.
"""

import os
import subprocess
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

from frappe_manager.main import cli_entrypoint
from frappe_manager.output_manager.theme import apply_output_theme


@pytest.fixture(autouse=True)
def _contain_entrypoint_side_effects():
    """``cli_entrypoint`` registers an atexit cleanup that needs a live output handler.

    Left alone it fires during interpreter shutdown, after the handler fixture is gone, and
    spews RuntimeError noise out of every test in this module. The refusal is unaffected.
    """
    with patch("frappe_manager.main.atexit.register"):
        yield


def _run_as(euid: int):
    """Run the entrypoint at a given euid with the typer app stubbed out."""
    with patch.object(os, "geteuid", lambda: euid), patch("frappe_manager.commands.app") as app:
        cli_entrypoint()
    return app


def test_root_is_refused_with_a_nonzero_exit(capsys: pytest.CaptureFixture[str]):
    with pytest.raises(SystemExit) as exit_info:
        _run_as(0)

    assert exit_info.value.code == 1, "refusing root must fail the command, not exit cleanly"
    assert "root" in capsys.readouterr().err.lower()


def test_root_never_reaches_the_typer_app():
    """CLI_DIR creation, the docker probe and every command all live behind app()."""
    with patch.object(os, "geteuid", lambda: 0), patch("frappe_manager.commands.app") as app, pytest.raises(SystemExit):
        cli_entrypoint()

    app.assert_not_called()


def test_refusal_names_the_way_out(capsys: pytest.CaptureFixture[str]):
    """A bare 'not supported' leaves the operator guessing; the message has to say what to do."""
    message = ""
    with pytest.raises(SystemExit):
        _run_as(0)
    message = " ".join(capsys.readouterr().err.lower().split())

    assert "docker" in message, "must say the non-root user needs docker access"
    assert "owns the benches" in message, "must say which user to run as"


def test_refusal_creates_nothing_on_disk(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """The whole point of checking before app(): no root-owned CLI_DIR left behind."""
    home = tmp_path / "frappe-home"
    monkeypatch.setenv("FRAPPE_MANAGER_HOME", str(home))

    with pytest.raises(SystemExit):
        _run_as(0)

    assert not home.exists(), f"{home} was created before the refusal"


@pytest.mark.parametrize("euid", [1, 501, 1000, 65534])
def test_non_root_is_not_refused(euid: int):
    """Inert for every ordinary user, including low system uids and nobody."""
    assert _run_as(euid).call_count == 1, f"euid {euid} must be allowed to run fm"


def test_guard_survives_a_broken_output_theme():
    """Theme setup runs first and is deliberately non-fatal; the refusal must still fire.

    The failure the fallback exists for is a bad FM_THEME/FM_STYLE in the environment: the first
    call raises, the vars are dropped, the retry succeeds. So this fails once, not forever.
    """
    calls = {"n": 0}
    real_theme = apply_output_theme

    def fail_first(*args, **kwargs):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("bad FM_THEME")
        return real_theme(*args, **kwargs)

    with (
        patch.object(os, "geteuid", lambda: 0),
        patch("frappe_manager.commands.app") as app,
        patch("frappe_manager.output_manager.theme.apply_output_theme", side_effect=fail_first),
        pytest.raises(SystemExit),
    ):
        cli_entrypoint()

    assert calls["n"] == 2, "the theme fallback should have retried once"
    app.assert_not_called()


def test_guard_precedes_the_app_call():
    """Pins the ordering that makes the guard worth anything.

    CLI_DIR is created inside app_callback, so a guard placed after ``app()`` would already have
    written a root-owned directory by the time it refused.
    """
    assert "CLI_DIR.mkdir" in Path("frappe_manager/commands/__init__.py").read_text(), (
        "CLI_DIR is no longer created in app_callback; re-check where the root guard belongs"
    )

    main_source = Path("frappe_manager/main.py").read_text()
    # Anchored on the indented statement: "app()" also appears in this module's docstring.
    assert main_source.index("geteuid") < main_source.index("\n        app()"), "the root guard must precede app()"
    assert main_source.index("geteuid") < main_source.index("from frappe_manager.commands import app"), (
        "importing frappe_manager.commands creates CLI_DIR, so it must stay below the root check"
    )


def test_importing_main_touches_nothing_on_disk(tmp_path: Path):
    """The guard is only reachable if importing its own module writes nothing first.

    Three modules (frappe_manager.commands, utils.docker, utils.helpers) call get_logger() at
    module scope, which creates CLI_DIR and opens logs/fm.log. main.py therefore imports them at
    their use sites, below the check. This runs in a subprocess because the import is cached
    process-wide and every other test has already paid for it.
    """
    home = tmp_path / "fm-home"
    env = {**os.environ, "FRAPPE_MANAGER_HOME": str(home)}
    env.pop("PYTHONPATH", None)

    result = subprocess.run(  # noqa: S603
        [sys.executable, "-c", "import frappe_manager.main"],
        env=env,
        capture_output=True,
        text=True,
        cwd=Path.cwd(),
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert not home.exists(), f"importing frappe_manager.main created {home}: {list(home.rglob('*'))}"


def test_message_fits_a_default_width_terminal():
    """Rich soft-wraps to the console width, falling back to 80 when stderr is not a terminal.

    A precondition refusal that spills into four wrapped lines reads as noise, so this keeps it
    to at most two lines at that fallback width. It is also fm's house style: compare
    "Docker daemon not running. Please start docker service".
    """
    source = Path("frappe_manager/main.py").read_text()
    start = source.index("fm must not run as root")
    end = source.index("raise SystemExit(1)", start)
    literal_chars = sum(len(part) for part in source[start:end].split('"')[::2])

    assert literal_chars <= 160, f"refusal is {literal_chars} chars, more than two lines at width 80"
