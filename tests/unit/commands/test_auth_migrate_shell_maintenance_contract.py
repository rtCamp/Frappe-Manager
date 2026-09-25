"""Characterization of the decisions inside `fm auth`, `fm migrate`, `fm shell`
and `fm maintenance`.

These commands carry their branch logic in the command body, so the things
worth defending are the guards that refuse an action, the state each flag
combination *resolves to*, the exact argv handed to the container, and the
ordering of side effects. In particular:

* `fm auth enable`/`fm auth disable` are ADDITIVE: naming a surface (`--web`/`--tools`) acts on
  that surface only, and naming neither acts on both. The two safety gates differ on purpose --
  the TLS gate fires only while a surface is newly turned on (an idempotent re-run must not start
  refusing), the nginx `$fm_upstream_auth` gate fires whenever the result leaves web protected.
* `migrate` decides its target set and per-bench success by comparing *base* versions so
  `0.19.0.dev0` counts as `0.19.0`. It is BENCH-tier only: a stale global-services tier is
  refused with a pointer at `fm services migrate`, never migrated implicitly.
* `shell` builds a compose argv and hands it to `os.execvp`. Tests pin the argv
  and the user/workdir decisions; `os.execvp` is always mocked, never run.
* `fm maintenance status` takes no bench and lists every domain currently in maintenance,
  across every bench.

Written as characterization tests: they pin what the code does today, including
the two oddities noted in the module docstrings of the tests concerned.
"""

import base64
from importlib import import_module
from io import StringIO
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
import typer
from rich.console import Console
from typer.testing import CliRunner

from frappe_manager.commands.auth._helpers import (
    print_state,
    read_password_from_stdin,
    surface_summary,
)
from frappe_manager.commands.auth.disable import disable as auth_disable
from frappe_manager.commands.auth.enable import enable as auth_enable
from frappe_manager.commands.auth.status import status as auth_status
from frappe_manager.commands.maintenance._helpers import (
    _DEFAULT_MESSAGE,
    _extract_bench,
    _extract_code,
    _extract_token,
    _resolve_page_html,
    _vhost_conf,
    optional_bench_site_callback,
)
from frappe_manager.commands.maintenance.disable import disable as maintenance_disable
from frappe_manager.commands.maintenance.enable import enable as maintenance_enable
from frappe_manager.commands.maintenance.status import status as maintenance_status
from frappe_manager.commands.migrate import MigrationFailureAction, migrate
from frappe_manager.commands.shell import (
    _get_default_shell_path,
    _get_default_user,
    _handle_bench_console,
    shell,
)
from frappe_manager.migration_manager.version import Version
from frappe_manager.output_manager import get_global_output_handler
from frappe_manager.site_manager.bench_config import AuthConfig, BenchRuntime, SiteConfig
from frappe_manager.site_manager.exceptions import BenchNotFoundError
from frappe_manager.ssl_manager import SUPPORTED_SSL_TYPES

# `frappe_manager.commands` re-exports the `migrate`/`shell` command FUNCTIONS under the module
# names, shadowing the submodule attributes, so import_module is the only way to reach those two
# modules themselves (needed to monkeypatch module-level constants). `auth` and `maintenance` are
# packages now, split into one module per verb: each symbol below is patched on the specific
# submodule that actually owns it. `check_bench_migration_required` and `Bench` are resolved
# inside `frappe_manager.commands.auth._helpers` for every auth verb (`resolve_scope` lives
# there), so that is the one patch target for auth regardless of which verb a test calls.
# `CLI_BENCHES_DIRECTORY` and `bench_site_callback` are resolved inside
# `frappe_manager.commands.maintenance._helpers` the same way; `secrets` is only imported into
# the `enable` verb module, which is the one that mints bypass tokens.
auth_helpers_mod = import_module("frappe_manager.commands.auth._helpers")
migrate_mod = import_module("frappe_manager.commands.migrate")
shell_mod = import_module("frappe_manager.commands.shell")
maint_helpers_mod = import_module("frappe_manager.commands.maintenance._helpers")
maint_enable_mod = import_module("frappe_manager.commands.maintenance.enable")

# `resolve_bench_targets` reads the benches root from `callbacks`, not from `migrate`, so a test
# that only redirects the command module's copy would have `all` enumerate the real ~/frappe.
callbacks_mod = import_module("frappe_manager.utils.callbacks")

COMPOSE = ["docker", "compose", "-f", "docker-compose.yml"]

# Credential fixtures kept out of the call sites so the linter does not read a
# literal keyword argument named "password" as a hardcoded secret.
PW = "pw"
OLD_PW = "old"
CHOSEN_PW = "chosen"
MINTED_PW = "minted"
STDIN_PW = "from-stdin"
STDIN_SENTINEL = "-"


# --------------------------------------------------------------------------- #
# shared fixtures
# --------------------------------------------------------------------------- #
@pytest.fixture
def out():
    """Record what the command reported.

    tests/unit/conftest.py installs a real RichOutputHandler globally; we patch
    the instance's sinks. `display_error` is the sink `error()` writes to before
    raising, so patching it keeps the real raise-the-exception behaviour.
    """
    handler = get_global_output_handler()
    with (
        patch.object(handler, "print") as p,
        patch.object(handler, "warning") as w,
        patch.object(handler, "display_error") as e,
        patch.object(handler, "print_data") as pd,
        patch.object(handler, "stop"),
    ):
        yield SimpleNamespace(print=p, warning=w, display_error=e, print_data=pd, handler=handler)


def texts(mock) -> list[str]:
    return [c.args[0] if c.args else c.kwargs.get("text", "") for c in mock.call_args_list]


def joined(mock) -> str:
    return "\n".join(texts(mock))


def render(table) -> str:
    console = Console(file=StringIO(), width=200, no_color=True, legacy_windows=False)
    console.print(table)
    return console.file.getvalue()

# =========================================================================== #
# auth.py
# =========================================================================== #
def _auth_bench(
    tmp_path,
    *,
    stored=None,
    ssl_type=SUPPORTED_SSL_TYPES.le,
    runtime=BenchRuntime.mount,
    nginx_conf: str | None = "map $remote_user $fm_upstream_auth { }",
    name="mybench",
    site_auth=None,
):
    bench = MagicMock()
    bench.name = name
    root = tmp_path / name
    root.mkdir(parents=True, exist_ok=True)
    bench.path = root
    if nginx_conf is not None:
        conf_dir = root / "configs" / "nginx" / "conf" / "conf.d"
        conf_dir.mkdir(parents=True, exist_ok=True)
        (conf_dir / "default.conf").write_text(nginx_conf)
    bench.bench_config.auth = stored
    bench.bench_config.runtime = runtime
    bench.bench_config.get_primary_certificate.return_value = SimpleNamespace(ssl_type=ssl_type)
    bench.bench_config.certificate_for.return_value = SimpleNamespace(ssl_type=ssl_type)
    # Modern bench by default; the older-conf tests flip it.
    bench.nginx_conf_serves_per_site.return_value = True
    # A real `[sites]` table, because the site path writes into the entry it finds there and
    # refuses when there is none.
    bench.bench_config.sites = {name: SiteConfig(), "b.example.com": SiteConfig(auth=site_auth)}
    bench.bench_config.auth_for = lambda s: (bench.bench_config.sites[s].auth or stored or AuthConfig())
    return bench


def _run_verb(verb, bench=None, **kwargs):
    """Call one of the real auth verb bodies with the bench lookup and migration gate mocked."""
    ctx = MagicMock()
    # `site` is the SITE half of the address, which `bench_site_callback` stashes here. None means
    # the whole bench, which is what a bare `fm auth enable BENCH` does.
    ctx.obj = {"services": MagicMock(), "site": kwargs.pop("site", None)}
    with (
        patch.object(auth_helpers_mod, "check_bench_migration_required") as gate,
        patch.object(auth_helpers_mod, "Bench") as bench_cls,
    ):
        bench_cls.get_object.return_value = bench if bench is not None else MagicMock()
        try:
            verb(ctx, **kwargs)
            raised = None
        except typer.Exit as exc:
            raised = exc
    return SimpleNamespace(bench_cls=bench_cls, gate=gate, exit=raised)


def _run_enable(bench=None, **kwargs):
    """Call the real `enable` body with the bench lookup and migration gate mocked."""
    params = {
        "address": "mybench",
        "web": False,
        "tools": False,
        "user": None,
        "password": None,
        "rotate": False,
        "allow_ip": [],
        "allow_path": [],
        "clear_exemptions": False,
        "insecure": False,
    }
    params.update(kwargs)
    return _run_verb(auth_enable, bench, **params)


def _run_disable(bench=None, **kwargs):
    """Call the real `disable` body with the bench lookup and migration gate mocked."""
    params = {"address": "mybench", "web": False, "tools": False}
    params.update(kwargs)
    return _run_verb(auth_disable, bench, **params)


def _run_status(bench=None, **kwargs):
    """Call the real `status` body with the bench lookup and migration gate mocked."""
    params = {"address": "mybench"}
    params.update(kwargs)
    return _run_verb(auth_status, bench, **params)


def _saved(bench) -> AuthConfig:
    """The AuthConfig the command assigned onto the bench config."""
    return bench.bench_config.auth


# --- flag guards refuse before the bench is even looked up ----------------- #
def test_rotate_with_explicit_password_is_refused(out):
    r = _run_enable(rotate=True, password=CHOSEN_PW)
    assert r.exit.exit_code == 1
    assert "--rotate cannot be combined with --password" in joined(out.display_error)


def test_bad_allow_ip_is_refused_with_the_flag_named(out):
    r = _run_enable(allow_ip=["not-an-ip"])
    assert r.exit.exit_code == 1
    assert "--allow-ip: invalid IP range 'not-an-ip'" in joined(out.display_error)


def test_relative_allow_path_is_refused(out):
    r = _run_enable(allow_path=["api/method/ping"])
    assert r.exit.exit_code == 1
    assert "--allow-path must be an absolute path prefix" in joined(out.display_error)


def test_allow_path_is_rejected_when_the_result_leaves_web_unprotected(out, tmp_path):
    # Path exemptions only exist on the web surface, so asking for one while the resulting state
    # protects tools only (--web left unnamed, so it stays at its current, unprotected value) is a
    # user error, not a silent no-op.
    bench = _auth_bench(tmp_path, stored=AuthConfig(web=False, tools=True, password=PW))
    r = _run_enable(bench, tools=True, allow_path=["/api/method/ping"])
    assert r.exit.exit_code == 1
    assert "web surface only" in joined(out.display_error)
    bench.save_bench_config.assert_not_called()


# --- reporting paths (status) ----------------------------------------------- #
def test_bare_status_on_an_unconfigured_bench_reports_the_model_defaults(out, tmp_path):
    bench = _auth_bench(tmp_path, stored=None)
    r = _run_status(bench)
    assert r.exit is None
    assert "Basic auth: not configured; bench defaults apply (tools protected, web open)" in texts(out.print)
    assert "fm auth enable mybench --web" in joined(out.print)
    bench.save_bench_config.assert_not_called()
    bench.ensure_fm_nginx_confs.assert_not_called()


def test_status_reports_stored_state_without_writing(out, tmp_path):
    stored = AuthConfig(user="alice", password=PW, web=True, tools=False, allow_ips=["10.0.0.0/8"])
    bench = _auth_bench(tmp_path, stored=stored)
    r = _run_status(bench)
    assert r.exit is None
    body = joined(out.print)
    assert "Basic auth on for: web" in body
    assert "user: alice" in body
    assert "no prompt from: 10.0.0.0/8" in body
    bench.save_bench_config.assert_not_called()


