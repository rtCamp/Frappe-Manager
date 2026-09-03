"""Guards for `fm ssl add --behind-proxy` (alias `--edge-tls`).

A modifier on the existing certificate methods for an origin sitting behind an external TLS
terminator (e.g. Cloudflare Flexible: browser to edge over HTTPS, edge to origin over plain HTTP),
never a new `ssl_type`. Its one hard requirement is an explicit certificate method: the mode fixes
internal self-calls by keeping a locally trusted certificate on the origin's own :443, and which
method produces that certificate is not something fm can default for the operator.

Exercised through the real Typer app via CliRunner, not a mocked `ctx`: the "needs an explicit
method" guard reads `ctx.get_parameter_source("challenge")`, which only a genuine invocation sets
correctly for whether `--challenge` was actually typed on the command line (it always defaults to
http01 otherwise, so a mocked ctx would have to fake the exact mechanism under test).
"""

from contextlib import ExitStack
from unittest.mock import MagicMock, patch

import pytest
import typer
from typer.testing import CliRunner

from frappe_manager.commands.ssl.add import add_certificate

ADD_MODULE = "frappe_manager.commands.ssl.add"
BENCH_HELPERS = "frappe_manager.commands.ssl.bench_helpers"
BENCH = "mybench"
DOMAIN = "example.com"

runner = CliRunner()


def _app() -> typer.Typer:
    app = typer.Typer()
    app.command()(add_certificate)
    return app


def _invoke(args: list[str]):
    """`ctx.obj` must be a real dict, not Click's default `None`: `bench_domain_callback` stashes
    the DOMAIN half of `BENCH/DOMAIN` on it, which is how `add_certificate` learns the domain at
    all outside `--standalone`."""
    return runner.invoke(_app(), args, obj={"services": MagicMock(name="services_manager")})


def _said(result) -> str:
    """Click's own parse errors ('No such option', 'extra argument') land in `result.output`
    directly; fm's own refusals go through the mocked output handler instead (see `_errors()`)."""
    return " ".join(result.output.split())


@pytest.fixture
def adding():
    """A bench that serves exactly `DOMAIN`, with `_add_bench_certificate` mocked out so every
    test observes only whether the CLI layer would have reached it, and with what. The output
    handler is patched in both modules: `add_certificate`'s own guards (including every
    --behind-proxy one) report through `add.py`'s import of it, not bench_helpers'."""
    with ExitStack() as stack:
        bench = MagicMock(name="bench")
        bench.bench_config.domains = [DOMAIN]
        handler = MagicMock(name="output_handler")
        handler.prompt_fuzzy.side_effect = EOFError("not a terminal")
        stack.enter_context(patch(f"{BENCH_HELPERS}.Bench")).get_object.return_value = bench
        stack.enter_context(patch(f"{BENCH_HELPERS}.get_output_handler", return_value=handler))
        stack.enter_context(patch(f"{ADD_MODULE}.get_output_handler", return_value=handler))
        issue = stack.enter_context(patch(f"{ADD_MODULE}._add_bench_certificate"))
        yield issue, handler


def _errors(handler) -> str:
    return "\n".join(str(c) for c in handler.display_error.call_args_list)


class TestTheFlagIsReal:
    def test_behind_proxy_parses_as_a_real_option(self, adding):
        issue, handler = adding
        result = _invoke([f"{BENCH}/{DOMAIN}", "--behind-proxy", "--dev"])

        assert "No such option" not in _said(result)
        assert result.exit_code == 0, _errors(handler)
        assert issue.call_args.kwargs["behind_proxy"] is True

    def test_edge_tls_is_an_alias_for_the_same_option(self, adding):
        issue, handler = adding
        result = _invoke([f"{BENCH}/{DOMAIN}", "--edge-tls", "--dev"])

        assert "No such option" not in _said(result)
        assert result.exit_code == 0, _errors(handler)
        assert issue.call_args.kwargs["behind_proxy"] is True

    def test_omitting_the_flag_defaults_to_false(self, adding):
        issue, handler = adding
        result = _invoke([f"{BENCH}/{DOMAIN}", "--dev"])

        assert result.exit_code == 0, _errors(handler)
        assert issue.call_args.kwargs["behind_proxy"] is False


class TestRequiresAnExplicitCertificateMethod:
    def test_bare_behind_proxy_is_refused(self, adding):
        issue, handler = adding
        result = _invoke([f"{BENCH}/{DOMAIN}", "--behind-proxy"])

        assert result.exit_code == 1
        assert "needs an explicit certificate method" in _errors(handler)
        issue.assert_not_called()

    def test_the_refusal_names_every_accepted_method(self, adding):
        _issue, handler = adding
        _invoke([f"{BENCH}/{DOMAIN}", "--behind-proxy"])

        message = _errors(handler)
        assert "--dev" in message
        assert "--custom" in message
        assert "--challenge" in message

    def test_behind_proxy_with_dev_is_accepted(self, adding):
        issue, handler = adding
        result = _invoke([f"{BENCH}/{DOMAIN}", "--behind-proxy", "--dev"])

        assert result.exit_code == 0, _errors(handler)
        issue.assert_called_once()
        assert issue.call_args.kwargs["dev"] is True
        assert issue.call_args.kwargs["behind_proxy"] is True

    def test_behind_proxy_with_custom_is_accepted(self, adding, tmp_path):
        cert = tmp_path / "a.crt"
        key = tmp_path / "a.key"
        cert.touch()
        key.touch()
        issue, handler = adding

        result = _invoke([f"{BENCH}/{DOMAIN}", "--behind-proxy", "--custom", "--cert", str(cert), "--key", str(key)])

        assert result.exit_code == 0, _errors(handler)
        issue.assert_called_once()
        assert issue.call_args.kwargs["custom"] is True
        assert issue.call_args.kwargs["behind_proxy"] is True

    def test_behind_proxy_with_an_explicit_challenge_is_accepted(self, adding):
        """`--challenge` always defaults to http01, so this proves the guard reads the parameter
        SOURCE and not merely whether the value equals the default."""
        issue, handler = adding
        result = _invoke([f"{BENCH}/{DOMAIN}", "--behind-proxy", "--challenge", "http01"])

        assert result.exit_code == 0, _errors(handler)
        issue.assert_called_once()
        assert issue.call_args.kwargs["behind_proxy"] is True

    def test_behind_proxy_with_an_explicit_dns01_challenge_is_accepted(self, adding):
        issue, handler = adding
        result = _invoke([f"{BENCH}/{DOMAIN}", "--behind-proxy", "--challenge", "dns01"])

        assert result.exit_code == 0, _errors(handler)
        issue.assert_called_once()


class TestBenchModeOnly:
    def test_behind_proxy_with_standalone_is_refused(self, adding):
        issue, handler = adding
        result = _invoke([DOMAIN, "--standalone", "--behind-proxy"])

        assert result.exit_code == 1
        assert "--behind-proxy is bench mode only" in _errors(handler)
        issue.assert_not_called()


class TestCertificateCarriesTheMode:
    def test_the_flag_reaches_the_certificate_constructor_call(self, adding):
        """End-to-end through the CLI layer only; `test_ssl_bench_helpers_contract.py` pins that
        `_add_bench_certificate` itself sets `behind_proxy` on whichever certificate it builds."""
        issue, handler = adding
        result = _invoke([f"{BENCH}/{DOMAIN}", "--behind-proxy", "--dev"])

        assert result.exit_code == 0, _errors(handler)
        assert issue.call_args.args[0] is not None  # ctx
        assert issue.call_args.kwargs["behind_proxy"] is True
