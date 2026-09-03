"""Guards for `fm ssl add --custom` (import a bring-your-own certificate).

`add_certificate`'s body is exercised directly with a MagicMock ctx, mirroring the harness in
test_ssl_all_selector_contract.py, so these tests pin guard ORDER and MESSAGES without going
through click's argument parsing (BenchDomainArgument's callback needs a real bench directory,
which is irrelevant to what is under test here: the flag-conflict guards that run before any bench
is ever resolved).
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


def _ctx(*, challenge_explicit=False):
    ctx = MagicMock(name="ctx")
    ctx.obj = {"services": MagicMock(name="services_manager"), "domain": DOMAIN}
    ctx.get_parameter_source.return_value = (
        ParameterSource.COMMANDLINE if challenge_explicit else ParameterSource.DEFAULT
    )
    return ctx


@pytest.fixture
def add():
    """(output, issue): the output handler add_certificate reports through, and the patched
    _add_bench_certificate call that would perform the real work."""
    with (
        patch(f"{ADD_MODULE}.get_output_handler") as get_output,
        patch(f"{ADD_MODULE}._add_bench_certificate") as issue,
        patch(f"{ADD_MODULE}.prompt_for_bench_selection", side_effect=lambda address: address),
        patch(f"{ADD_MODULE}._prompt_for_domain", return_value=DOMAIN),
        patch(f"{ADD_MODULE}._resolve_domains", return_value=[DOMAIN]),
    ):
        output = MagicMock(name="output")
        get_output.return_value = output
        yield output, issue


def _errors(output) -> list[str]:
    return [c.args[0] for c in output.display_error.call_args_list]


class TestCustomMutualExclusivity:
    def test_custom_with_dev_is_refused(self, add):
        output, issue = add
        with pytest.raises(typer.Exit) as exc:
            add_certificate(_ctx(), address=BENCH, custom=True, dev=True)

        assert exc.value.exit_code == 1
        assert _errors(output) == ["--custom cannot be used with --dev"]
        issue.assert_not_called()

    def test_custom_with_standalone_is_refused(self, add):
        output, issue = add
        with pytest.raises(typer.Exit):
            add_certificate(_ctx(), address=DOMAIN, custom=True, standalone=True)

        assert _errors(output) == ["--custom is bench mode only; --standalone is not supported yet"]
        issue.assert_not_called()

    def test_custom_with_dry_run_is_refused(self, add):
        output, issue = add
        with pytest.raises(typer.Exit):
            add_certificate(_ctx(), address=BENCH, custom=True, dry_run=True)

        assert _errors(output) == [
            "--custom cannot be used with --dry-run: there is no staging server to rehearse an import against"
        ]
        issue.assert_not_called()

    def test_custom_with_cname_is_refused(self, add):
        output, issue = add
        with pytest.raises(typer.Exit):
            add_certificate(_ctx(), address=BENCH, custom=True, cname="acme.example.net")

        assert _errors(output) == ["--cname is not applicable to --custom (there is no ACME challenge to delegate)"]
        issue.assert_not_called()

    def test_custom_with_dns_provider_is_refused(self, add):
        output, issue = add
        with pytest.raises(typer.Exit):
            add_certificate(_ctx(), address=BENCH, custom=True, dns_provider="acct-b")

        assert _errors(output) == [
            "--dns-provider is not applicable to --custom (there is no ACME challenge to authenticate)"
        ]
        issue.assert_not_called()

    def test_custom_with_explicit_challenge_is_refused(self, add):
        """A bare `challenge is None` check cannot see this: --challenge always defaults to
        http01, so only the parameter source distinguishes 'typed on the command line' from
        'nothing was passed'."""
        output, issue = add
        with pytest.raises(typer.Exit):
            add_certificate(_ctx(challenge_explicit=True), address=BENCH, custom=True)

        assert _errors(output) == ["--challenge is not applicable to --custom: there is no ACME challenge to perform"]
        issue.assert_not_called()

    def test_custom_with_default_challenge_is_not_refused_by_the_challenge_guard(self, add, tmp_path):
        """The inverse: an UNTYPED --challenge (source=DEFAULT) must not trip the guard above."""
        output, issue = add
        cert = tmp_path / "a.crt"
        key = tmp_path / "a.key"
        cert.touch()
        key.touch()

        add_certificate(_ctx(challenge_explicit=False), address=BENCH, custom=True, cert=cert, key=key)

        assert "--challenge is not applicable to --custom" not in " ".join(_errors(output))


class TestCertKeyCaRequireCustom:
    @pytest.mark.parametrize("flag", ["cert", "key", "ca"])
    def test_each_flag_without_custom_is_refused(self, add, tmp_path, flag):
        output, issue = add
        path = tmp_path / "a.pem"
        path.touch()
        with pytest.raises(typer.Exit):
            add_certificate(_ctx(), address=BENCH, **{flag: path})

        assert _errors(output) == ["--cert/--key/--ca require --custom"]
        issue.assert_not_called()

    def test_custom_without_cert_is_refused(self, add, tmp_path):
        output, issue = add
        with pytest.raises(typer.Exit):
            add_certificate(_ctx(), address=BENCH, custom=True, key=tmp_path / "a.key")

        assert _errors(output) == ["--custom requires both --cert and --key"]
        issue.assert_not_called()

    def test_custom_without_key_is_refused(self, add, tmp_path):
        output, issue = add
        with pytest.raises(typer.Exit):
            add_certificate(_ctx(), address=BENCH, custom=True, cert=tmp_path / "a.crt")

        assert _errors(output) == ["--custom requires both --cert and --key"]
        issue.assert_not_called()

    def test_custom_with_neither_is_refused(self, add):
        output, issue = add
        with pytest.raises(typer.Exit):
            add_certificate(_ctx(), address=BENCH, custom=True)

        assert _errors(output) == ["--custom requires both --cert and --key"]
        issue.assert_not_called()


class TestSourceFileExistence:
    def test_missing_cert_file_is_refused(self, add, tmp_path):
        output, issue = add
        key = tmp_path / "a.key"
        key.touch()
        missing = tmp_path / "missing.crt"

        with pytest.raises(typer.Exit):
            add_certificate(_ctx(), address=BENCH, custom=True, cert=missing, key=key)

        assert _errors(output) == [f"--cert file not found: {missing}"]
        issue.assert_not_called()

    def test_missing_key_file_is_refused(self, add, tmp_path):
        output, issue = add
        cert = tmp_path / "a.crt"
        cert.touch()
        missing = tmp_path / "missing.key"

        with pytest.raises(typer.Exit):
            add_certificate(_ctx(), address=BENCH, custom=True, cert=cert, key=missing)

        assert _errors(output) == [f"--key file not found: {missing}"]
        issue.assert_not_called()

    def test_missing_ca_file_is_refused(self, add, tmp_path):
        output, issue = add
        cert = tmp_path / "a.crt"
        key = tmp_path / "a.key"
        cert.touch()
        key.touch()
        missing = tmp_path / "missing.ca"

        with pytest.raises(typer.Exit):
            add_certificate(_ctx(), address=BENCH, custom=True, cert=cert, key=key, ca=missing)

        assert _errors(output) == [f"--ca file not found: {missing}"]
        issue.assert_not_called()


class TestCustomHappyPath:
    def test_valid_custom_call_reaches_add_bench_certificate_with_source_paths(self, add, tmp_path):
        output, issue = add
        cert = tmp_path / "a.crt"
        key = tmp_path / "a.key"
        ca = tmp_path / "ca.crt"
        cert.touch()
        key.touch()
        ca.touch()
        ctx = _ctx()

        add_certificate(ctx, address=BENCH, custom=True, cert=cert, key=key, ca=ca)

        issue.assert_called_once_with(
            ctx,
            BENCH,
            DOMAIN,
            LETSENCRYPT_PREFERRED_CHALLENGE.http01,
            None,
            False,
            dev=False,
            dns_provider=None,
            custom=True,
            cert_path=cert,
            key_path=key,
            ca_path=ca,
            behind_proxy=False,
        )
        assert _errors(output) == []
