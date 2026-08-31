"""The `BENCH[/SITE]` positional, black-box through the CLI.

A bench holds exactly one site today, so `fm shell` is the only command that can do
anything with a site part and every other command refuses one. These tests pin that
split, and they pin it at the CLI boundary rather than by calling the callbacks,
because the exit code and the message are the contract a user meets.

Most cases need no bench on disk: the refusal happens in the parameter callback,
BEFORE the must-exist check, which is the property that makes a mistyped address
cheap to diagnose. The two that need a bench say so.
"""

from unittest.mock import MagicMock, patch

import pytest
import typer
from typer.testing import CliRunner

from frappe_manager.commands.create import create
from frappe_manager.commands.restart import restart
from frappe_manager.commands.shell import shell
from frappe_manager.commands.ssl.list import list_certificates
from frappe_manager.site_manager.exceptions import BenchNotFoundError

runner = CliRunner()

BENCH = "x.localhost"


@pytest.fixture
def benches(tmp_path):
    """A benches directory holding one real bench, patched into both callback modules."""
    root = tmp_path / "sites"
    (root / BENCH).mkdir(parents=True)
    with patch("frappe_manager.utils.callbacks.CLI_BENCHES_DIRECTORY", root):
        yield root


def _ctx():
    """A stand-in typer context. `ctx.obj` is where the site half of an address is handed on."""
    ctx = MagicMock()
    ctx.obj = {}
    return ctx


def _app(name, fn):
    app = typer.Typer()
    app.command(name)(fn)
    return app


def _said(result) -> str:
    """`result.output` with rich's box drawing and line wrapping flattened.

    Typer renders a parameter refusal inside an 80-column bordered box, so any asserted phrase
    long enough to cross the boundary gets a `│\\n│` in the middle of it. That makes the assertion
    depend on terminal width rather than on what fm said, and it fails or passes depending on how
    pytest was invoked. Collapsing the borders and the whitespace keeps the assertions about the
    message.
    """
    text = result.output.replace("│", " ").replace("╭", " ").replace("╮", " ")
    text = text.replace("╰", " ").replace("╯", " ").replace("─", " ")
    return " ".join(text.split())


# --------------------------------------------------------------- parse failures


@pytest.mark.parametrize(
    ("address", "expected"),
    [
        ("a/b/c", "more than one"),
        ("shop/", "empty site"),
        ("/shop", "empty bench"),
    ],
)
def test_a_malformed_address_is_refused_before_any_bench_lookup(benches, address, expected):
    """No bench named `a` exists, and the message is about the ADDRESS, not a missing bench:
    a parse failure must not be reported as `bench not found`."""
    result = runner.invoke(_app("restart", restart), [address])
    assert result.exit_code == 2
    assert expected in _said(result)
    assert "not found" not in _said(result).lower()


# ------------------------------------------------- a site part where none is honoured


def test_a_bench_scoped_command_refuses_a_site_part(benches):
    result = runner.invoke(_app("restart", restart), [f"{BENCH}/{BENCH}"])
    assert result.exit_code == 2
    assert "takes a bench, not a site" in _said(result)


def test_the_refusal_names_the_bench_form_to_use(benches):
    """The operator needs the fix, not just the complaint."""
    result = runner.invoke(_app("restart", restart), [f"{BENCH}/{BENCH}"])
    assert f"use '{BENCH}'" in _said(result)


def test_an_ssl_command_refuses_a_site_part_too(benches):
    """The four `ssl` commands carried NO callback before this, so a slashed value reached
    `Bench.get_object` and died as a not-found error on a nested path."""
    result = runner.invoke(_app("list", list_certificates), [f"{BENCH}/{BENCH}"])
    assert result.exit_code == 2
    assert "takes a bench, not a site" in _said(result)


def test_a_bench_scoped_command_still_accepts_a_plain_bench(benches):
    """The refusal must not have cost the ordinary form: this gets past argument parsing
    and fails later on bench internals instead."""
    result = runner.invoke(_app("restart", restart), [BENCH])
    assert "takes a bench, not a site" not in _said(result)


# ------------------------------------------------------------------- fm create


def test_create_refuses_the_reserved_name():
    """A bare `all` is to become the address meaning every bench. Reserved now so no bench can be
    created that the keyword would later collide with."""
    result = runner.invoke(_app("create", create), ["all"])
    assert result.exit_code == 2
    assert "'all' is reserved" in _said(result)


