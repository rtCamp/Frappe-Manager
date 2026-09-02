"""The `BENCH(/SITE)` positional, black-box through the CLI.

A bench holds exactly one site today, so `fm shell` is the only command that can do
anything with a site part and every other bench-scoped command refuses one. The `ssl`
commands are the exception, and a different one: their second segment is a served
DOMAIN rather than a site. These tests pin that split, and they pin it at the CLI
boundary rather than by calling the callbacks, because the exit code and the message
are the contract a user meets.

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
from frappe_manager.output_manager import get_global_output_handler
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


def test_an_ssl_command_reads_the_second_segment_as_a_domain(benches):
    """The `ssl` commands carry `bench_domain_callback`, not the site-refusing one: a certificate
    is keyed by domain, and a bench serves its sites' names AND their aliases. So the second
    segment is honoured, handed on through `ctx.obj`, and `fm ssl list` refuses it in its own body
    with its own reason rather than as a malformed address."""
    handler = get_global_output_handler()

    with patch.object(handler, "display_error") as display_error:
        result = runner.invoke(_app("list", list_certificates), [f"{BENCH}/shop.example.com"], obj={})

    assert result.exit_code == 1
    assert "takes a bench, not a single domain" in str(display_error.call_args)


def test_an_ssl_command_still_refuses_a_third_segment(benches):
    """Honouring a domain is not honouring anything: the address grammar is still two parts."""
    result = runner.invoke(_app("list", list_certificates), [f"{BENCH}/a/b"], obj={})

    assert result.exit_code == 2
    assert "more than one" in _said(result)


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
    """Case-insensitive on purpose: the help says `BENCH/SITE`, matching the usage line's metavar,
    and what this defends is that the address is mentioned at all rather than its capitalisation."""
    result = runner.invoke(_app("shell", shell), ["--help"])
    assert "bench/site" in _said(result).lower()


# ------------------------------- an exact site name is never retargeted


"""`BENCH/SITE` resolves the site the operator TYPED, before the `.localhost` convenience form.

`validate_sitename` appends `.localhost` to a bare label, which is what makes `fm create
shop/analytics` produce `analytics.localhost`. Applying it before the lookup meant a bench serving
BOTH `shop` and `shop.localhost` resolved `shop/shop` to `shop.localhost`: the recorded-site check
then passed, because that site does exist, and the command acted on a schema the operator had not
named. On `fm delete` that offered to drop the wrong database.

fm never creates a bare-label site, so this shape comes from a hand-written config or old data,
which is precisely when silently acting on a different schema is least excusable.
"""


def _recorded(root, bench, *sites):
    body = f'name = "{bench}"\ndeveloper_mode = false\nadmin_tools = false\nenvironment = "prod"\n'
    body += "".join(f'\n[sites."{s}"]\n' for s in sites)
    (root / bench).mkdir(parents=True, exist_ok=True)
    (root / bench / "bench_config.toml").write_text(body)


def test_a_bare_label_site_resolves_to_itself_not_to_the_fqdn_form(benches):
    from frappe_manager.utils.callbacks import bench_site_callback

    _recorded(benches, "shop", "shop", "shop.localhost")
    ctx = _ctx()

    assert bench_site_callback(ctx, "shop/shop") == "shop"
    assert ctx.obj["site"] == "shop"


def test_a_bare_label_still_gets_the_fqdn_form_when_only_that_is_recorded(benches):
    # The convenience that made `fm create shop/analytics` mean analytics.localhost survives.
    from frappe_manager.utils.callbacks import bench_site_callback

    _recorded(benches, "shop", "shop.localhost")
    ctx = _ctx()

    assert bench_site_callback(ctx, "shop/shop") == "shop"
    assert ctx.obj["site"] == "shop.localhost"


def test_a_site_recorded_under_neither_spelling_is_refused(benches):
    from frappe_manager.utils.callbacks import bench_site_callback

    _recorded(benches, "shop", "shop.localhost")
    ctx = _ctx()

    with pytest.raises(typer.BadParameter, match="has no site"):
        bench_site_callback(ctx, "shop/ghost.example.com")


def test_an_fqdn_site_is_unaffected(benches):
    from frappe_manager.utils.callbacks import bench_site_callback

    _recorded(benches, "shop", "shop.localhost", "b.example.com")
    ctx = _ctx()

    assert bench_site_callback(ctx, "shop/b.example.com") == "shop"
    assert ctx.obj["site"] == "b.example.com"


# ------------------------------- the usage line states the grammar


"""Every command that takes an address says WHICH address in its own usage line.

The metavar defaulted to the parameter name, so `fm shell --help` read
`Usage: fm shell [OPTIONS] [BENCHNAME]` and nothing in the help mentioned that `BENCH/SITE` was
accepted at all. Worse, one shared alias would have made `fm ssl list` advertise `(/DOMAIN)`,
which that command refuses, and `fm ssl add` advertise a bare `all`, which it also refuses.