@pytest.mark.usefixtures("out")
def test_status_runs_the_migration_gate_and_looks_the_bench_up(tmp_path):
    bench = _auth_bench(tmp_path, stored=AuthConfig(password=PW))
    r = _run_status(bench)
    r.gate.assert_called_once_with("mybench")
    r.bench_cls.get_object.assert_called_once()


# --- surface_summary / print_state ------------------------------------------ #
def test_surface_summary_names_only_the_protected_surfaces_in_order():
    assert surface_summary(web=False, tools=False) == "off on both surfaces (web, tools)"
    assert surface_summary(web=True, tools=False) == "on for: web"
    assert surface_summary(web=False, tools=True) == "on for: tools"
    assert surface_summary(web=True, tools=True) == "on for: web, tools"


def test_print_state_hides_inert_credentials_when_both_surfaces_are_off():
    output = MagicMock()
    config = AuthConfig(user="alice", password=PW, web=False, tools=False, allow_ips=["10.0.0.1/32"])
    print_state(output, config, hint_when_off=True)
    body = joined(output.print)
    assert "off on both surfaces" in body
    assert "credentials and exemptions stay stored" in body
    assert "alice" not in body
    assert "pw" not in body


def test_print_state_omits_the_stored_hint_when_nothing_is_stored():
    output = MagicMock()
    print_state(output, AuthConfig(web=False, tools=False), hint_when_off=True)
    assert texts(output.print) == ["Basic auth off on both surfaces (web, tools)"]


def test_print_state_never_hints_after_a_write():
    output = MagicMock()
    config = AuthConfig(password=PW, web=False, tools=False)
    print_state(output, config, hint_when_off=False)
    assert "stay stored" not in joined(output.print)


def test_print_state_reports_path_exemptions_only_while_web_is_protected():
    tools_only = MagicMock()
    print_state(
        tools_only,
        AuthConfig(password=PW, web=False, tools=True, allow_paths=["/api/method/ping"]),
        hint_when_off=False,
    )
    assert "no prompt on:" not in joined(tools_only.print)

    web_on = MagicMock()
    print_state(
        web_on,
        AuthConfig(password=PW, web=True, tools=False, allow_paths=["/api/method/ping"]),
        hint_when_off=False,
    )
    assert "no prompt on: /api/method/ping" in joined(web_on.print)


# --- enable/disable are additive, not declarative --------------------------- #
@pytest.mark.usefixtures("out")
def test_naming_tools_alone_leaves_the_web_surface_on(tmp_path):
    """Additive, not declarative: naming --tools says nothing about --web, so an already-protected
    web surface stays protected instead of being turned off again."""
    bench = _auth_bench(tmp_path, stored=AuthConfig(web=True, tools=False, password=PW))
    r = _run_enable(bench, tools=True)
    assert r.exit is None
    assert (_saved(bench).web, _saved(bench).tools) == (True, True)


@pytest.mark.usefixtures("out")
def test_naming_web_alone_leaves_the_tools_surface_on(tmp_path):
    bench = _auth_bench(tmp_path, stored=AuthConfig(web=False, tools=True, password=PW))
    r = _run_enable(bench, web=True)
    assert r.exit is None
    assert (_saved(bench).web, _saved(bench).tools) == (True, True)


@pytest.mark.usefixtures("out")
def test_naming_neither_surface_enables_both(tmp_path):
    bench = _auth_bench(tmp_path, stored=AuthConfig(web=False, tools=False, password=PW))
    _run_enable(bench)
    assert (_saved(bench).web, _saved(bench).tools) == (True, True)


@pytest.mark.usefixtures("out")
def test_disabling_neither_named_turns_both_surfaces_off_but_keeps_the_credentials(tmp_path):
    stored = AuthConfig(user="alice", password=PW, web=True, tools=True, allow_ips=["10.0.0.0/8"])
    bench = _auth_bench(tmp_path, stored=stored)
    _run_disable(bench)
    applied = _saved(bench)
    assert (applied.web, applied.tools) == (False, False)
    assert (applied.user, applied.password) == ("alice", "pw")
    assert applied.allow_ips == ["10.0.0.0/8"]


@pytest.mark.usefixtures("out")
def test_a_credential_change_leaves_the_untouched_surface_alone(tmp_path):
    bench = _auth_bench(tmp_path, stored=AuthConfig(web=True, tools=False, password=PW))
    _run_enable(bench, web=True, user="alice")
    applied = _saved(bench)
    assert (applied.web, applied.tools) == (True, False)
    assert applied.user == "alice"


@pytest.mark.usefixtures("out")
def test_an_absent_auth_table_is_treated_as_the_model_defaults(tmp_path):
    # No [auth] table means the bench serves AuthConfig()'s defaults today (tools protected, web
    # open); naming --tools writes that default explicitly while --web, left unnamed, keeps its
    # default (open).
    bench = _auth_bench(tmp_path, stored=None)
    _run_enable(bench, tools=True, user="alice")
    applied = _saved(bench)
    assert (applied.web, applied.tools) == (False, True)


# --- TLS gate ---------------------------------------------------------------- #
def test_enabling_web_without_tls_is_refused(out, tmp_path):
    bench = _auth_bench(tmp_path, stored=AuthConfig(web=False, tools=False), ssl_type=SUPPORTED_SSL_TYPES.none)
    r = _run_enable(bench, web=True)
    assert r.exit.exit_code == 1
    body = joined(out.display_error)
    assert "has no TLS certificate" in body
    assert "fm ssl add mybench" in body
    bench.save_bench_config.assert_not_called()


def test_enabling_tools_without_tls_only_warns_and_proceeds(out, tmp_path):
    bench = _auth_bench(tmp_path, stored=AuthConfig(web=False, tools=False), ssl_type=SUPPORTED_SSL_TYPES.none)
    r = _run_enable(bench, tools=True)
    assert r.exit is None
    assert "admin tools credentials are effectively cleartext" in joined(out.warning)
    assert (_saved(bench).web, _saved(bench).tools) == (False, True)
    bench.save_bench_config.assert_called_once()


def test_insecure_lets_the_web_surface_on_without_tls(out, tmp_path):
    bench = _auth_bench(tmp_path, stored=AuthConfig(web=False, tools=False), ssl_type=SUPPORTED_SSL_TYPES.none)
    r = _run_enable(bench, web=True, insecure=True)
    assert r.exit is None
    assert _saved(bench).web is True
    out.warning.assert_not_called()


def test_the_tls_gate_never_fires_on_an_idempotent_re_run(out, tmp_path):
    # Already-on surfaces add no exposure, so re-running the same command on a
    # plain-http bench must not start refusing.
    bench = _auth_bench(
        tmp_path,
        stored=AuthConfig(web=True, tools=True, password=PW),
        ssl_type=SUPPORTED_SSL_TYPES.none,
    )
    r = _run_enable(bench, web=True, tools=True)
    assert r.exit is None
    out.display_error.assert_not_called()
    out.warning.assert_not_called()


@pytest.mark.usefixtures("out")
def test_the_tls_gate_is_not_consulted_at_all_when_nothing_is_enabled(tmp_path):
    bench = _auth_bench(tmp_path, stored=AuthConfig(web=True, tools=True, password=PW))
    _run_disable(bench)
    bench.bench_config.get_primary_certificate.assert_not_called()


# --- nginx $fm_upstream_auth capability gate --------------------------------- #
def test_a_stale_nginx_conf_blocks_web_auth_and_names_the_bake_remedy(out, tmp_path):
    bench = _auth_bench(tmp_path, stored=AuthConfig(password=PW), runtime=BenchRuntime.image, nginx_conf="server {}")
    r = _run_enable(bench, web=True)
    assert r.exit.exit_code == 1
    body = joined(out.display_error)
    assert "predates the Authorization-header fix" in body
    assert "fm bake mybench" in body
    assert "fm switch mybench" in body
    bench.save_bench_config.assert_not_called()


def test_a_stale_nginx_conf_on_a_mount_bench_names_the_migrate_remedy(out, tmp_path):
    bench = _auth_bench(tmp_path, stored=AuthConfig(password=PW), runtime=BenchRuntime.mount, nginx_conf="server {}")
    r = _run_enable(bench, web=True)
    assert r.exit.exit_code == 1
    body = joined(out.display_error)
    assert "fm migrate" in body
    assert "fm restart mybench --nginx --container" in body
    assert "fm bake" not in body


@pytest.mark.usefixtures("out")
def test_an_absent_nginx_conf_does_not_gate(tmp_path):
    # A missing conf is rendered fresh from the current image on next start.
    bench = _auth_bench(tmp_path, stored=AuthConfig(password=PW), nginx_conf=None)
    r = _run_enable(bench, web=True)
    assert r.exit is None
    assert _saved(bench).web is True


def test_the_nginx_gate_fires_even_when_web_was_already_on(out, tmp_path):
    # Unlike the TLS gate this is about the conf being able to serve web auth at
    # all, so an idempotent re-run over a stale conf is still refused.
    bench = _auth_bench(tmp_path, stored=AuthConfig(web=True, password=PW), nginx_conf="server {}")
    r = _run_enable(bench, web=True)
    assert r.exit.exit_code == 1
    assert "predates the Authorization-header fix" in joined(out.display_error)


@pytest.mark.usefixtures("out")
def test_the_nginx_gate_never_fires_for_the_tools_surface(tmp_path):
    bench = _auth_bench(tmp_path, stored=AuthConfig(web=False, password=PW), nginx_conf="server {}")
    r = _run_enable(bench, tools=True)
    assert r.exit is None
    assert (_saved(bench).web, _saved(bench).tools) == (False, True)


@pytest.mark.usefixtures("out")
def test_turning_auth_off_over_a_stale_conf_is_allowed(tmp_path):
    bench = _auth_bench(tmp_path, stored=AuthConfig(web=True, password=PW), nginx_conf="server {}")
    r = _run_disable(bench)
    assert r.exit is None
    assert (_saved(bench).web, _saved(bench).tools) == (False, False)


# --- credentials -------------------------------------------------------------- #
def test_password_dash_is_read_from_a_pipe_without_the_trailing_newline():
    with patch.object(auth_helpers_mod.sys, "stdin") as stdin:
        stdin.isatty.return_value = False
        stdin.readline.return_value = "piped-secret\r\n"
        assert read_password_from_stdin() == "piped-secret"


def test_password_dash_prompts_without_echo_on_a_terminal():
    with patch.object(auth_helpers_mod.sys, "stdin") as stdin, patch.object(auth_helpers_mod.typer, "prompt") as prompt:
        stdin.isatty.return_value = True
        prompt.return_value = "typed-secret"
        assert read_password_from_stdin() == "typed-secret"
    prompt.assert_called_once_with("Password", hide_input=True)


@pytest.mark.usefixtures("out")
def test_password_dash_is_resolved_from_stdin_before_the_bench_is_looked_up(tmp_path):
    bench = _auth_bench(tmp_path, stored=AuthConfig(web=False, tools=True, password=OLD_PW))
    with patch.object(auth_helpers_mod, "read_password_from_stdin", return_value=STDIN_PW) as reader:
        r = _run_enable(bench, password=STDIN_SENTINEL)
    assert r.exit is None
    reader.assert_called_once_with()
    assert _saved(bench).password == STDIN_PW


@pytest.mark.usefixtures("out")
def test_rotate_mints_a_new_password_and_keeps_the_surfaces_and_user(tmp_path):
    bench = _auth_bench(tmp_path, stored=AuthConfig(user="alice", password=OLD_PW, web=True, tools=False))
    with patch.object(auth_helpers_mod, "generate_password", return_value="fresh") as gen:
        r = _run_enable(bench, web=True, rotate=True)
    assert r.exit is None
    gen.assert_called_once_with()
    applied = _saved(bench)
    assert (applied.password, applied.user) == ("fresh", "alice")
    assert (applied.web, applied.tools) == (True, False)


