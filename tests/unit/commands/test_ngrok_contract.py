"""What `fm ngrok` and the tunnel helper it calls actually DECIDE.

Three regressions are defended here:

* a tunnel that never comes up must FAIL the command. `create_tunnel` used to catch every
  exception, print it and return, so `fm ngrok` exited 0 on a bad token / no network / busy
  port and any supervisor or script wrapping it read the dead tunnel as success.
* `--auth-token <new> --save-token` must overwrite a token already stored in fm_config.toml.
  The save block was guarded by "and nothing is stored yet", so a replacement token was
  silently discarded -- no write, no prompt, no message.
* `fm ngrok` must run the bench-migration gate like every other bench-scoped command. The
  group callback only sees the bench name when it is typed on the command line, which is why
  each command re-checks; ngrok was the one that did not.

Nothing here touches the network, docker or stdin: the `ngrok` SDK object, `Bench` and
`create_tunnel` are all replaced at their seams.
"""

from importlib import import_module
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
import typer

from frappe_manager.commands.ngrok import ngrok
from frappe_manager.ngrok import NgrokTunnelError, create_tunnel
from frappe_manager.output_manager import get_global_output_handler

ngrok_helper = import_module("frappe_manager.ngrok")
# `frappe_manager.commands` re-exports the `ngrok` FUNCTION under the same name, shadowing the
# submodule attribute, so import_module is what actually resolves the module here.
ngrok_cmd = import_module("frappe_manager.commands.ngrok")

BENCH = "mybench"
OLD_TOKEN = "old-token"
NEW_TOKEN = "new-token"


# --------------------------------------------------------------------------- #
# create_tunnel
# --------------------------------------------------------------------------- #
def test_a_setup_failure_raises_once_instead_of_printing_it(monkeypatch):
    """It used to display_error AND re-raise, and the command caught it to display_error again,
    so the same raw text reached the terminal three times (the third as "Unexpected Error").
    One typed exception, rendered once by main.py's handler."""
    handler = get_global_output_handler()
    sdk = MagicMock(name="ngrok-sdk")
    sdk.set_auth_token.side_effect = RuntimeError("ERR_NGROK_105 authentication failed")
    monkeypatch.setattr(ngrok_helper, "ngrok", sdk)

    with (
        patch.object(handler, "display_error") as display_error,
        pytest.raises(NgrokTunnelError, match="ERR_NGROK_105"),
    ):
        create_tunnel(BENCH, "bad-token")

    display_error.assert_not_called()
    sdk.forward.assert_not_called()


def test_the_auth_token_never_reaches_the_error_message(monkeypatch):
    """ngrok echoes the token back inside its own error text ("Your authtoken: <value>"), and fm
    printed and logged that verbatim -- measured as three copies of a real token in fm.log.
    Redaction keyed on option names cannot see this: the secret is inside prose fm did not write."""
    sdk = MagicMock(name="ngrok-sdk")
    sdk.set_auth_token.side_effect = RuntimeError(
        "('failed to connect session', 'Your authtoken: 2abcSECRETxyz\\nsee dashboard', 'ERR_NGROK_105')"
    )
    monkeypatch.setattr(ngrok_helper, "ngrok", sdk)

    with pytest.raises(NgrokTunnelError) as excinfo:
        create_tunnel(BENCH, "2abcSECRETxyz")

    assert "2abcSECRETxyz" not in str(excinfo.value)
    assert "***" in str(excinfo.value)
    assert "ERR_NGROK_105" in str(excinfo.value)


def test_a_failure_from_the_forward_call_itself_is_typed_too(monkeypatch):
    """The auth token can be fine and the listener still never come up (port in use, no network)."""
    sdk = MagicMock(name="ngrok-sdk")
    sdk.forward.side_effect = OSError("address already in use")
    monkeypatch.setattr(ngrok_helper, "ngrok", sdk)

    with pytest.raises(NgrokTunnelError, match="address already in use"):
        create_tunnel(BENCH, "good-token")