def test_create_refuses_a_site_on_a_bench_that_does_not_exist(tmp_path):
    """`fm create shop/a.localhost` adds a site to `shop`. There is nothing to add to when `shop`
    is absent, and creating both at once would hide which half the operator got wrong.

    Asserted on the exception, not the output: `BenchNotFoundError` is rendered by `main.py`, and
    this bare test app has none of that, so it propagates instead of printing.
    """
    root = tmp_path / "sites"
    root.mkdir(parents=True)
    with patch("frappe_manager.utils.callbacks.CLI_BENCHES_DIRECTORY", root):
        result = runner.invoke(_app("create", create), ["shop/a.localhost"])
    assert result.exit_code != 0
    assert isinstance(result.exception, BenchNotFoundError)
    assert "shop" in str(result.exception)


def test_create_accepts_a_site_on_a_bench_that_exists(tmp_path):
    """The site-add form. It gets past argument parsing; the body then does the work, which is not
    what this asserts."""
    root = tmp_path / "sites"
    (root / "shop").mkdir(parents=True)
    (root / "shop" / "bench_config.toml").write_text(
        'name = "shop"\ndeveloper_mode = false\nadmin_tools = false\nenvironment = "prod"\n\n[sites."shop.localhost"]\n'
    )
    with patch("frappe_manager.utils.callbacks.CLI_BENCHES_DIRECTORY", root):
        result = runner.invoke(_app("create", create), ["shop/b.example.com"])
    assert "not found" not in _said(result).lower()
    assert "already serves" not in _said(result)


def test_create_refuses_a_site_the_bench_already_serves(tmp_path):
    """Adding a site that is already there would either fail deep inside `new-site` or, worse,
    re-run it against the existing schema."""
    root = tmp_path / "sites"
    (root / "shop").mkdir(parents=True)
    (root / "shop" / "bench_config.toml").write_text(
        'name = "shop"\ndeveloper_mode = false\nadmin_tools = false\nenvironment = "prod"\n\n[sites."shop.localhost"]\n'
    )
    with patch("frappe_manager.utils.callbacks.CLI_BENCHES_DIRECTORY", root):
        result = runner.invoke(_app("create", create), ["shop/shop.localhost"])
    assert result.exit_code == 2
    assert "already serves the site 'shop.localhost'" in _said(result)


def test_the_reserved_name_is_refused_before_the_localhost_suffix_is_added():
    """`validate_sitename` would turn `all` into `all.localhost` and the check would miss."""
    result = runner.invoke(_app("create", create), ["all"])
    assert "all.localhost" not in _said(result)


def test_create_still_accepts_an_ordinary_name(tmp_path):
    root = tmp_path / "sites"
    root.mkdir(parents=True)
    with patch("frappe_manager.utils.callbacks.CLI_BENCHES_DIRECTORY", root):
        result = runner.invoke(_app("create", create), ["shop"])
    assert "not a bench/site address" not in _said(result)
    assert "is reserved" not in _said(result)


# ------------------------------------------- the bench name is a name, not the site's domain


def test_create_keeps_the_bench_name_as_typed(tmp_path):
    """`fm create shop` makes a bench called `shop`, serving a site called `shop.localhost`. The
    name used to be normalised to the site's form here, so the bench WAS its site and the directory
    came out as `shop.localhost`."""
    from frappe_manager.utils.callbacks import create_command_sitename_callback

    root = tmp_path / "sites"
    root.mkdir(parents=True)
    with patch("frappe_manager.utils.callbacks.CLI_BENCHES_DIRECTORY", root):
        assert create_command_sitename_callback(_ctx(), "shop") == "shop"


def test_create_leaves_a_name_that_is_already_a_domain_alone(tmp_path):
    from frappe_manager.utils.callbacks import create_command_sitename_callback

    root = tmp_path / "sites"
    root.mkdir(parents=True)
    with patch("frappe_manager.utils.callbacks.CLI_BENCHES_DIRECTORY", root):
        assert create_command_sitename_callback(_ctx(), "a.example.com") == "a.example.com"