@pytest.mark.usefixtures("out")
def test_the_first_enable_mints_a_password_when_none_is_stored(tmp_path):
    bench = _auth_bench(tmp_path, stored=AuthConfig(web=False, tools=False, password=None))
    with patch.object(auth_helpers_mod, "generate_password", return_value=MINTED_PW):
        _run_enable(bench, tools=True)
    assert _saved(bench).password == MINTED_PW


@pytest.mark.usefixtures("out")
def test_an_explicit_password_wins_over_minting(tmp_path):
    bench = _auth_bench(tmp_path, stored=AuthConfig(web=False, tools=False, password=None))
    with patch.object(auth_helpers_mod, "generate_password", return_value=MINTED_PW) as gen:
        _run_enable(bench, tools=True, password=CHOSEN_PW)
    gen.assert_not_called()
    assert _saved(bench).password == CHOSEN_PW


@pytest.mark.usefixtures("out")
def test_no_password_is_minted_when_disable_leaves_everything_off(tmp_path):
    # Turning surfaces off is never itself a reason to mint a password: only web_on, tools_on, or
    # an explicit credential flag mints one, and `disable` never touches credentials.
    bench = _auth_bench(tmp_path, stored=AuthConfig(web=False, tools=False, password=None))
    with patch.object(auth_helpers_mod, "generate_password", return_value=MINTED_PW) as gen:
        r = _run_disable(bench)
    assert r.exit is None
    gen.assert_not_called()
    assert _saved(bench).password is None


def test_invalid_credentials_are_refused_before_saving(out, tmp_path):
    bench = _auth_bench(tmp_path, stored=AuthConfig(web=False, tools=True, password=PW))
    r = _run_enable(bench, user="has:colon")
    assert r.exit.exit_code == 1
    assert "Invalid credentials: username cannot contain ':'" in joined(out.display_error)
    bench.save_bench_config.assert_not_called()


def test_no_such_warning_when_a_surface_is_protected(out, tmp_path):
    bench = _auth_bench(tmp_path, stored=AuthConfig(web=False, tools=True, password=PW))
    _run_enable(bench, user="alice")
    assert "nothing enforces them" not in joined(out.warning)


def test_protecting_tools_on_a_bench_without_admin_tools_says_nothing_enforces_it_yet(out, tmp_path):
    # D31: ensure_fm_nginx_confs computes needs_auth = web or (tools and admin_tools),
    # so on a bench whose admin tools are off nothing is written and nothing is
    # enforced -- but the command still saved the surface and printed the credentials,
    # reporting a protected surface that does not exist.
    bench = _auth_bench(tmp_path, stored=AuthConfig(web=False, tools=False, password=PW))
    bench.bench_config.admin_tools = False
    r = _run_enable(bench, tools=True)
    assert r.exit is None
    assert _saved(bench).tools is True
    body = joined(out.warning)
    assert "Admin tools are disabled on mybench" in body
    assert "fm tools enable mybench" in body


def test_no_admin_tools_warning_when_the_tools_locations_exist(out, tmp_path):
    bench = _auth_bench(tmp_path, stored=AuthConfig(web=False, tools=False, password=PW))
    bench.bench_config.admin_tools = True
    _run_enable(bench, tools=True)
    assert "Admin tools are disabled" not in joined(out.warning)


def test_no_admin_tools_warning_when_only_the_web_surface_is_protected(out, tmp_path):
    # The web surface is enforced by the server-context conf, which admin tools have
    # nothing to do with.
    bench = _auth_bench(tmp_path, stored=AuthConfig(web=False, tools=False, password=PW))
    bench.bench_config.admin_tools = False
    _run_enable(bench, web=True)
    assert "Admin tools are disabled" not in joined(out.warning)


# --- exemptions --------------------------------------------------------------- #
@pytest.mark.usefixtures("out")
def test_allow_ip_replaces_the_stored_list_and_is_normalised_to_cidr(tmp_path):
    bench = _auth_bench(tmp_path, stored=AuthConfig(web=False, tools=True, password=PW, allow_ips=["10.0.0.0/8"]))
    _run_enable(bench, allow_ip=["203.0.113.7"])
    assert _saved(bench).allow_ips == ["203.0.113.7/32"]


@pytest.mark.usefixtures("out")
def test_omitting_the_exemption_flags_keeps_the_stored_lists(tmp_path):
    stored = AuthConfig(web=True, tools=True, password=PW, allow_ips=["10.0.0.0/8"], allow_paths=["/assets"])
    bench = _auth_bench(tmp_path, stored=stored)
    _run_enable(bench, user="alice")
    applied = _saved(bench)
    assert applied.allow_ips == ["10.0.0.0/8"]
    assert applied.allow_paths == ["/assets"]


@pytest.mark.usefixtures("out")
def test_clear_exemptions_empties_both_lists(tmp_path):
    stored = AuthConfig(web=True, tools=True, password=PW, allow_ips=["10.0.0.0/8"], allow_paths=["/assets"])
    bench = _auth_bench(tmp_path, stored=stored)
    _run_enable(bench, clear_exemptions=True)
    applied = _saved(bench)
    assert applied.allow_ips == []
    assert applied.allow_paths == []


@pytest.mark.usefixtures("out")
def test_clear_exemptions_combined_with_allow_ip_replaces_ips_and_empties_paths(tmp_path):
    stored = AuthConfig(web=True, tools=True, password=PW, allow_ips=["10.0.0.0/8"], allow_paths=["/assets"])
    bench = _auth_bench(tmp_path, stored=stored)
    _run_enable(bench, clear_exemptions=True, allow_ip=["203.0.113.0/24"])
    applied = _saved(bench)
    assert applied.allow_ips == ["203.0.113.0/24"]
    assert applied.allow_paths == []


# --- side-effect ordering -------------------------------------------------- #
@pytest.mark.usefixtures("out")
def test_the_config_is_saved_before_nginx_is_reconciled(tmp_path):
    bench = _auth_bench(tmp_path, stored=AuthConfig(web=False, tools=True, password=PW))
    _run_enable(bench, user="alice")
    bench.save_bench_config.assert_called_once_with(print_message=False)
    bench.ensure_fm_nginx_confs.assert_called_once_with()
    names = [c[0] for c in bench.method_calls]
    assert names.index("save_bench_config") < names.index("ensure_fm_nginx_confs")


# =========================================================================== #
# migrate.py
# =========================================================================== #
def _bench_dir(root, name: str, *, with_config: bool = True):
    path = root / name
    path.mkdir(parents=True, exist_ok=True)
    if with_config:
        (path / "bench_config.toml").write_text(f'name = "{name}"\n')
    return path


def _run_migrate(
    benches_dir,
    *,
    system_version="0.19.0",
    current_version="0.19.0",
    bench_versions=None,
    migrate_benches=None,
    execute_result=True,
    **kwargs,
):
    """Call the real `migrate` body with the executor and the benches directory
    mocked out. Returns the executor class mock plus what was raised."""
    fm_config_manager = MagicMock()
    fm_config_manager.get_system_migration_version.return_value = Version(system_version)
    ctx = MagicMock()
    ctx.obj = {"fm_config_manager": fm_config_manager}

    params = {
        "address": None,
        "skip_backup": False,
        "skip_config_backup": False,
        "skip_db_backup": False,
        "exclude_bench": [],
        "yes": False,
        "dry_run": False,
        "rerun": False,
        "on_failure": None,
    }
    params.update(kwargs)

    versions = bench_versions or {}
    with (
        patch.object(migrate_mod, "CLI_BENCHES_DIRECTORY", benches_dir),
        # `all` is expanded by `resolve_bench_targets`, which reads the constant out of `callbacks`.
        patch.object(callbacks_mod, "CLI_BENCHES_DIRECTORY", benches_dir),
        patch.object(migrate_mod, "get_current_fm_version", return_value=current_version),
        patch.object(migrate_mod, "MigrationExecutor") as executor_cls,
        patch.object(migrate_mod, "set_bench_migration_version") as set_bench_version,
        patch.object(
            migrate_mod,
            "get_bench_migration_version",
            side_effect=lambda p: Version(versions.get(p.name, "0.18.0")),
        ),
        patch.object(migrate_mod, "spinner"),
    ):
        executor_cls.return_value.execute.return_value = execute_result
        executor_cls.return_value.migrate_benches = migrate_benches if migrate_benches is not None else {}
        try:
            migrate(ctx, **params)
            raised = None
        except typer.Exit as exc:
            raised = exc
    return SimpleNamespace(
        executor_cls=executor_cls,
        executor=executor_cls.return_value,
        set_bench_version=set_bench_version,
        fm_config_manager=fm_config_manager,
        exit=raised,
    )


def _executor_kwargs(r):
    return r.executor_cls.call_args.kwargs


def _migrate_cli(argv, benches_dir, monkeypatch, *, system_version="0.18.0", current_version="0.19.0"):
    """Invoke `migrate` through a runner so the ARGUMENT CALLBACK runs.

    Every other migrate test calls the body directly, which is the right level for the body's own
    decisions but skips the parameter callbacks entirely. The two tests below are about what the
    callback does with the value before the body ever sees it, so they need real parsing.
    """
    monkeypatch.setattr(callbacks_mod, "CLI_BENCHES_DIRECTORY", benches_dir)
    monkeypatch.setattr(migrate_mod, "CLI_BENCHES_DIRECTORY", benches_dir)

    fm_config_manager = MagicMock()
    fm_config_manager.get_system_migration_version.return_value = Version(system_version)

    app = typer.Typer()
    app.command()(migrate)

    with (
        patch.object(migrate_mod, "get_current_fm_version", return_value=current_version),
        patch.object(migrate_mod, "MigrationExecutor") as executor_cls,
        patch.object(migrate_mod, "set_bench_migration_version"),
        patch.object(migrate_mod, "spinner"),
    ):
        executor_cls.return_value.execute.return_value = True
        executor_cls.return_value.migrate_benches = {}
        result = CliRunner().invoke(app, argv, obj={"fm_config_manager": fm_config_manager})
    return result, executor_cls


@pytest.mark.usefixtures("out")
def test_all_is_accepted_in_the_benchname_slot_so_no_conflict_can_be_expressed(tmp_path, monkeypatch):
    """`all` IS the benchname now, which is why "every bench" and "one bench" cannot both be asked for.

    `--all-benches` was a flag, so it could be combined with a name and the body had to refuse the
    pair with "Cannot specify both <benchname> and --all-benches". One slot holds one value, so that
    guard has nothing left to guard. What has to hold instead is that the word survives the argument
    callback, which for every other value demands a bench directory of that name: there is no bench
    called `all` here, and `all` still reaches the body and selects both real benches.
    """
    benches_dir = tmp_path / "sites"
    _bench_dir(benches_dir, "a.localhost")
    _bench_dir(benches_dir, "b.localhost")

    result, executor_cls = _migrate_cli(["all"], benches_dir, monkeypatch, system_version="0.19.0")

    assert result.exception is None, result.exception
    assert sorted(executor_cls.call_args.kwargs["target_benches"]) == ["a.localhost", "b.localhost"]


@pytest.mark.usefixtures("out")
def test_a_missing_bench_is_refused_before_the_body_runs(tmp_path, monkeypatch):
    """The existence check left the body when `--all-benches` became the `all` address.

    It used to be an `output.display_error("Bench 'x' does not exist")` plus exit 1 inside `migrate`.
    `benchname` carries `bench_all_callback` now, so a typo is refused while the argument is being
    parsed: the body never runs and no executor is ever built.
    """
    benches_dir = tmp_path / "sites"
    _bench_dir(benches_dir, "real.localhost")

    result, executor_cls = _migrate_cli(["ghost.localhost"], benches_dir, monkeypatch)

    assert result.exit_code != 0
    assert isinstance(result.exception, BenchNotFoundError)
    assert "ghost.localhost" in str(result.exception)
    executor_cls.assert_not_called()