# --------------------------------------------------------------------------- #
# the command
# --------------------------------------------------------------------------- #
def _config(stored: str | None) -> SimpleNamespace:
    return SimpleNamespace(ngrok_auth_token=stored, export_to_toml=MagicMock(name="export_to_toml"))


def _run_ngrok(config, answer: str = "no", gate=None, domain=None, served=None, **kwargs):
    handler = get_global_output_handler()
    ctx = MagicMock()
    ctx.obj = {"services": MagicMock(), "verbose": False, "fm_config_manager": config, "domain": domain}

    params = {"address": BENCH, "auth_token": None, "save_token": None}
    params.update(kwargs)

    bench = MagicMock(name="Bench")
    # bench, site and domain are one string today; a mock that sets only `name` hands a
    # MagicMock to any caller that correctly asks for the site or the domain.
    bench.name = BENCH
    bench.site_name = BENCH
    bench.primary_domain = BENCH
    bench.domains = [BENCH]
    # `bench_config.domains` is what the tunnel host is validated against now: the same list, read
    # from the config rather than the convenience property, so a two-site bench can be modelled.
    bench.bench_config.domains = list(served if served is not None else [BENCH])
    bench.running = True

    with (
        patch.object(ngrok_cmd, "Bench") as bench_cls,
        patch.object(ngrok_cmd, "create_tunnel") as tunnel,
        patch.object(ngrok_cmd, "check_bench_migration_required", gate or MagicMock()) as migration_gate,
        patch.object(handler, "prompt_ask", return_value=answer) as prompt_ask,
        patch.object(handler, "print"),
    ):
        bench_cls.get_object.return_value = bench
        try:
            ngrok(ctx, **params)
            raised = None
        except typer.Exit as exc:
            raised = exc

    return SimpleNamespace(
        exit=raised,
        tunnel=tunnel,
        gate=migration_gate,
        prompt=prompt_ask,
        bench_cls=bench_cls,
    )


def test_save_token_overwrites_an_already_stored_auth_token():
    config = _config(OLD_TOKEN)

    result = _run_ngrok(config, auth_token=NEW_TOKEN, save_token=True)

    assert result.exit is None
    assert config.ngrok_auth_token == NEW_TOKEN
    config.export_to_toml.assert_called_once_with()
    result.tunnel.assert_called_once_with(BENCH, NEW_TOKEN)


def test_a_replacement_token_prompts_when_no_save_flag_is_given():
    """Without --save-token/--no-save-token the user is asked -- previously the whole block,
    prompt included, was skipped whenever any token was already stored."""
    config = _config(OLD_TOKEN)

    result = _run_ngrok(config, auth_token=NEW_TOKEN, answer="yes")

    assert [call.kwargs["prompt"] for call in result.prompt.call_args_list] == [
        "Do you want to save the ngrok auth token in config for future use?",
    ]
    assert config.ngrok_auth_token == NEW_TOKEN
    config.export_to_toml.assert_called_once_with()


def test_declining_the_save_still_tunnels_with_the_supplied_token():
    config = _config(OLD_TOKEN)

    result = _run_ngrok(config, auth_token=NEW_TOKEN, save_token=False)

    assert config.ngrok_auth_token == OLD_TOKEN
    config.export_to_toml.assert_not_called()
    result.tunnel.assert_called_once_with(BENCH, NEW_TOKEN)


def test_re_supplying_the_stored_token_is_not_a_replacement():
    """Same token as the config already holds: nothing to save, so no prompt and no rewrite."""
    config = _config(OLD_TOKEN)

    result = _run_ngrok(config, auth_token=OLD_TOKEN, save_token=True)

    result.prompt.assert_not_called()
    config.export_to_toml.assert_not_called()
    result.tunnel.assert_called_once_with(BENCH, OLD_TOKEN)


def test_the_stored_token_is_used_without_prompting_when_none_is_supplied():
    config = _config(OLD_TOKEN)

    result = _run_ngrok(config)

    result.prompt.assert_not_called()
    config.export_to_toml.assert_not_called()
    result.tunnel.assert_called_once_with(BENCH, OLD_TOKEN)