def test_create_refuses_a_name_whose_legacy_bench_already_exists(tmp_path):
    """A bench made before the names came apart is called `shop.localhost` and serves that site, so
    `fm create shop` would otherwise stand up a second bench beside it serving the same site."""
    from frappe_manager.utils.callbacks import create_command_sitename_callback

    root = tmp_path / "sites"
    (root / "shop.localhost").mkdir(parents=True)
    with (
        patch("frappe_manager.utils.callbacks.CLI_BENCHES_DIRECTORY", root),
        pytest.raises(typer.BadParameter, match="already exists"),
    ):
        create_command_sitename_callback(_ctx(), "shop")


# -------------------------------------------------------------------- fm shell


def test_shell_accepts_the_benchs_own_site(benches):
    """The one command that honours a site part. It gets past parsing; the body then fails
    on docker, which is not what this asserts."""
    result = runner.invoke(_app("shell", shell), [f"{BENCH}/{BENCH}"])
    assert "takes a bench, not a site" not in _said(result)
    assert "has no site" not in _said(result)


def test_shell_accepts_a_site_whose_name_is_not_the_benchs(tmp_path):
    """The shape after the decoupling: bench `shop` serving site `shop.localhost`. Validating the
    site against the BENCH NAME rather than against the recorded sites refused exactly this, the
    correct address, and said "its site is 'shop'"."""
    root = tmp_path / "sites"
    (root / "shop").mkdir(parents=True)
    (root / "shop" / "bench_config.toml").write_text(
        'name = "shop"\ndeveloper_mode = false\nadmin_tools = false\nenvironment = "prod"\n\n[sites."shop.localhost"]\n'
    )
    with patch("frappe_manager.utils.callbacks.CLI_BENCHES_DIRECTORY", root):
        result = runner.invoke(_app("shell", shell), ["shop/shop.localhost"])
    assert "has no site" not in _said(result)


def test_shell_accepts_either_site_of_a_multi_site_bench(tmp_path):
    """A bench serving several sites is addressable at each of them."""
    root = tmp_path / "sites"
    (root / "multi").mkdir(parents=True)
    (root / "multi" / "bench_config.toml").write_text(
        'name = "multi"\ndeveloper_mode = false\nadmin_tools = false\nenvironment = "prod"\n'
        '\n[sites."a.example.com"]\n\n[sites."b.example.com"]\n'
    )
    with patch("frappe_manager.utils.callbacks.CLI_BENCHES_DIRECTORY", root):
        for site in ("a.example.com", "b.example.com"):
            assert "has no site" not in _said(runner.invoke(_app("shell", shell), [f"multi/{site}"]))


def test_shell_refuses_a_site_the_bench_does_not_have(benches):
    """Checked against the sites the bench records, before any container is touched."""
    result = runner.invoke(_app("shell", shell), [f"{BENCH}/nope.localhost"])
    assert result.exit_code == 2
    assert f"bench '{BENCH}' has no site 'nope.localhost'" in _said(result)


def test_the_refusal_lists_the_sites_the_bench_does_serve(benches):
    """Naming what IS available is the difference between a typo the operator can fix and one they
    have to go and look up."""
    result = runner.invoke(_app("shell", shell), [f"{BENCH}/nope.localhost"])
    assert f"It serves '{BENCH}'" in _said(result)


def test_the_refusal_lists_every_site_of_a_multi_site_bench(tmp_path):
    root = tmp_path / "sites"
    (root / "multi").mkdir(parents=True)
    (root / "multi" / "bench_config.toml").write_text(
        'name = "multi"\ndeveloper_mode = false\nadmin_tools = false\nenvironment = "prod"\n'
        '\n[sites."a.example.com"]\n\n[sites."b.example.com"]\n'
    )
    with patch("frappe_manager.utils.callbacks.CLI_BENCHES_DIRECTORY", root):
        said = _said(runner.invoke(_app("shell", shell), ["multi/c.example.com"]))
    assert "'a.example.com'" in said
    assert "'b.example.com'" in said


def test_shell_has_no_site_option():
    """`--site` was replaced by the address; leaving both would be two ways to say one thing."""
    result = runner.invoke(_app("shell", shell), ["--help"])
    assert "--site" not in _said(result)


def test_shell_help_documents_the_address():
    result = runner.invoke(_app("shell", shell), ["--help"])
    assert "bench/site" in _said(result)