def test_exclude_bench_with_one_named_bench_is_refused(out, tmp_path):
    # Subtracting from a set of one is either a no-op or an empty migration; either way the operator
    # asked for something the command cannot honour, so it says so instead of guessing.
    _bench_dir(tmp_path, "a.localhost")
    r = _run_migrate(tmp_path, address="a.localhost", exclude_bench="b.localhost")
    assert r.exit.exit_code == 1
    assert "--exclude-bench only means something with 'all', which names every bench" in joined(out.display_error)
    r.executor_cls.assert_not_called()


def test_exclude_bench_with_no_bench_named_at_all_is_refused(out, tmp_path):
    # A bare `fm migrate` touches infrastructure and no bench, so there is nothing to exclude from.
    r = _run_migrate(tmp_path, exclude_bench="a.localhost")
    assert r.exit.exit_code == 1
    assert "--exclude-bench only means something with 'all', which names every bench" in joined(out.display_error)
    r.executor_cls.assert_not_called()


def test_nothing_to_do_when_no_bench_was_named_points_at_services_migrate(out, tmp_path):
    r = _run_migrate(tmp_path, system_version="0.19.0", current_version="0.19.0")
    assert r.exit.exit_code == 0
    assert (
        "✓ No benches to migrate. fm's global services & configuration are handled by fm services migrate."
        in texts(out.print)
    )
    r.executor_cls.assert_not_called()


@pytest.mark.usefixtures("out")
def test_rerun_reruns_benches_only_and_never_the_services_tier(tmp_path):
    """--rerun used to force the services-tier migration too; the split makes fm migrate
    bench-only, so a rerun re-applies bench steps and leaves the services tier to
    fm services migrate."""
    _bench_dir(tmp_path, "a.localhost")
    r = _run_migrate(tmp_path, address="a.localhost", system_version="0.19.0", current_version="0.19.0", rerun=True)
    assert r.exit is None
    assert _executor_kwargs(r)["migrate_global_services"] is False
    assert _executor_kwargs(r)["rerun"] is True
    r.fm_config_manager.set_system_migration_version.assert_not_called()


def test_a_stale_services_tier_is_refused_naming_the_command_that_fixes_it(out, tmp_path):
    """The services tier is a prerequisite, never an implicit side effect: migrating a bench
    over stale global services would migrate it into a world that does not exist yet (the
    v1.0.0 cutover renames the addresses benches dial)."""
    _bench_dir(tmp_path, "a.localhost")
    r = _run_migrate(tmp_path, address="a.localhost", system_version="0.18.0", current_version="0.19.0")
    assert r.exit.exit_code == 1
    assert "Run 'fm services migrate' first" in joined(out.display_error)
    r.executor_cls.assert_not_called()


@pytest.mark.usefixtures("out")
def test_all_collects_only_directories_holding_a_bench_config(tmp_path):
    # `all` expands through `_bench_names`, the one registry completion and the picker also read, so
    # the set it migrates is the set the shell offered. A directory with no config is not a bench.
    _bench_dir(tmp_path, "a.localhost")
    _bench_dir(tmp_path, "b.localhost")
    _bench_dir(tmp_path, "no-config.localhost", with_config=False)
    (tmp_path / "stray.txt").write_text("x")
    r = _run_migrate(tmp_path, address="all")
    assert sorted(_executor_kwargs(r)["target_benches"]) == ["a.localhost", "b.localhost"]


@pytest.mark.usefixtures("out")
def test_exclude_bench_is_repeatable_splits_commas_and_removes_targets(tmp_path):
    _bench_dir(tmp_path, "a.localhost")
    _bench_dir(tmp_path, "b.localhost")
    _bench_dir(tmp_path, "c.localhost")
    # one repeated flag, one comma-carrying value: both spellings land in the same list
    r = _run_migrate(tmp_path, address="all", exclude_bench=["a.localhost, c.localhost"])
    assert _executor_kwargs(r)["target_benches"] == ["b.localhost"]
    assert _executor_kwargs(r)["exclude_benches"] == ["a.localhost", "c.localhost"]


@pytest.mark.usefixtures("out")
def test_kind_scoped_backup_flags_reach_the_executor(tmp_path):
    """Skips are by KIND now (--skip-config-backup / --skip-db-backup), not by bench; the
    command's only job is to hand each one through unchanged."""
    _bench_dir(tmp_path, "a.localhost")
    r = _run_migrate(tmp_path, address="a.localhost", skip_config_backup=True, skip_db_backup=True)
    assert _executor_kwargs(r)["skip_config_backup"] is True
    assert _executor_kwargs(r)["skip_db_backup"] is True
    assert _executor_kwargs(r)["skip_backup"] is False


@pytest.mark.usefixtures("out")
def test_on_failure_defaults_to_prompt_and_is_passed_through_as_its_value(tmp_path):
    _bench_dir(tmp_path, "a.localhost")
    default = _run_migrate(tmp_path, address="a.localhost")
    assert _executor_kwargs(default)["on_failure"] == "prompt"

    chosen = _run_migrate(tmp_path, address="a.localhost", on_failure=MigrationFailureAction.rollback)
    assert _executor_kwargs(chosen)["on_failure"] == "rollback"


def test_a_failed_execute_aborts_before_any_version_is_stamped(out, tmp_path):
    _bench_dir(tmp_path, "a.localhost")
    r = _run_migrate(tmp_path, address="a.localhost", execute_result=False)
    assert r.exit.exit_code == 1
    r.set_bench_version.assert_not_called()
    r.fm_config_manager.set_system_migration_version.assert_not_called()
    out.print_data.assert_not_called()


def test_a_dev_release_counts_as_migrated_because_base_versions_are_compared(out, tmp_path):
    _bench_dir(tmp_path, "a.localhost")
    r = _run_migrate(
        tmp_path,
        address="a.localhost",
        current_version="0.19.0",
        bench_versions={"a.localhost": "0.18.0"},
        migrate_benches={"a.localhost": {"last_migration_version": Version("0.19.0.dev0"), "exception": None}},
    )
    assert r.exit is None
    r.set_bench_version.assert_called_once_with(tmp_path / "a.localhost", Version("0.19.0"))
    table = render(out.print_data.call_args.args[0])
    assert "a.localhost" in table
    assert "v0.18.0" in table


def test_a_bench_the_executor_never_touched_is_reported_as_skipped(out, tmp_path):
    _bench_dir(tmp_path, "a.localhost")
    r = _run_migrate(
        tmp_path,
        address="a.localhost",
        bench_versions={"a.localhost": "0.19.0"},
        migrate_benches={},
    )
    assert r.exit is None
    r.set_bench_version.assert_not_called()
    assert "already up to date" in render(out.print_data.call_args.args[0])


def test_a_bench_that_raised_is_reported_as_failed_and_never_stamped(out, tmp_path):
    _bench_dir(tmp_path, "a.localhost")
    r = _run_migrate(
        tmp_path,
        address="a.localhost",
        migrate_benches={
            "a.localhost": {"last_migration_version": Version("0.19.0"), "exception": RuntimeError("boom")}
        },
    )
    # `display_error` prints without raising, so the command used to fall off the end and exit 0
    # with a failed bench on screen. A per-bench failure is a command failure.
    assert r.exit.exit_code == 1
    r.set_bench_version.assert_not_called()
    assert "Migration failed" in render(out.print_data.call_args.args[0])
    assert "Check logs for details" in joined(out.display_error)


def test_all_exits_nonzero_when_only_some_benches_failed(out, tmp_path):
    """A partially failed fleet migration must not report success to a CI job checking $?.

    The total-executor-failure guard earlier in the body does not cover this: `execute()`
    returned True and the good bench really was migrated and stamped.
    """
    _bench_dir(tmp_path, "a.localhost")
    _bench_dir(tmp_path, "b.localhost")
    r = _run_migrate(
        tmp_path,
        address="all",
        current_version="0.19.0",
        migrate_benches={
            "a.localhost": {"last_migration_version": Version("0.19.0"), "exception": None},
            "b.localhost": {"last_migration_version": Version("0.19.0"), "exception": RuntimeError("boom")},
        },
    )
    assert r.exit.exit_code == 1
    r.set_bench_version.assert_called_once_with(tmp_path / "a.localhost", Version("0.19.0"))
    table = render(out.print_data.call_args.args[0])
    assert "Migration failed" in table


def test_all_exits_zero_when_every_bench_migrated(out, tmp_path):
    _bench_dir(tmp_path, "a.localhost")
    _bench_dir(tmp_path, "b.localhost")
    r = _run_migrate(
        tmp_path,
        address="all",
        current_version="0.19.0",
        migrate_benches={
            "a.localhost": {"last_migration_version": Version("0.19.0"), "exception": None},
            "b.localhost": {"last_migration_version": Version("0.19.0"), "exception": None},
        },
    )
    assert r.exit is None
    assert "Migration failed" not in render(out.print_data.call_args.args[0])


def test_a_bench_left_behind_at_an_older_version_is_neither_stamped_nor_flagged(out, tmp_path):
    # The executor ran it, no exception, but it did not reach the current
    # version: today that is silently reported as nothing at all.
    _bench_dir(tmp_path, "a.localhost")
    r = _run_migrate(
        tmp_path,
        address="a.localhost",
        current_version="0.19.0",
        migrate_benches={"a.localhost": {"last_migration_version": Version("0.18.0"), "exception": None}},
    )
    assert r.exit is None
    r.set_bench_version.assert_not_called()
    table = render(out.print_data.call_args.args[0])
    assert "a.localhost" not in table
    assert "Migration failed" not in table


@pytest.mark.usefixtures("out")
def test_a_bench_whose_config_vanished_mid_migration_is_not_stamped(tmp_path):
    _bench_dir(tmp_path, "a.localhost", with_config=False)
    r = _run_migrate(
        tmp_path,
        address="a.localhost",
        migrate_benches={"a.localhost": {"last_migration_version": Version("0.19.0"), "exception": None}},
    )
    assert r.exit is None
    r.set_bench_version.assert_not_called()


def test_the_infrastructure_row_is_hidden_when_a_bench_was_named_and_infra_is_current(out, tmp_path):
    _bench_dir(tmp_path, "a.localhost")
    r = _run_migrate(
        tmp_path,
        address="a.localhost",
        system_version="0.19.0",
        current_version="0.19.0",
        migrate_benches={"a.localhost": {"last_migration_version": Version("0.19.0"), "exception": None}},
    )
    assert r.exit is None
    table = render(out.print_data.call_args.args[0])
    assert "FM Infrastructure" not in table
    assert "a.localhost" in table
    r.fm_config_manager.set_system_migration_version.assert_not_called()


# =========================================================================== #
# shell.py
# =========================================================================== #
def test_default_user_is_frappe_only_for_the_frappe_service():
    assert _get_default_user("frappe", None) == "frappe"
    assert _get_default_user("frappe", "root") == "root"
    assert _get_default_user("nginx", None) is None
    assert _get_default_user("nginx", "nginx") == "nginx"


def test_default_shell_path_falls_back_to_sh_only_for_the_non_bash_services():
    assert _get_default_shell_path("frappe", None) == "/bin/bash"
    assert _get_default_shell_path("nginx", None) == "/bin/bash"
    assert _get_default_shell_path("redis-cache", None) == "sh"
    assert _get_default_shell_path("mailpit", None) == "sh"
    # An explicit shell always wins, even for a service without bash.
    assert _get_default_shell_path("redis-cache", "/bin/zsh") == "/bin/zsh"


