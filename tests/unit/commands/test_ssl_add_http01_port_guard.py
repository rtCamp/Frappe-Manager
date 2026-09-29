"""Guard: `fm ssl add --challenge http01` (also the default) is refused when the proxy's published
HTTP port is not 80, because the CA always dials port 80 directly and there is no workaround.

`add_certificate`'s body is exercised directly with a MagicMock ctx, mirroring
test_ssl_add_custom_guards.py, so these tests pin guard order and messages without going through
click's argument parsing.
"""

from unittest.mock import MagicMock, patch

import pytest
import typer
from click.core import ParameterSource

from frappe_manager.commands.ssl.add import add_certificate
from frappe_manager.ssl_manager import LETSENCRYPT_PREFERRED_CHALLENGE

ADD_MODULE = "frappe_manager.commands.ssl.add"
BENCH = "mybench"
DOMAIN = "example.com"


def _ctx():
    ctx = MagicMock(name="ctx")
    ctx.obj = {"services": MagicMock(name="services_manager"), "domain": DOMAIN}
    ctx.get_parameter_source.return_value = ParameterSource.DEFAULT
    return ctx


def _fm_config(http_port: int):
    config = MagicMock(name="fm_config")
    config.proxy.http_port = http_port
    return config


@pytest.fixture
def add():
    """(output, issue, external_issue): the output handler add_certificate reports through, the
    patched _add_bench_certificate call, and the patched _add_external_certificate call -- both
    real work is mocked out so only the guard itself is under test."""
    with (
        patch(f"{ADD_MODULE}.get_output_handler") as get_output,
        patch(f"{ADD_MODULE}._add_bench_certificate") as issue,
        patch(f"{ADD_MODULE}._add_external_certificate") as external_issue,
        patch(f"{ADD_MODULE}.prompt_for_bench_selection", side_effect=lambda address: address),
        patch(f"{ADD_MODULE}._prompt_for_domain", return_value=DOMAIN),
        patch(f"{ADD_MODULE}._resolve_domains", return_value=[DOMAIN]),
        patch(f"{ADD_MODULE}.FMConfigManager") as fm_config_manager,
    ):
        output = MagicMock(name="output")
        get_output.return_value = output
        fm_config_manager.import_from_toml.return_value = _fm_config(80)
        yield output, issue, external_issue, fm_config_manager


def _errors(output) -> list[str]:
    return [c.args[0] for c in output.display_error.call_args_list]


def test_http01_is_refused_when_the_proxy_http_port_is_not_80(add):
    output, issue, _external_issue, fm_config_manager = add
    fm_config_manager.import_from_toml.return_value = _fm_config(8080)

    with pytest.raises(typer.Exit) as exc:
        add_certificate(_ctx(), address=BENCH, dev=False, custom=False)

    assert exc.value.exit_code == 1
    message = _errors(output)[0]
    assert "--challenge http01" in message
    assert "--challenge dns01" in message
    assert "--dev" in message
    assert "--custom" in message
    issue.assert_not_called()


def test_http01_is_permitted_when_the_proxy_http_port_is_80(add):
    output, issue, _external_issue, _fm_config_manager = add

    add_certificate(_ctx(), address=BENCH, dev=True)

    assert _errors(output) == []
    issue.assert_called_once()


def test_dns01_is_never_refused_regardless_of_the_port(add):
    output, issue, _external_issue, fm_config_manager = add
    fm_config_manager.import_from_toml.return_value = _fm_config(8080)

    add_certificate(_ctx(), address=BENCH, challenge=LETSENCRYPT_PREFERRED_CHALLENGE.dns01)

    assert _errors(output) == []
    issue.assert_called_once()


def test_dev_bypasses_the_http01_port_guard(add):
    output, issue, _external_issue, fm_config_manager = add
    fm_config_manager.import_from_toml.return_value = _fm_config(8080)

    add_certificate(_ctx(), address=BENCH, dev=True)

    assert _errors(output) == []
    issue.assert_called_once()


def test_custom_bypasses_the_http01_port_guard(add, tmp_path):
    output, issue, _external_issue, fm_config_manager = add
    fm_config_manager.import_from_toml.return_value = _fm_config(8080)
    cert = tmp_path / "a.crt"
    key = tmp_path / "a.key"
    cert.touch()
    key.touch()

    add_certificate(_ctx(), address=BENCH, custom=True, cert=cert, key=key)

    assert _errors(output) == []
    issue.assert_called_once()


def test_standalone_mode_is_also_refused_by_the_http01_port_guard(add):
    """The standalone port-80 challenge location matters more here: a standalone domain's
    certificate is usually the only reason the block exists (proxy-front-design.md #4.4)."""
    output, _issue, external_issue, fm_config_manager = add
    fm_config_manager.import_from_toml.return_value = _fm_config(8080)

    with pytest.raises(typer.Exit) as exc:
        add_certificate(_ctx(), address=DOMAIN, standalone=True)

    assert exc.value.exit_code == 1
    assert "--challenge http01" in _errors(output)[0]
    external_issue.assert_not_called()