def test_a_first_token_is_still_saved_when_nothing_is_stored():
    config = _config(None)

    result = _run_ngrok(config, auth_token=NEW_TOKEN, save_token=True)

    assert config.ngrok_auth_token == NEW_TOKEN
    config.export_to_toml.assert_called_once_with()
    result.tunnel.assert_called_once_with(BENCH, NEW_TOKEN)


def test_no_token_anywhere_refuses_the_command():
    config = _config(None)

    result = _run_ngrok(config)

    assert result.exit.exit_code == 1
    result.tunnel.assert_not_called()


# --------------------------------------------------------------------------- #
# the migration gate
# --------------------------------------------------------------------------- #
def test_ngrok_runs_the_bench_migration_gate():
    result = _run_ngrok(_config(OLD_TOKEN))

    result.gate.assert_called_once_with(BENCH)


def test_a_bench_that_needs_migration_never_reaches_the_tunnel():
    """The gate is the FIRST statement: a refused bench must not be resolved, started or tunnelled."""
    gate = MagicMock(side_effect=typer.Exit(1))

    result = _run_ngrok(_config(OLD_TOKEN), gate=gate)

    assert result.exit.exit_code == 1
    result.bench_cls.get_object.assert_not_called()
    result.tunnel.assert_not_called()


# ------------------------- which of the bench's hostnames the tunnel answers for


"""One tunnel rewrites the Host header to ONE hostname, so on a bench serving several sites it
reaches exactly one of them. It always reached the primary, whatever the operator asked for, while
the command's help said it exposed "a running bench"."""


def test_a_named_domain_is_the_host_the_tunnel_forwards_to():
    world = _run_ngrok(
        _config(OLD_TOKEN),
        domain="b.example.com",
        served=[BENCH, "b.example.com"],
    )

    assert world.exit is None
    world.tunnel.assert_called_once_with("b.example.com", OLD_TOKEN)


def test_a_bare_bench_still_tunnels_to_the_primary():
    """Unchanged for the single-site case, which is most benches."""
    world = _run_ngrok(_config(OLD_TOKEN), served=[BENCH, "b.example.com"])

    world.tunnel.assert_called_once_with(BENCH, OLD_TOKEN)


def test_a_domain_the_bench_does_not_serve_is_refused_by_name():
    """Naming what it does serve, because the operator's next move is to pick one of those."""
    world = _run_ngrok(
        _config(OLD_TOKEN),
        domain="elsewhere.example.com",
        served=[BENCH, "b.example.com"],
    )

    assert world.exit is not None
    assert world.exit.exit_code == 1
    world.tunnel.assert_not_called()


def test_an_alias_is_a_legal_tunnel_host():
    """The population is DOMAINS, not sites: an alias is a hostname the bench answers on, and
    exposing one is a legitimate thing to want."""
    world = _run_ngrok(
        _config(OLD_TOKEN),
        domain="www.b.example.com",
        served=[BENCH, "b.example.com", "www.b.example.com"],
    )

    assert world.exit is None
    world.tunnel.assert_called_once_with("www.b.example.com", OLD_TOKEN)


def test_a_bare_label_addresses_the_served_domain():
    """`BENCH/<name>` resolves the same way here as in `fm update`: exact first, then the
    `.localhost` form. This command matched verbatim, so `fm ngrok bench/shop` reported a domain
    the bench plainly serves as one it does not."""
    run = _run_ngrok(_config(None), domain="shop", served=["shop.localhost"], auth_token="t")

    run.tunnel.assert_called_once()
    assert run.tunnel.call_args.args[0] == "shop.localhost"


def test_a_domain_the_bench_does_not_serve_is_still_refused():
    """Resolution must not become a way to tunnel to something the bench never serves."""
    run = _run_ngrok(_config(None), domain="stranger", served=["shop.localhost"], auth_token="t")

    run.tunnel.assert_not_called()
    assert run.exit is not None