def _shell_bench(tmp_path, *, runtime=BenchRuntime.mount, services=("frappe", "nginx", "mailpit")):
    bench = MagicMock()
    bench.name = "mybench"
    bench.path = tmp_path
    bench.bench_config.runtime = runtime
    bench.docker_client.compose.docker_compose_cmd = list(COMPOSE)
    bench.get_available_services.return_value = list(services)
    bench.execute_command.return_value = 0
    return bench


# --- bench console argv ---------------------------------------------------- #
def _console(bench, **kwargs):
    params = {
        "benchname": "mybench",
        "command": None,
        "site": None,
        "user": None,
        "run": False,
        "output": MagicMock(),
    }
    params.update(kwargs)
    with (
        patch.object(shell_mod.os, "execvp") as execvp,
        patch.object(shell_mod.sys, "stdin") as stdin,
    ):
        stdin.isatty.return_value = params.pop("_isatty", True)
        stdin.read.return_value = params.pop("_stdin_data", "")
        try:
            _handle_bench_console(bench, **params)
            raised = None
        except typer.Exit as exc:
            raised = exc
    return SimpleNamespace(execvp=execvp, exit=raised)


def test_interactive_bench_console_execs_compose_exec_with_user_and_workdir(tmp_path):
    bench = _shell_bench(tmp_path)
    r = _console(bench, user="frappe")
    argv = [
        *COMPOSE,
        "exec",
        "--user",
        "frappe",
        "--workdir",
        "/workspace/frappe-bench",
        "frappe",
        "bench",
        "--site",
        "mybench",
        "console",
    ]
    r.execvp.assert_called_once_with("docker", argv)


def test_bench_console_site_defaults_to_the_benchname_and_is_overridable(tmp_path):
    bench = _shell_bench(tmp_path)
    default = _console(bench, user="frappe")
    assert default.execvp.call_args.args[1][-2] == "mybench"

    explicit = _console(bench, user="frappe", site="other.localhost")
    assert explicit.execvp.call_args.args[1][-2] == "other.localhost"


def test_bench_console_without_a_user_omits_the_user_flag(tmp_path):
    bench = _shell_bench(tmp_path)
    r = _console(bench, user=None)
    argv = r.execvp.call_args.args[1]
    assert "--user" not in argv
    assert argv[:5] == [*COMPOSE, "exec"]
    assert argv[5:7] == ["--workdir", "/workspace/frappe-bench"]


def test_bench_console_with_run_uses_the_exec_entrypoint_and_drops_user_and_workdir(tmp_path):
    bench = _shell_bench(tmp_path)
    r = _console(bench, user="frappe", run=True)
    argv = [
        *COMPOSE,
        "run",
        "--rm",
        "--entrypoint",
        "/exec-entrypoint.sh",
        "frappe",
        "/bin/bash",
        "-c",
        "cd /workspace/frappe-bench && bench --site mybench console",
    ]
    r.execvp.assert_called_once_with("docker", argv)


def test_bench_console_with_a_command_runs_frappe_initialised_python_instead_of_exec(tmp_path):
    bench = _shell_bench(tmp_path)
    r = _console(bench, command="print(frappe.__version__)", site="site.localhost")
    r.execvp.assert_not_called()
    assert r.exit is None
    service, payload, user = bench.execute_command.call_args.args
    assert service == "frappe"
    assert user is None
    # No FRAPPE_SITE here, deliberately: the console embeds `frappe.init(site=...)` in the
    # payload (asserted below), which is explicit and outranks any environment variable.
    assert bench.execute_command.call_args.kwargs == {"use_run": False}
    encoded = payload.split("FM_EXEC_CODE='", 1)[1].split("'", 1)[0]
    code = base64.b64decode(encoded).decode()
    assert "frappe.init(site='site.localhost')" in code
    assert "frappe.connect()" in code
    assert "print(frappe.__version__)" in code
    assert "/workspace/frappe-bench/env/bin/python" in payload


def test_bench_console_reads_piped_python_from_stdin(tmp_path):
    bench = _shell_bench(tmp_path)
    r = _console(bench, _isatty=False, _stdin_data="import frappe\nprint(1)\n")
    r.execvp.assert_not_called()
    payload = bench.execute_command.call_args.args[1]
    encoded = payload.split("FM_EXEC_CODE='", 1)[1].split("'", 1)[0]
    assert "print(1)" in base64.b64decode(encoded).decode()


def test_bench_console_command_beats_piped_stdin(tmp_path):
    bench = _shell_bench(tmp_path)
    r = _console(bench, command="print('cmd')", _isatty=False, _stdin_data="print('piped')")
    payload = bench.execute_command.call_args.args[1]
    code = base64.b64decode(payload.split("FM_EXEC_CODE='", 1)[1].split("'", 1)[0]).decode()
    assert "print('cmd')" in code
    assert "piped" not in code
    assert r.exit is None


def test_bench_console_propagates_a_non_zero_exit_code(tmp_path):
    bench = _shell_bench(tmp_path)
    bench.execute_command.return_value = 42
    r = _console(bench, command="raise SystemExit(42)")
    assert r.exit.exit_code == 42


# --- shell command body ---------------------------------------------------- #
def _run_shell(bench, *, args=None, interactive=True, isatty=True, stdin_data="", site=None, **kwargs):
    ctx = MagicMock()
    ctx.obj = {"services": MagicMock()}
    # The site half of a `bench/site` address arrives on the context, not as a parameter:
    # `bench_site_callback` puts it there so command bodies keep receiving a bench name.
    if site is not None:
        ctx.obj["site"] = site
    ctx.args = list(args) if args else []
    params = {
        "address": "mybench",
        "command": None,
        "user": None,
        "service": "frappe",
        "shell_path": None,
        "run": False,
        "bench_console": False,
    }
    params.update(kwargs)
    handler = get_global_output_handler()
    with (
        patch.object(shell_mod, "check_bench_migration_required"),
        patch.object(shell_mod, "Bench") as bench_cls,
        patch.object(shell_mod.os, "execvp") as execvp,
        patch.object(shell_mod.sys, "stdin") as stdin,
        patch.object(handler, "is_interactive", return_value=interactive),
    ):
        bench_cls.get_object.return_value = bench
        stdin.isatty.return_value = isatty
        stdin.read.return_value = stdin_data
        try:
            shell(ctx, **params)
            raised = None
        except typer.Exit as exc:
            raised = exc
    return SimpleNamespace(execvp=execvp, exit=raised)


def test_an_unknown_service_is_refused_and_the_available_ones_listed(out, tmp_path):
    bench = _shell_bench(tmp_path)
    r = _run_shell(bench, service="postgres")
    assert r.exit.exit_code == 1
    assert "Service 'postgres' not found" in joined(out.display_error)
    assert "Available services: frappe, mailpit, nginx" in joined(out.print)
    bench.shell.assert_not_called()
    r.execvp.assert_not_called()


def test_bench_console_is_refused_for_any_service_but_frappe(out, tmp_path):
    bench = _shell_bench(tmp_path)
    r = _run_shell(bench, service="nginx", bench_console=True)
    assert r.exit.exit_code == 1
    assert "--bench-console only works with the frappe service" in joined(out.display_error)


def test_an_image_runtime_bench_warns_that_the_shell_is_ephemeral(out, tmp_path):
    bench = _shell_bench(tmp_path, runtime=BenchRuntime.image)
    _run_shell(bench)
    assert "Image-mode shell is ephemeral" in joined(out.warning)


def test_a_mount_runtime_bench_does_not_warn(out, tmp_path):
    bench = _shell_bench(tmp_path, runtime=BenchRuntime.mount)
    _run_shell(bench)
    out.warning.assert_not_called()


@pytest.mark.usefixtures("out")
def test_a_bare_shell_delegates_to_bench_shell_with_the_resolved_defaults(tmp_path):
    bench = _shell_bench(tmp_path)
    r = _run_shell(bench)
    assert r.exit is None
    bench.shell.assert_called_once_with("frappe", "frappe", shell_path="/bin/bash", use_run=False, site=None)
    r.execvp.assert_not_called()


@pytest.mark.usefixtures("out")
def test_a_bare_shell_on_a_non_bash_service_uses_sh_and_no_default_user(tmp_path):
    bench = _shell_bench(tmp_path)
    _run_shell(bench, service="mailpit")
    bench.shell.assert_called_once_with("mailpit", None, shell_path="sh", use_run=False, site=None)


@pytest.mark.usefixtures("out")
def test_piped_stdin_without_a_command_is_executed_verbatim(tmp_path):
    bench = _shell_bench(tmp_path)
    r = _run_shell(bench, isatty=False, stdin_data="ls -la\nbench --version\n")
    assert r.exit is None
    bench.execute_command.assert_called_once_with(
        "frappe", "ls -la\nbench --version\n", "frappe", shell_path="/bin/bash", use_run=False, site=None
    )
    r.execvp.assert_not_called()
    bench.shell.assert_not_called()


@pytest.mark.usefixtures("out")
def test_piped_stdin_propagates_a_non_zero_exit_code(tmp_path):
    bench = _shell_bench(tmp_path)
    bench.execute_command.return_value = 3
    r = _run_shell(bench, isatty=False, stdin_data="false\n")
    assert r.exit.exit_code == 3


@pytest.mark.usefixtures("out")
def test_interactive_passthrough_execs_compose_exec_with_user_and_frappe_workdir(tmp_path):
    bench = _shell_bench(tmp_path)
    r = _run_shell(bench, args=["bench", "migrate"])
    argv = [
        *COMPOSE,
        "exec",
        "--user",
        "frappe",
        "--workdir",
        "/workspace/frappe-bench",
        "frappe",
        "/bin/bash",
        "-c",
        "bench migrate",
    ]
    r.execvp.assert_called_once_with("docker", argv)
    bench.execute_command.assert_not_called()


@pytest.mark.usefixtures("out")
def test_passthrough_on_a_non_frappe_service_gets_no_workdir(tmp_path):
    bench = _shell_bench(tmp_path)
    r = _run_shell(bench, args=["nginx", "-t"], service="nginx", user="root")
    argv = [*COMPOSE, "exec", "--user", "root", "nginx", "/bin/bash", "-c", "nginx -t"]
    r.execvp.assert_called_once_with("docker", argv)


@pytest.mark.usefixtures("out")
def test_passthrough_with_run_uses_the_exec_entrypoint_and_the_bench_workdir(tmp_path):
    """--run pinned no --workdir, which is what made `fm shell <bench> --run bench ...`
    fail on a mount-runtime bench: /exec-entrypoint.sh never cds and the image's
    WORKDIR is /workspace, one level above the bench. The flag is now passed exactly
    as the exec branch passes it; --user still cannot be (see the warning test below)."""
    bench = _shell_bench(tmp_path)
    r = _run_shell(bench, args=["bench", "migrate"], run=True)
    argv = [
        *COMPOSE,
        "run",
        "--rm",
        "--entrypoint",
        "/exec-entrypoint.sh",
        "--workdir",
        "/workspace/frappe-bench",
        "frappe",
        "/bin/bash",
        "-c",
        "bench migrate",
    ]
    r.execvp.assert_called_once_with("docker", argv)


@pytest.mark.usefixtures("out")
def test_passthrough_with_run_on_a_non_frappe_service_gets_no_workdir(tmp_path):
    bench = _shell_bench(tmp_path)
    r = _run_shell(bench, args=["nginx", "-t"], service="nginx", run=True)
    assert "--workdir" not in r.execvp.call_args.args[1]


def test_an_explicit_user_with_run_is_refused_out_loud_not_dropped_in_silence(out, tmp_path):
    """--run goes through /exec-entrypoint.sh, which gosu-drops to the bench's
    USERID:USERGROUP whatever user the container started as, so --user cannot be
    honoured here. It used to be dropped silently; forwarding it is not the fix
    (the frappe default would make a non-root `run --user` fail gosu outright)."""
    bench = _shell_bench(tmp_path)
    r = _run_shell(bench, args=["id", "-un"], run=True, user="root")
    assert "--user" not in r.execvp.call_args.args[1]
    assert "--user root is ignored with --run" in joined(out.warning)