Pinned here because nothing else catches it: a metavar is not a flag, so `docs-lint` does not see
it, and the commands keep working with the wrong one displayed.
"""

from frappe_manager.commands import app as fm_app


def _usage(argv: list[str]) -> str:
    """The one physical usage line, nothing else.

    Scoped deliberately: `BENCHNAME` still appears in help PROSE, where it names the command's own
    argument inside a sentence like "fm update BENCHNAME --runtime mount". What must not say it is
    the interface itself, and the docstring sits between the usage line and the arguments panel.
    """
    result = runner.invoke(fm_app, [*argv, "--help"])
    assert result.exit_code == 0, result.output
    for line in result.output.splitlines():
        if "Usage:" in line:
            return " ".join(line.split())
    raise AssertionError(f"no usage line in help for {argv}")


ADDRESS_GRAMMAR = [
    # bench only
    (["start"], "[BENCH]"),
    (["stop"], "[BENCH]"),
    (["restart"], "[BENCH]"),
    (["info"], "[BENCH]"),
    (["logs"], "[BENCH]"),
    (["bake"], "[BENCH]"),
    (["prune"], "BENCH"),
    # bench, or one of its sites
    (["create"], "BENCH(/SITE)"),
    (["shell"], "[BENCH(/SITE)]"),
    (["delete"], "[BENCH(/SITE)]"),
    (["reset"], "[BENCH(/SITE)]"),
    # --apps installs into a site's database, which can legitimately mean every site, so `update`
    # advertises the `all` form the other site-addressed commands refuse.
    (["update"], "[BENCH(/SITE|all)]"),
    # bench, or one hostname it serves
    (["ssl", "add"], "[BENCH(/DOMAIN)]"),
    (["ssl", "remove"], "[BENCH(/DOMAIN)]"),
    # the two that also take a bare `all`, and differ from each other
    (["ssl", "renew"], "[BENCH(/DOMAIN)|all]"),
    (["ssl", "list"], "[BENCH|all]"),
]


@pytest.mark.parametrize(("argv", "grammar"), ADDRESS_GRAMMAR, ids=lambda v: " ".join(v) if isinstance(v, list) else v)
def test_the_usage_line_states_the_address_grammar(argv, grammar):
    assert grammar in _usage(argv)


def test_no_command_still_advertises_the_parameter_name(benches):
    # `BENCHNAME` is the Python parameter leaking into the interface. It says "a bench name" and
    # nothing else, which is what hid the address form for every one of these.
    for argv, _ in ADDRESS_GRAMMAR:
        assert "BENCHNAME" not in _usage(argv), argv


def test_ssl_list_does_not_advertise_a_domain_it_refuses():
    # It reports every certificate a bench holds; naming one domain is refused in its body. A
    # usage line offering `/DOMAIN` would send the operator straight into that refusal.
    usage = _usage(["ssl", "list"])
    assert "/DOMAIN" not in usage


def test_ssl_add_does_not_advertise_a_bare_all_it_refuses():
    # Refused because a certificate per domain of every bench crosses Let's Encrypt's rate limit.
    usage = _usage(["ssl", "add"])
    assert "|all" not in usage


# ------------------------- an alias is a name worth recognising, not just rejecting


def _aliased_bench(tmp_path):
    """A bench whose site `b.example.com` also answers on `www.b.example.com`."""
    root = tmp_path / "sites"
    (root / "multi").mkdir(parents=True)
    (root / "multi" / "bench_config.toml").write_text(
        'name = "multi"\ndeveloper_mode = false\nadmin_tools = false\nenvironment = "prod"\n'
        '\n[sites."a.example.com"]\n'
        '\n[sites."b.example.com"]\nalias_domains = ["www.b.example.com"]\n'
    )
    return root


def test_an_alias_is_refused_with_the_site_it_belongs_to(tmp_path):
    """These commands act on a SITE, and an alias is another hostname for one, so it is a name an
    operator may well reach for. Listing every site and leaving them to work out which owns the
    alias is a dead end; naming it is a lesson.
    """
    root = _aliased_bench(tmp_path)
    with patch("frappe_manager.utils.callbacks.CLI_BENCHES_DIRECTORY", root):
        result = runner.invoke(_app("shell", shell), ["multi/www.b.example.com"])

    assert result.exit_code == 2
    said = _said(result)
    assert "is an alias of 'b.example.com'" in said
    assert "use 'multi/b.example.com'" in said


def test_a_name_that_is_neither_site_nor_alias_still_lists_the_sites(tmp_path):
    """The alias branch must not swallow the ordinary typo case."""
    root = _aliased_bench(tmp_path)
    with patch("frappe_manager.utils.callbacks.CLI_BENCHES_DIRECTORY", root):
        result = runner.invoke(_app("shell", shell), ["multi/nope.example.com"])

    said = _said(result)
    assert "alias" not in said
    assert "'a.example.com'" in said
    assert "'b.example.com'" in said


def test_the_alias_is_matched_as_typed_not_after_normalisation(tmp_path):
    """`validate_sitename` appends `.localhost` to a bare label, which would turn a bare-label alias
    into a name no config records and lose the match. The alias lookup sees what was typed."""
    root = tmp_path / "sites"
    (root / "multi").mkdir(parents=True)
    (root / "multi" / "bench_config.toml").write_text(
        'name = "multi"\ndeveloper_mode = false\nadmin_tools = false\nenvironment = "prod"\n'
        '\n[sites."b.example.com"]\nalias_domains = ["intranet"]\n'
    )
    with patch("frappe_manager.utils.callbacks.CLI_BENCHES_DIRECTORY", root):
        result = runner.invoke(_app("shell", shell), ["multi/intranet"])

    assert "is an alias of 'b.example.com'" in _said(result)


def test_maintenance_gets_the_same_alias_pointer(tmp_path):
    """The refusal lives in the shared address callback, so every command that acts on one site
    gains it: `fm maintenance shop/www.b.example.com` is exactly the invocation that prompted it."""
    from frappe_manager.commands.maintenance import maintenance

    root = _aliased_bench(tmp_path)
    with patch("frappe_manager.utils.callbacks.CLI_BENCHES_DIRECTORY", root):
        result = runner.invoke(_app("maintenance", maintenance), ["multi/www.b.example.com"])

    assert "is an alias of 'b.example.com'" in _said(result)
