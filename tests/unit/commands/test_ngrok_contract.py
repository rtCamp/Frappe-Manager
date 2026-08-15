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
from frappe_manager.ngrok import create_tunnel
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
def test_create_tunnel_propagates_a_forwarding_failure(monkeypatch):
    handler = get_global_output_handler()
    sdk = MagicMock(name="ngrok-sdk")
    sdk.set_auth_token.side_effect = RuntimeError("ERR_NGROK_105 authentication failed")
    monkeypatch.setattr(ngrok_helper, "ngrok", sdk)

    with (
        patch.object(handler, "display_error") as display_error,
        pytest.raises(RuntimeError, match="ERR_NGROK_105"),
    ):
        create_tunnel(BENCH, "bad-token")

    display_error.assert_called_once_with("Error creating tunnel: ERR_NGROK_105 authentication failed")
    sdk.forward.assert_not_called()


def test_create_tunnel_propagates_a_failure_from_the_forward_call_itself(monkeypatch):
    """The auth token can be fine and the listener still never come up (port in use, no network)."""
    handler = get_global_output_handler()
    sdk = MagicMock(name="ngrok-sdk")
    sdk.forward.side_effect = OSError("address already in use")
    monkeypatch.setattr(ngrok_helper, "ngrok", sdk)

    with patch.object(handler, "display_error"), pytest.raises(OSError, match="address already in use"):
        create_tunnel(BENCH, "good-token")


# --------------------------------------------------------------------------- #
# the command
# --------------------------------------------------------------------------- #
def _config(stored: str | None) -> SimpleNamespace:
    return SimpleNamespace(ngrok_auth_token=stored, export_to_toml=MagicMock(name="export_to_toml"))


def _run_ngrok(config, answer: str = "no", gate=None, **kwargs):
    handler = get_global_output_handler()
    ctx = MagicMock()
    ctx.obj = {"services": MagicMock(), "verbose": False, "fm_config_manager": config}

    params = {"benchname": BENCH, "auth_token": None, "save_token": None}
    params.update(kwargs)

    bench = MagicMock(name="Bench")
    bench.name = BENCH
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