@pytest.mark.usefixtures("out")
def test_the_default_frappe_user_does_not_trigger_the_run_warning(tmp_path):
    """Only a user the operator actually typed is worth a warning."""
    handler = get_global_output_handler()
    with patch.object(handler, "warning") as warning:
        _run_shell(bench=_shell_bench(tmp_path), args=["ls"], run=True)
    assert not any("ignored with --run" in str(call.args[0]) for call in warning.call_args_list)


@pytest.mark.usefixtures("out")
def test_non_interactive_passthrough_runs_the_command_instead_of_exec(tmp_path):
    bench = _shell_bench(tmp_path)
    r = _run_shell(bench, args=["bench", "migrate"], interactive=False)
    r.execvp.assert_not_called()
    bench.execute_command.assert_called_once_with(
        "frappe", "bench migrate", "frappe", shell_path="/bin/bash", use_run=False, site=None
    )
    assert r.exit is None


@pytest.mark.usefixtures("out")
def test_non_interactive_passthrough_propagates_a_non_zero_exit_code(tmp_path):
    bench = _shell_bench(tmp_path)
    bench.execute_command.return_value = 7
    r = _run_shell(bench, args=["false"], interactive=False)
    assert r.exit.exit_code == 7


@pytest.mark.usefixtures("out")
def test_passthrough_wins_over_piped_stdin(tmp_path):
    bench = _shell_bench(tmp_path)
    r = _run_shell(bench, args=["bench", "migrate"], isatty=False, stdin_data="ls\n")
    r.execvp.assert_called_once()
    bench.execute_command.assert_not_called()
    assert "ls" not in " ".join(r.execvp.call_args.args[1])


@pytest.mark.usefixtures("out")
def test_dash_c_command_is_executed_without_exec(tmp_path):
    bench = _shell_bench(tmp_path)
    r = _run_shell(bench, command="bench --version")
    r.execvp.assert_not_called()
    bench.execute_command.assert_called_once_with(
        "frappe", "bench --version", "frappe", shell_path="/bin/bash", use_run=False, site=None
    )
    bench.shell.assert_not_called()
    assert r.exit is None


# --- the site half of the address reaches every exec path ------------------- #
# `fm shell` has four ways to reach a container and only one of them is the interactive
# shell. The address exists so a bare `bench` command targets the named site, so a path
# that drops the site is a silent wrong-database bug -- the exact class of thing the
# address was introduced to prevent. One test per path.
@pytest.mark.usefixtures("out")
def test_dash_c_carries_the_site(tmp_path):
    """The scripted form: `fm shell BENCH/SITE -c 'bench migrate'`."""
    bench = _shell_bench(tmp_path)
    _ = _run_shell(bench, command="bench migrate", site="a.localhost")
    assert bench.execute_command.call_args.kwargs["site"] == "a.localhost"


@pytest.mark.usefixtures("out")
def test_piped_stdin_carries_the_site(tmp_path):
    bench = _shell_bench(tmp_path)
    _ = _run_shell(bench, isatty=False, stdin_data="bench migrate\n", site="a.localhost")
    assert bench.execute_command.call_args.kwargs["site"] == "a.localhost"


@pytest.mark.usefixtures("out")
def test_non_interactive_passthrough_carries_the_site(tmp_path):
    bench = _shell_bench(tmp_path)
    _ = _run_shell(bench, args=["bench", "migrate"], interactive=False, site="a.localhost")
    assert bench.execute_command.call_args.kwargs["site"] == "a.localhost"


@pytest.mark.usefixtures("out")
def test_interactive_passthrough_execs_with_the_site_in_the_environment(tmp_path):
    """This path builds its own argv inline rather than going through the docker layer, so
    it needs its own check: the flag must land before the service name, or docker reads it
    as an argument to the containerised command instead of a flag to itself."""
    bench = _shell_bench(tmp_path)
    r = _run_shell(bench, args=["bench", "migrate"], site="a.localhost")
    argv = r.execvp.call_args.args[1]
    assert argv[argv.index("--env") + 1] == "FRAPPE_SITE=a.localhost"
    # The trailing four are the docker-side boundary; `frappe` also appears earlier as the
    # value of `--user`, so the service is located by position, not by searching for it.
    assert argv[-4:] == ["frappe", "/bin/bash", "-c", "bench migrate"]
    assert argv.index("--env") < len(argv) - 4


@pytest.mark.usefixtures("out")
def test_interactive_passthrough_carries_the_site_on_the_run_branch_too(tmp_path):
    """`--run` builds a second, byte-identical inline argv next to the exec one. Two copies
    of the same construction is exactly where a flag gets added to one and not the other."""
    bench = _shell_bench(tmp_path)
    r = _run_shell(bench, args=["bench", "migrate"], run=True, site="a.localhost")
    argv = r.execvp.call_args.args[1]
    assert "run" in argv
    assert argv[argv.index("--env") + 1] == "FRAPPE_SITE=a.localhost"
    assert argv[-4:] == ["frappe", "/bin/bash", "-c", "bench migrate"]
    assert argv.index("--env") < len(argv) - 4


@pytest.mark.usefixtures("out")
def test_the_bare_interactive_shell_carries_the_site(tmp_path):
    bench = _shell_bench(tmp_path)
    _ = _run_shell(bench, site="a.localhost")
    assert bench.shell.call_args.kwargs["site"] == "a.localhost"


@pytest.mark.usefixtures("out")
def test_dash_c_command_propagates_a_non_zero_exit_code(tmp_path):
    bench = _shell_bench(tmp_path)
    bench.execute_command.return_value = 1
    r = _run_shell(bench, command="false")
    assert r.exit.exit_code == 1


@pytest.mark.usefixtures("out")
def test_a_command_beats_piped_stdin(tmp_path):
    bench = _shell_bench(tmp_path)
    _run_shell(bench, command="bench --version", isatty=False, stdin_data="ls\n")
    assert bench.execute_command.call_args.args[1] == "bench --version"


@pytest.mark.usefixtures("out")
def test_bench_console_is_reached_before_any_plain_shell_branch(tmp_path):
    bench = _shell_bench(tmp_path)
    with patch.object(shell_mod, "_handle_bench_console") as handler:
        r = _run_shell(bench, bench_console=True, command="print(1)", site="s.localhost", run=True)
    assert r.exit is None
    handler.assert_called_once()
    args = handler.call_args.args
    assert args[1:6] == ("mybench", "print(1)", "s.localhost", "frappe", True)
    bench.shell.assert_not_called()


def test_a_command_with_passthrough_args_is_refused(out, tmp_path):
    # Before this guard the passthrough branch silently discarded -c.
    bench = _shell_bench(tmp_path)
    r = _run_shell(bench, command="ls", args=["bench", "migrate"])
    assert r.exit.exit_code == 1
    assert "-c cannot be combined with arguments after --" in joined(out.display_error)
    bench.execute_command.assert_not_called()
    r.execvp.assert_not_called()


def test_shell_path_with_bench_console_is_refused(out, tmp_path):
    # --shell-path was silently inert: the console branch returns before it is resolved.
    bench = _shell_bench(tmp_path)
    with patch.object(shell_mod, "_handle_bench_console") as handler:
        r = _run_shell(bench, bench_console=True, shell_path="/bin/zsh")
    assert r.exit.exit_code == 1
    assert "--shell-path has no effect with --bench-console" in joined(out.display_error)
    handler.assert_not_called()


# =========================================================================== #
# maintenance.py
# =========================================================================== #
def test_the_address_callback_lets_a_missing_bench_through_without_delegating():
    """`fm maintenance status` takes no bench and then lists every domain in maintenance, so a
    missing address must not be validated (and must not delegate to `bench_site_callback`) at
    all."""
    ctx = MagicMock()
    with patch.object(maint_helpers_mod, "bench_site_callback") as delegate:
        assert optional_bench_site_callback(ctx, None) is None
    delegate.assert_not_called()


def test_the_address_callback_validates_an_explicit_bench():
    """An explicit bench (or bench/site) still goes through the standard validation, so a bad
    site is still refused."""
    ctx = MagicMock()
    with patch.object(maint_helpers_mod, "bench_site_callback", return_value="mybench") as delegate:
        assert optional_bench_site_callback(ctx, "mybench") == "mybench"
    delegate.assert_called_once_with(ctx, "mybench")


def _maint_services(tmp_path):
    vhostd = tmp_path / "vhost.d"
    html = tmp_path / "html"
    services = MagicMock()
    services.proxy_storage.dirs.vhostd.host = str(vhostd)
    services.proxy_storage.dirs.html.host = str(html)
    services.proxy_storage.dirs.html.container = "/usr/share/nginx/html"
    return services, vhostd, html


def _write_bench_config(benches_dir, benchname: str, body: str = "") -> None:
    bench_dir = benches_dir / benchname
    bench_dir.mkdir(parents=True, exist_ok=True)
    (bench_dir / "bench_config.toml").write_text(f'name = "{benchname}"\n{body}')


def _run_maintenance_verb(verb, services, benches_dir, site=None, **kwargs):
    ctx = MagicMock()
    # `site` is the SITE half of the address, which `bench_site_callback` stashes here. None means
    # the whole bench, which is what a bare `fm maintenance enable BENCH` does.
    ctx.obj = {"services": services, "site": site}
    with patch.object(maint_helpers_mod, "CLI_BENCHES_DIRECTORY", benches_dir):
        try:
            verb(ctx, **kwargs)
            raised = None
        except typer.Exit as exc:
            raised = exc
    return SimpleNamespace(exit=raised)


def _run_maintenance_enable(services, benches_dir, site=None, **kwargs):
    params = {
        "address": "mybench",
        "response_code": 503,
        "retry_after": 300,
        "allow_ip": [],
        "allow_path": [],
        "message": None,
        "page": None,
        "rotate_token": False,
        # Confirmation only guards a bench NOT already serving the page; bypassed here since
        # these tests exercise the write itself, not the interactive confirmation gate.
        "yes": True,
    }
    params.update(kwargs)
    return _run_maintenance_verb(maintenance_enable, services, benches_dir, site, **params)


def _run_maintenance_disable(services, benches_dir, site=None, **kwargs):
    params = {"address": "mybench"}
    params.update(kwargs)
    return _run_maintenance_verb(maintenance_disable, services, benches_dir, site, **params)


def _run_maintenance_status(services, benches_dir, site=None, **kwargs):
    params = {"address": "mybench"}
    params.update(kwargs)
    return _run_maintenance_verb(maintenance_status, services, benches_dir, site, **params)


# --- global listing (no bench) -------------------------------------------- #
def test_no_bench_lists_every_domain_in_maintenance(out, tmp_path):
    services, vhostd, _ = _maint_services(tmp_path)
    vhostd.mkdir(parents=True)
    (vhostd / "a.localhost").write_text(
        _vhost_conf("bench-a", "a" * 32, "/html", 404, 300, [], [], secure_cookie=False)
    )
    (vhostd / "b.localhost").write_text("client_max_body_size 50m;\n")
    (vhostd / "sub").mkdir()
    r = _run_maintenance_status(services, tmp_path / "benches", address=None)
    assert r.exit is None
    body = joined(out.print)
    assert "a.localhost: maintenance ON (bench bench-a, code 404, bypass token " + "a" * 32 + ")" in body
    assert "b.localhost" not in body


def test_no_bench_and_nothing_in_maintenance_says_so(out, tmp_path):
    services, vhostd, _ = _maint_services(tmp_path)
    vhostd.mkdir(parents=True)
    r = _run_maintenance_status(services, tmp_path / "benches", address=None)
    assert r.exit is None
    assert texts(out.print) == ["No domain is in maintenance"]


def test_no_bench_with_a_missing_vhostd_directory_still_reports_cleanly(out, tmp_path):
    services, _, _ = _maint_services(tmp_path)
    r = _run_maintenance_status(services, tmp_path / "benches", address=None)
    assert r.exit is None
    assert texts(out.print) == ["No domain is in maintenance"]


# --- enable flag guards ----------------------------------------------------- #
@pytest.mark.parametrize("code", [399, 600, 200, 0])
def test_a_response_code_outside_the_error_range_is_refused(out, tmp_path, code):
    services, _, _ = _maint_services(tmp_path)
    r = _run_maintenance_enable(services, tmp_path / "benches", response_code=code)
    assert r.exit.exit_code == 1
    assert f"got {code}" in joined(out.display_error)


@pytest.mark.parametrize("code", [400, 599])
def test_the_error_range_boundaries_are_accepted(out, tmp_path, code):
    services, _, _ = _maint_services(tmp_path)
    benches = tmp_path / "benches"
    _write_bench_config(benches, "mybench")
    r = _run_maintenance_enable(services, benches, response_code=code)
    assert r.exit is None
    out.display_error.assert_not_called()


def test_message_with_page_is_refused(out, tmp_path):
    services, _, _ = _maint_services(tmp_path)
    page = tmp_path / "page.html"
    page.write_text("<html></html>")
    r = _run_maintenance_enable(services, tmp_path / "benches", message="down", page=page)
    assert r.exit.exit_code == 1
    assert "--message cannot be combined with --page" in joined(out.display_error)


def test_a_missing_page_file_is_refused(out, tmp_path):
    services, _, _ = _maint_services(tmp_path)
    r = _run_maintenance_enable(services, tmp_path / "benches", page=tmp_path / "nope.html")
    assert r.exit.exit_code == 1
    assert "--page file not found" in joined(out.display_error)


@pytest.mark.parametrize("bad_ip", ["203.0.113.0/24", "not-an-ip", ""])
def test_allow_ip_takes_single_addresses_only(out, tmp_path, bad_ip):
    services, _, _ = _maint_services(tmp_path)
    r = _run_maintenance_enable(services, tmp_path / "benches", allow_ip=[bad_ip])
    assert r.exit.exit_code == 1
    assert "CIDR ranges are not supported here" in joined(out.display_error)


@pytest.mark.parametrize("bad_path", ["api/method/ping", "/api/method/ping?x=1", "/api/*/ping", ""])
def test_allow_path_must_be_absolute_with_an_optional_trailing_star(out, tmp_path, bad_path):
    services, _, _ = _maint_services(tmp_path)
    r = _run_maintenance_enable(services, tmp_path / "benches", allow_path=[bad_path])
    assert r.exit.exit_code == 1
    assert "--allow-path must be an absolute path" in joined(out.display_error)


def test_valid_ipv6_and_starred_paths_pass_validation(out, tmp_path):
    services, _, _ = _maint_services(tmp_path)
    benches = tmp_path / "benches"
    _write_bench_config(benches, "mybench")
    r = _run_maintenance_enable(
        services,
        benches,
        allow_ip=["2001:db8::1"],
        allow_path=["/api/method/hook*"],
    )
    assert r.exit is None
    out.display_error.assert_not_called()


# --- status per domain ------------------------------------------------------ #
def test_status_reports_on_off_and_foreign_per_domain_without_reloading(out, tmp_path):
    services, vhostd, _ = _maint_services(tmp_path)
    benches = tmp_path / "benches"
    _write_bench_config(
        benches,
        "mybench",
        # Both aliases are alternates for the site `mybench`; aliases are recorded per site.
        '[sites."mybench"]\nalias_domains = ["alias.example.com", "plain.example.com"]\n'
        '\n[[ssl.certificates]]\ndomain = "mybench"\nssl_type = "letsencrypt"\n',
    )
    vhostd.mkdir(parents=True)
    (vhostd / "mybench").write_text(_vhost_conf("mybench", "b" * 32, "/html", 404, 300, [], [], secure_cookie=True))
    (vhostd / "alias.example.com").write_text("client_max_body_size 50m;\n")
    r = _run_maintenance_status(services, benches)
    assert r.exit is None
    assert texts(out.print) == [
        "mybench: maintenance ON (code 404, bypass: https://mybench/fm-bypass/" + "b" * 32 + ")",
        "alias.example.com: custom vhost config present (no fm maintenance block)",
        "plain.example.com: maintenance off",
    ]
    services.nginx_controller.reload.assert_not_called()


def test_status_uses_http_for_a_domain_without_its_own_certificate(out, tmp_path):
    services, vhostd, _ = _maint_services(tmp_path)
    benches = tmp_path / "benches"
    _write_bench_config(benches, "mybench")
    vhostd.mkdir(parents=True)
    (vhostd / "mybench").write_text(_vhost_conf("mybench", "c" * 32, "/html", 503, 300, [], [], secure_cookie=False))
    _run_maintenance_status(services, benches)
    assert "bypass: http://mybench/fm-bypass/" in joined(out.print)


# --- disable ----------------------------------------------------------------- #
def test_disable_when_nothing_is_enabled_reports_it_and_does_not_reload(out, tmp_path):
    services, vhostd, _ = _maint_services(tmp_path)
    benches = tmp_path / "benches"
    _write_bench_config(benches, "mybench")
    vhostd.mkdir(parents=True)
    r = _run_maintenance_disable(services, benches)
    assert r.exit is None
    assert texts(out.print) == ["Maintenance was not enabled"]
    services.nginx_controller.reload.assert_not_called()


def test_disable_removes_only_the_fm_block_and_keeps_foreign_directives(out, tmp_path):
    services, vhostd, _ = _maint_services(tmp_path)
    benches = tmp_path / "benches"
    _write_bench_config(benches, "mybench")
    vhostd.mkdir(parents=True)
    conf = vhostd / "mybench"
    conf.write_text(
        _vhost_conf("mybench", "d" * 32, "/html", 503, 300, [], [], secure_cookie=False) + "client_max_body_size 50m;\n"
    )
    r = _run_maintenance_disable(services, benches)
    assert r.exit is None
    assert conf.read_text() == "client_max_body_size 50m;\n"
    services.nginx_controller.reload.assert_called_once_with()
    assert "Maintenance disabled for: mybench" in joined(out.print)


@pytest.mark.usefixtures("out")
def test_disable_deletes_the_file_when_only_the_fm_block_was_in_it(tmp_path):
    services, vhostd, _ = _maint_services(tmp_path)
    benches = tmp_path / "benches"
    _write_bench_config(benches, "mybench")
    vhostd.mkdir(parents=True)
    conf = vhostd / "mybench"
    conf.write_text(_vhost_conf("mybench", "e" * 32, "/html", 503, 300, [], [], secure_cookie=False))
    _run_maintenance_disable(services, benches)
    assert not conf.exists()
    services.nginx_controller.reload.assert_called_once_with()


def test_disable_reloads_once_for_all_domains(out, tmp_path):
    services, vhostd, _ = _maint_services(tmp_path)
    benches = tmp_path / "benches"
    # `alias.example.com` is an alias of the site `mybench`.
    _write_bench_config(benches, "mybench", '[sites."mybench"]\nalias_domains = ["alias.example.com"]\n')
    vhostd.mkdir(parents=True)
    for domain in ("mybench", "alias.example.com"):
        (vhostd / domain).write_text(_vhost_conf("mybench", "f" * 32, "/html", 503, 300, [], [], secure_cookie=False))
    _run_maintenance_disable(services, benches)
    services.nginx_controller.reload.assert_called_once_with()
    assert "Maintenance disabled for: mybench, alias.example.com" in joined(out.print)


# --- enable ------------------------------------------------------------------- #
def test_enable_writes_the_page_and_block_for_every_domain_then_reloads(out, tmp_path):
    services, vhostd, html = _maint_services(tmp_path)
    benches = tmp_path / "benches"
    # `alias.example.com` is an alias of the site `mybench`.
    _write_bench_config(benches, "mybench", '[sites."mybench"]\nalias_domains = ["alias.example.com"]\n')
    r = _run_maintenance_enable(services, benches, response_code=404, allow_ip=["203.0.113.7"], allow_path=["/hook*"])
    assert r.exit is None
    assert (html / "fm-maintenance-mybench.html").is_file()
    for domain in ("mybench", "alias.example.com"):
        text = (vhostd / domain).read_text()
        assert "# fm:maintenance BEGIN (bench: mybench)" in text
        assert "return 404;" in text
        assert 'if ($remote_addr = "203.0.113.7")' in text
        assert 'if ($uri ~ "^/hook")' in text
    services.nginx_controller.reload.assert_called_once_with()
    body = joined(out.print)
    assert "Maintenance enabled for: mybench, alias.example.com (serving 404)" in body
    assert "Allowed IPs: 203.0.113.7" in body
    assert "Allowed paths: /hook*" in body
    assert "/fm-bypass/off" in body


@pytest.mark.usefixtures("out")
def test_enable_prepends_its_block_and_preserves_foreign_directives(tmp_path):
    services, vhostd, _ = _maint_services(tmp_path)
    benches = tmp_path / "benches"
    _write_bench_config(benches, "mybench")
    vhostd.mkdir(parents=True)
    (vhostd / "mybench").write_text("client_max_body_size 50m;\n")
    _run_maintenance_enable(services, benches)
    text = (vhostd / "mybench").read_text()
    assert text.startswith("# fm:maintenance BEGIN")
    assert text.endswith("client_max_body_size 50m;\n")
    assert text.count("# fm:maintenance BEGIN") == 1


def test_re_enabling_reuses_the_existing_bypass_token(out, tmp_path):
    services, vhostd, _ = _maint_services(tmp_path)
    benches = tmp_path / "benches"
    _write_bench_config(benches, "mybench")
    vhostd.mkdir(parents=True)
    token = "1" * 32
    (vhostd / "mybench").write_text(_vhost_conf("mybench", token, "/html", 503, 300, [], [], secure_cookie=False))
    _run_maintenance_enable(services, benches, response_code=404)
    text = (vhostd / "mybench").read_text()
    assert _extract_token(text) == token
    assert _extract_code(text) == 404
    assert f"/fm-bypass/{token}" in joined(out.print)


@pytest.mark.usefixtures("out")
def test_rotate_token_replaces_the_existing_token(tmp_path):
    services, vhostd, _ = _maint_services(tmp_path)
    benches = tmp_path / "benches"
    _write_bench_config(benches, "mybench")
    vhostd.mkdir(parents=True)
    token = "2" * 32
    (vhostd / "mybench").write_text(_vhost_conf("mybench", token, "/html", 503, 300, [], [], secure_cookie=False))
    _run_maintenance_enable(services, benches, rotate_token=True)
    assert _extract_token((vhostd / "mybench").read_text()) != token


@pytest.mark.usefixtures("out")
def test_a_fresh_enable_mints_a_token(tmp_path):
    services, vhostd, _ = _maint_services(tmp_path)
    benches = tmp_path / "benches"
    _write_bench_config(benches, "mybench")
    with patch.object(maint_enable_mod.secrets, "token_hex", return_value="3" * 32) as gen:
        _run_maintenance_enable(services, benches)
    gen.assert_called_once_with(16)
    assert _extract_token((vhostd / "mybench").read_text()) == "3" * 32


def test_the_bypass_cookie_gets_secure_only_on_the_domains_that_have_tls(out, tmp_path):
    services, vhostd, _ = _maint_services(tmp_path)
    benches = tmp_path / "benches"
    _write_bench_config(
        benches,
        "mybench",
        # `plain.example.com` is an alias of the site `mybench`; only `mybench` has a certificate.
        '[sites."mybench"]\nalias_domains = ["plain.example.com"]\n'
        '\n[[ssl.certificates]]\ndomain = "mybench"\nssl_type = "letsencrypt"\n',
    )
    _run_maintenance_enable(services, benches)
    assert "; Secure" in (vhostd / "mybench").read_text()
    assert "; Secure" not in (vhostd / "plain.example.com").read_text()
    assert "Bypass (sets a cookie so you see the real site): https://mybench/fm-bypass/" in joined(out.print)


def test_enable_omits_the_allow_list_lines_when_no_exemption_was_given(out, tmp_path):
    services, _, _ = _maint_services(tmp_path)
    benches = tmp_path / "benches"
    _write_bench_config(benches, "mybench")
    _run_maintenance_enable(services, benches)
    body = joined(out.print)
    assert "Allowed IPs" not in body
    assert "Allowed paths" not in body


# --- page resolution ---------------------------------------------------------- #
def test_page_resolution_order_is_page_then_message_then_bench_file_then_default(tmp_path, monkeypatch):
    monkeypatch.setattr(maint_helpers_mod, "CLI_BENCHES_DIRECTORY", tmp_path)
    bench_page = tmp_path / "mybench" / "configs" / "maintenance.html"
    bench_page.parent.mkdir(parents=True)
    bench_page.write_text("BENCH PAGE")
    explicit = tmp_path / "explicit.html"
    explicit.write_text("EXPLICIT PAGE")

    assert _resolve_page_html("mybench", explicit, None) == "EXPLICIT PAGE"
    assert "a message" in _resolve_page_html("mybench", None, "a message")
    assert _resolve_page_html("mybench", None, None) == "BENCH PAGE"

    bench_page.unlink()
    assert _DEFAULT_MESSAGE in _resolve_page_html("mybench", None, None)


def test_page_wins_over_message_in_the_resolver_even_though_the_command_refuses_both(tmp_path, monkeypatch):
    monkeypatch.setattr(maint_helpers_mod, "CLI_BENCHES_DIRECTORY", tmp_path)
    explicit = tmp_path / "explicit.html"
    explicit.write_text("EXPLICIT PAGE")
    assert _resolve_page_html("mybench", explicit, "ignored") == "EXPLICIT PAGE"


def test_a_custom_message_is_html_escaped(tmp_path, monkeypatch):
    monkeypatch.setattr(maint_helpers_mod, "CLI_BENCHES_DIRECTORY", tmp_path)
    html = _resolve_page_html("mybench", None, "<script>alert('x')</script>")
    assert "<script>" not in html
    assert "&lt;script&gt;" in html


@pytest.mark.usefixtures("out")
def test_the_bench_page_is_picked_up_by_the_enable_path(tmp_path):
    services, _, html_dir = _maint_services(tmp_path)
    benches = tmp_path / "benches"
    _write_bench_config(benches, "mybench")
    bench_page = benches / "mybench" / "configs" / "maintenance.html"
    bench_page.parent.mkdir(parents=True)
    bench_page.write_text("BENCH PAGE")
    _run_maintenance_enable(services, benches)
    assert (html_dir / "fm-maintenance-mybench.html").read_text() == "BENCH PAGE"


# --- conf readers and renderer ------------------------------------------------- #
@pytest.mark.usefixtures("out")
def test_the_conf_readers_fall_back_when_the_text_holds_nothing():
    assert _extract_token("nothing here") is None
    assert _extract_code("nothing here") == 503
    assert _extract_bench("nothing here") == "?"


def test_retry_after_zero_drops_the_header_entirely():
    with_header = _vhost_conf("b", "a" * 32, "/html", 503, 300, [], [], secure_cookie=False)
    without = _vhost_conf("b", "a" * 32, "/html", 503, 0, [], [], secure_cookie=False)
    assert "add_header Retry-After 300 always;" in with_header
    assert "Retry-After" not in without


def test_an_exact_allow_path_matches_by_equality_and_a_starred_one_by_prefix():
    exact = _vhost_conf("b", "a" * 32, "/html", 503, 300, [], ["/api/method/ping"], secure_cookie=False)
    prefix = _vhost_conf("b", "a" * 32, "/html", 503, 300, [], ["/api/method/ping*"], secure_cookie=False)
    assert "if ($uri = /api/method/ping) {" in exact
    assert '$uri ~ "^/api/method/ping"' not in exact
    assert 'if ($uri ~ "^/api/method/ping")' in prefix
    assert "if ($uri = /api/method/ping) {" not in prefix


def test_api_requests_are_routed_to_the_json_body_with_the_same_code():
    conf = _vhost_conf("b", "a" * 32, "/html", 404, 300, [], [], secure_cookie=False)
    assert 'if ($uri ~ ^/api/) {\n    set $fm_maintenance "${fm_maintenance}-api";\n}' in conf
    assert 'if ($fm_maintenance = "1-api") {\n    rewrite ^ /fm-maintenance-json last;\n}' in conf
    assert "return 404 '{\"message\":\"Service temporarily unavailable for maintenance\"}';" in conf


def test_the_bypass_url_and_the_page_path_carry_the_token_and_bench_name():
    conf = _vhost_conf("mybench", "9" * 32, "/usr/share/nginx/html", 503, 300, [], [], secure_cookie=False)
    assert "location = /fm-bypass/" + "9" * 32 + " {" in conf
    assert "try_files /fm-maintenance-mybench.html =502;" in conf
    assert "root /usr/share/nginx/html;" in conf

# --- the site half of the address ------------------------------------------ #
def test_tools_with_a_site_part_is_refused(out, tmp_path):
    """One Adminer and one Mailpit serve the whole bench, on every hostname it has.

    Protecting them for one site would leave the SAME tools reachable unprotected on its
    neighbours: one of two doors into the same room. Refused rather than silently applied
    bench-wide, which is the version an operator only finds out about when it matters."""
    bench = _auth_bench(tmp_path)
    r = _run_enable(bench, site="b.example.com", tools=True)
    assert r.exit.exit_code == 1
    assert "--tools cannot take a site part" in joined(out.display_error)
    bench.save_bench_config.assert_not_called()


def test_enabling_web_with_a_site_part_writes_the_sites_entry_not_the_benchs(out, tmp_path):
    bench = _auth_bench(tmp_path, stored=AuthConfig(web=False, tools=True, password="benchpass"))
    r = _run_enable(bench, site="b.example.com", web=True)
    assert r.exit is None
    # The bench's own table is untouched: its other sites keep serving exactly as before.
    assert bench.bench_config.auth.web is False
    assert bench.bench_config.sites["b.example.com"].auth.web is True


def test_a_site_gets_its_own_password_not_the_benchs(out, tmp_path):
    bench = _auth_bench(tmp_path, stored=AuthConfig(web=True, password="benchpass"))
    _run_enable(bench, site="b.example.com", web=True)
    # Per-site credentials are the point: a password handed out for one site must not open another.
    assert bench.bench_config.sites["b.example.com"].auth.password != "benchpass"


def test_an_unrecorded_site_is_refused_rather_than_stored_nowhere(out, tmp_path):
    bench = _auth_bench(tmp_path)
    bench.bench_config.sites = {"mybench": SiteConfig()}
    r = _run_enable(bench, site="b.example.com", web=True)
    assert r.exit.exit_code == 1
    assert "records no entry for site" in joined(out.display_error)
    bench.save_bench_config.assert_not_called()


def test_the_tls_gate_asks_about_the_named_site_not_the_primary(out, tmp_path):
    """Certificates are per hostname, so the primary having one says nothing about a neighbour.

    Answering from the primary would hand out a site's credentials in cleartext."""
    bench = _auth_bench(tmp_path, ssl_type=SUPPORTED_SSL_TYPES.le)
    bench.bench_config.certificate_for.return_value = SimpleNamespace(ssl_type=SUPPORTED_SSL_TYPES.none)
    r = _run_enable(bench, site="b.example.com", web=True)
    assert r.exit.exit_code == 1
    assert "b.example.com' has no TLS certificate" in joined(out.display_error)
    bench.bench_config.certificate_for.assert_called_once_with("b.example.com")


def test_disabling_a_site_part_leaves_the_benchs_tools_alone(out, tmp_path):
    bench = _auth_bench(tmp_path, stored=AuthConfig(web=True, tools=True, password="benchpass"))
    _run_disable(bench, site="b.example.com")
    # Disabling a site turns that site's prompt off. The tools surface is bench-wide, so it is not
    # a thing a site-scoped call may switch off.
    assert bench.bench_config.auth.tools is True
    assert bench.bench_config.sites["b.example.com"].auth.web is False


def test_a_bare_site_address_reports_that_it_inherits(out, tmp_path):
    bench = _auth_bench(tmp_path, stored=AuthConfig(web=True, password="benchpass"))
    r = _run_status(bench, site="b.example.com")
    assert r.exit is None
    # Not "unconfigured": the site IS protected, by the bench's setting. Saying where the answer
    # came from is what stops an operator turning it on twice.
    assert "inherited from bench" in joined(out.print)
    bench.save_bench_config.assert_not_called()


def test_a_site_scoped_write_is_refused_when_the_nginx_conf_predates_per_site_blocks(out, tmp_path):
    """The conf is rendered once at the nginx container's first boot, so a bench can be running this
    code against one that includes only `custom/*.conf`.

    Recording the override there would be silent: nginx reads none of it, the site keeps following
    the bench, and --status reports a prompt nobody serves."""
    bench = _auth_bench(tmp_path)
    bench.nginx_conf_serves_per_site.return_value = False
    r = _run_enable(bench, site="b.example.com", web=True)
    assert r.exit.exit_code == 1
    assert "predates one server block per site" in joined(out.display_error)
    bench.save_bench_config.assert_not_called()


def test_reading_a_site_is_still_allowed_on_an_older_conf(out, tmp_path):
    """Only writes are refused: reporting what a site currently serves is always safe, and it is how
    an operator finds out the bench needs its nginx image updated."""
    bench = _auth_bench(tmp_path, stored=AuthConfig(web=True, password="bp"))
    bench.nginx_conf_serves_per_site.return_value = False
    r = _run_status(bench, site="b.example.com")
    assert r.exit is None
    assert "inherited from bench" in joined(out.print)


# --- what the report says about credentials nothing is enforcing ------------ #
# `credentials_touched` is an OR of three flags (`user`/`password`/`rotate`) that, combined with
# both surfaces being off, warns that saved credentials are inert. Bench-wide this warning is now
# unreachable through the CLI: `enable` always turns at least one surface on (naming neither turns
# both on), and `disable` never touches credentials, so the two are deleted rather than re-pinned.
# The site-scoped path stays reachable, since a site inherits the bench's surfaces without ever
# writing to them.
def test_a_write_does_not_repeat_the_stored_credentials_hint(out, tmp_path):
    """The hint belongs to a REPORT: it tells you the credentials survive a disable. Printing it
    again on the way out of the write that just turned them off is the command explaining its own
    input back."""
    bench = _auth_bench(tmp_path, stored=AuthConfig(web=True, tools=True, password="stored"))
    _run_disable(bench)
    assert "stay stored" not in joined(out.print)


def test_a_bare_site_address_says_the_inherited_credentials_are_inert(out, tmp_path):
    """The site inherits a bench whose surfaces are both off, so it serves no prompt. Without the
    hint the report reads as if the password shown were doing something."""
    bench = _auth_bench(tmp_path, stored=AuthConfig(web=False, tools=False, password="stored"))
    r = _run_status(bench, site="b.example.com")
    assert r.exit is None
    printed = joined(out.print)
    assert "inherited from bench" in printed
    assert "stay stored" in printed
