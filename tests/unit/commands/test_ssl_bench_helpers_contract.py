"""Characterization of the bench-side SSL command helpers and the admin-tools module.

Two production surfaces are pinned here so a later refactor cannot change them silently:

``frappe_manager/commands/ssl/bench_helpers.py`` -- the bench half of ``fm ssl``:

* which *refusals* fire and in what order.  ``_add_bench_certificate`` rejects an unknown
  domain before it even changes the status head, rejects ``--cname`` on a non-DNS-01
  challenge, and rejects ``--cname`` on a dev certificate only *after* the head changed.
* which certificate each flag combination builds -- a bare ``SSLCertificate`` for dev, a
  ``LetsencryptSSLCertificate`` otherwise, delegating or not according to ``delegation_cname``
  -- and with which field values, since those select the downstream issuance path.
* what each path *writes*: ``dry_run`` suppresses the ``host_name`` rewrite entirely; a
  successful add flips ``host_name`` to ``https://``, a successful remove back to
  ``http://``, and both treat a failure of that write as non-fatal.
* the certificate-state table produced by ``_list_bench_certificates``: it is driven by the
  bench *config* domains, not by the certificate list, so a domain with no certificate still
  gets a row and a certificate for an unconfigured domain is invisible.

``frappe_manager/site_manager/modules/bench_admin_tools.py`` -- adminer/mailpit wiring:

* enable/disable ordering and their side effects on nginx, on the adminer plugin directory
  and on ``common_site_config.json``.
* the nginx/auth interaction: ``disable()`` drops only ``custom/admin-tools.conf`` and must
  leave the htpasswd file and the server-level ``auth.conf`` (owned by
  ``Bench.ensure_fm_nginx_confs()``) untouched -- destroying them was a real prior bug.
* which per-location auth directives follow from the (web, tools) auth state.

Everything external is mocked at its seam: no docker daemon, no bench, no network.  The
filesystem is confined to ``tmp_path``; the real jinja2 templates and the real
``frappe_manager.site_manager.modules.auth`` renderers are used on purpose, because the
rendered conf text is the observable contract.

These tests pin CURRENT behaviour, including the quirks noted in comments below.  They are
not a wish list; do not "fix" a pinned quirk here without changing production first.
"""

# SLF001: the private helpers and rich's cell store ARE the observable surface here.

import json
from contextlib import ExitStack
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, call, patch

import pytest
import typer
from rich.table import Table

from frappe_manager.commands.ssl.bench_helpers import (
    _add_bench_certificate,
    _list_bench_certificates,
    _remove_bench_certificate,
)
from frappe_manager.docker.docker_exceptions import DockerException
from frappe_manager.docker.subprocess_output import SubprocessOutput
from frappe_manager.site_manager.bench_config import AuthConfig, SiteConfig
from frappe_manager.site_manager.exceptions import AdminToolsFailedToStart, AdminToolsFailedToStop, BenchException
from frappe_manager.site_manager.modules.bench_admin_tools import BenchAdminTools
from frappe_manager.ssl_manager import LETSENCRYPT_PREFERRED_CHALLENGE, SUPPORTED_SSL_TYPES
from frappe_manager.ssl_manager.certificate import RETIRED_CERTIFICATE_KEYS, CustomCertificate, SSLCertificate
from frappe_manager.ssl_manager.certificate_exceptions import SSLCertificateNotFoundError
from frappe_manager.ssl_manager.letsencrypt_certificate import LetsencryptSSLCertificate
from frappe_manager.utils.helpers import get_current_fm_version

SSL_MODULE = "frappe_manager.commands.ssl.bench_helpers"
TOOLS_MODULE = "frappe_manager.site_manager.modules.bench_admin_tools"

BENCH = "test.local"
DOMAIN = "test.local"
ALIAS = "alias.example.com"
DNS01 = LETSENCRYPT_PREFERRED_CHALLENGE.dns01
HTTP01 = LETSENCRYPT_PREFERRED_CHALLENGE.http01


def _docker_failure() -> DockerException:
    return DockerException(["compose", "up"], SubprocessOutput([], [], [], 1))


# ======================================================================================
# bench_helpers harness
# ======================================================================================


class SSLHarness:
    """Every collaborator of bench_helpers, patched at the module boundary."""

    def __init__(self, stack: ExitStack):
        self.services = MagicMock(name="services_manager")
        self.ctx = MagicMock(name="ctx")
        self.ctx.obj = {"services": self.services}

        self.output = MagicMock(name="output_handler")

        self.get_output_handler = stack.enter_context(patch(f"{SSL_MODULE}.get_output_handler"))
        self.get_output_handler.return_value = self.output

        self.Bench = stack.enter_context(patch(f"{SSL_MODULE}.Bench"))
        self.bench = self.Bench.get_object.return_value
        # Aliases live on the site now, so the harness records them the way a bench does: this
        # bench's single site is named after the bench (DOMAIN), and ALIAS is an alias OF that
        # site. `bench_config.domains` -- what all three helpers read -- is site name then aliases.
        self.set_sites({DOMAIN: [ALIAS]})
        self.cert_manager = self.bench.certificate_manager
        self.cert_manager.list_certificates.return_value = []

    def set_sites(self, sites: dict[str, list[str]]) -> None:
        """Record `{site name: that site's aliases}` and derive the bench's hostname list from it.

        Mirrors `BenchConfig.domains`: each site contributes its own name (a site's name IS its
        canonical domain) followed by the aliases recorded under that site.
        """
        configs = {name: SiteConfig(alias_domains=aliases) for name, aliases in sites.items()}
        self.bench.bench_config.sites = configs
        self.bench.site_name = next(iter(configs))
        self.bench.bench_config.domains = [
            domain for name, config in configs.items() for domain in (name, *config.alias_domains)
        ]
        # domain -> site, which is what decides whose `host_name` a certificate rewrites. Derived
        # from the same table as `domains` so a harness with two sites cannot disagree with itself.
        self.bench.bench_config.get_site_mappings.return_value = {
            domain: name for name, config in configs.items() for domain in (name, *config.alias_domains)
        }

    # -- convenience readers -----------------------------------------------------------

    def prints(self) -> list[str]:
        return [c.args[0] for c in self.output.print.call_args_list]

    def print_emojis(self) -> list[str]:
        return [c.kwargs.get("emoji_code") for c in self.output.print.call_args_list]

    def heads(self) -> list[str]:
        return [c.args[0] for c in self.output.change_head.call_args_list]

    def errors(self) -> list[str]:
        return [c.args[0] for c in self.output.display_error.call_args_list]

    def debugs(self) -> list[str]:
        return [c.args[0] for c in self.output.debug.call_args_list]

    def warnings(self) -> list[str]:
        return [c.args[0] for c in self.output.warning.call_args_list]

    def spinner_texts(self) -> list[str]:
        return [c.args[0] for c in self.output.start.call_args_list]

    def added_cert(self):
        return self.cert_manager.add_certificate.call_args.args[0]

    def site_config_writes(self) -> list[tuple[str, dict]]:
        """`(site, config)` per write: a certificate's host_name belongs to ONE site, so the
        helper names it rather than letting the bench pick."""
        return [(c.args[0], c.args[1]) for c in self.bench.set_bench_site_config.call_args_list]

    def table(self) -> Table:
        assert self.output.print_data.call_count == 1
        return self.output.print_data.call_args.args[0]


@pytest.fixture
def h():
    with ExitStack() as stack:
        yield SSLHarness(stack)


def _add(
    h,
    *,
    domain=DOMAIN,
    challenge=HTTP01,
    cname=None,
    dry_run=False,
    dev=False,
    custom=False,
    cert_path=None,
    key_path=None,
    ca_path=None,
):
    return _add_bench_certificate(
        h.ctx,
        BENCH,
        domain,
        challenge,
        cname,
        dry_run,
        dev,
        custom=custom,
        cert_path=cert_path,
        key_path=key_path,
        ca_path=ca_path,
    )


def _remove(h, *, domain=DOMAIN, yes=True):
    return _remove_bench_certificate(h.ctx, BENCH, domain, yes)


# ======================================================================================
# _add_bench_certificate -- refusals
# ======================================================================================


@pytest.mark.timeout(15)
def test_add_resolves_the_bench_through_get_object_with_the_command_output_handler(h):
    _add(h)
    h.Bench.get_object.assert_called_once_with(BENCH, h.services, output_handler=h.output)


@pytest.mark.timeout(15)
def test_add_refuses_a_domain_absent_from_the_bench_config(h):
    with pytest.raises(typer.Exit) as exc:
        _add(h, domain="stranger.example.com")

    assert exc.value.exit_code == 1
    # The refusal must name the allowed domains and the exact command that would add one.
    message = h.errors()[0]
    assert "stranger.example.com" in message
    assert f"Allowed domains: {DOMAIN}, {ALIAS}" in message
    assert f"fm update {BENCH} --add-alias stranger.example.com" in message
    h.cert_manager.add_certificate.assert_not_called()


@pytest.mark.timeout(15)
def test_add_rejects_the_unknown_domain_before_announcing_any_work(h):
    """The domain guard is the very first decision: no head change, no spinner."""
    with pytest.raises(typer.Exit):
        _add(h, domain="stranger.example.com")

    assert h.heads() == []
    assert h.spinner_texts() == []


@pytest.mark.timeout(15)
def test_add_refuses_cname_with_http01_challenge(h):
    with pytest.raises(typer.Exit) as exc:
        _add(h, challenge=HTTP01, cname="delegated.fm.test")

    assert exc.value.exit_code == 1
    assert h.errors() == ["CNAME delegation (--cname) can only be used with DNS-01 challenge"]
    h.cert_manager.add_certificate.assert_not_called()
    # Still before the head change.
    assert h.heads() == []


@pytest.mark.timeout(15)
def test_add_reports_the_bad_domain_and_not_the_bad_cname_when_both_are_wrong(h):
    """Guard precedence: the domain check runs first and wins."""
    with pytest.raises(typer.Exit):
        _add(h, domain="stranger.example.com", challenge=HTTP01, cname="delegated.fm.test")

    assert len(h.errors()) == 1
    assert "is not configured for bench" in h.errors()[0]
    assert "CNAME delegation" not in h.errors()[0]


@pytest.mark.timeout(15)
def test_add_refuses_cname_on_a_dev_certificate_but_only_after_announcing_the_work(h):
    with pytest.raises(typer.Exit) as exc:
        _add(h, challenge=DNS01, cname="delegated.fm.test", dev=True)

    assert exc.value.exit_code == 1
    assert h.errors() == ["--cname is not applicable to dev certificates"]
    # The dev/cname guard sits *below* change_head, unlike the other two.
    assert h.heads() == [f"Adding SSL certificate for {DOMAIN}"]
    h.cert_manager.add_certificate.assert_not_called()


# ======================================================================================
# _add_bench_certificate -- which certificate gets built
# ======================================================================================


@pytest.mark.timeout(15)
def test_add_dev_builds_a_plain_dev_certificate_with_no_challenge(h):
    _add(h, challenge=DNS01, dev=True)

    cert = h.added_cert()
    # Exactly the base class: a dev cert must not travel down the letsencrypt path.
    assert type(cert) is SSLCertificate
    assert cert.domain == DOMAIN
    assert cert.ssl_type == SUPPORTED_SSL_TYPES.dev
    # dev ignores --challenge entirely.
    assert cert.challenge_type is None


@pytest.mark.timeout(15)
def test_add_custom_builds_a_custom_certificate_carrying_the_source_paths(h, tmp_path):
    cert_file = tmp_path / "app.crt"
    key_file = tmp_path / "app.key"
    _add(h, challenge=DNS01, custom=True, cert_path=cert_file, key_path=key_file)

    cert = h.added_cert()
    assert type(cert) is CustomCertificate
    assert cert.domain == DOMAIN
    assert cert.ssl_type == SUPPORTED_SSL_TYPES.custom
    assert cert.cert_source == cert_file
    assert cert.key_source == key_file
    assert cert.ca_source is None
    # custom ignores --challenge entirely, exactly like dev.
    assert cert.challenge_type is None


@pytest.mark.timeout(15)
def test_add_custom_carries_the_ca_source_when_given(h, tmp_path):
    cert_file = tmp_path / "app.crt"
    key_file = tmp_path / "app.key"
    ca_file = tmp_path / "ca.crt"
    _add(h, custom=True, cert_path=cert_file, key_path=key_file, ca_path=ca_file)

    assert h.added_cert().ca_source == ca_file

@pytest.mark.timeout(15)
def test_add_with_cname_builds_a_certificate_carrying_the_delegation(h):
    _add(h, challenge=DNS01, cname="alias-example-com.fm.test")

    cert = h.added_cert()
    assert type(cert) is LetsencryptSSLCertificate
    assert cert.domain == DOMAIN
    assert cert.ssl_type == SUPPORTED_SSL_TYPES.le
    assert cert.challenge_type == DNS01
    # The field, not a class, is what makes acme.sh receive --challenge-alias.
    assert cert.delegation_cname == "alias-example-com.fm.test"
    # Credentials are deliberately absent; they are resolved from `[ssl.dns_providers]` at issuance.
    assert RETIRED_CERTIFICATE_KEYS.isdisjoint(cert.model_dump())


@pytest.mark.timeout(15)
def test_add_with_cname_tells_the_user_delegation_is_in_use(h):
    _add(h, challenge=DNS01, cname="alias-example-com.fm.test")

    assert "Using CNAME delegation: alias-example-com.fm.test" in h.prints()
    assert ":information:" in h.print_emojis()


@pytest.mark.timeout(15)
@pytest.mark.parametrize("challenge", [HTTP01, DNS01])
def test_add_without_cname_or_dev_builds_a_letsencrypt_certificate_for_the_asked_challenge(h, challenge):
    _add(h, challenge=challenge)

    cert = h.added_cert()
    assert type(cert) is LetsencryptSSLCertificate
    assert cert.ssl_type == SUPPORTED_SSL_TYPES.le
    assert cert.challenge_type == challenge
    assert cert.delegation_cname is None
    assert RETIRED_CERTIFICATE_KEYS.isdisjoint(cert.model_dump())
    # No delegation notice on the plain path.
    assert not any(p.startswith("Using CNAME delegation") for p in h.prints())


@pytest.mark.timeout(15)
def test_add_issues_an_alias_certificate_for_the_alias_not_the_primary_domain(h):
    _add(h, domain=ALIAS, challenge=HTTP01)

    assert h.added_cert().domain == ALIAS
    assert h.heads() == [f"Adding SSL certificate for {ALIAS}"]


# ======================================================================================
# _add_bench_certificate -- writes and ordering
# ======================================================================================


@pytest.mark.timeout(15)
def test_add_wraps_issuance_in_a_spinner_labelled_for_the_domain(h):
    _add(h)

    assert h.spinner_texts() == [f"Adding SSL certificate for {DOMAIN}"]
    assert h.output.stop.call_count == 1


@pytest.mark.timeout(15)
def test_add_forwards_dry_run_to_the_certificate_manager(h):
    _add(h, dry_run=True)

    h.cert_manager.add_certificate.assert_called_once()
    assert h.cert_manager.add_certificate.call_args.kwargs == {"dry_run": True}


@pytest.mark.timeout(15)
def test_add_dry_run_writes_nothing_and_claims_nothing(h):
    _add(h, dry_run=True)

    h.bench.set_bench_site_config.assert_not_called()
    assert h.prints() == []


@pytest.mark.timeout(15)
def test_add_flips_host_name_to_https_and_confirms_when_not_a_dry_run(h):
    _add(h, dry_run=False)

    assert h.site_config_writes() == [(DOMAIN, {"host_name": f"https://{DOMAIN}"})]
    assert h.prints() == [
        f"SSL certificate added for {DOMAIN}",
        "Certificate has been issued and configured.",
        f"Run 'fm start {BENCH}' to apply it (recreates only the services whose definition "
        "changed; running jobs are undisturbed until then).",
    ]
    assert h.print_emojis() == [":white_check_mark:", ":zap:", ":information:"]


# ======================================================================================
# _add_bench_certificate / _remove_bench_certificate -- eager compose regeneration
# ======================================================================================


@pytest.mark.timeout(15)
def test_add_regenerates_bench_and_workers_compose(h):
    """This is the ONLY thing that carries a --dev/--custom --ca certificate's CA trust into the
    compose file; fm ssl add used to call neither generate_compose."""
    _add(h, dry_run=False)

    h.bench.generate_compose.assert_called_once_with(h.bench.bench_config.export_to_compose_inputs.return_value)
    h.bench.workers.generate_compose.assert_called_once_with()


@pytest.mark.timeout(15)
def test_add_skips_workers_regen_when_no_workers_compose_exists(h):
    h.bench.workers.compose_file_manager.compose_path.exists.return_value = False

    _add(h, dry_run=False)

    h.bench.generate_compose.assert_called_once()
    h.bench.workers.generate_compose.assert_not_called()


@pytest.mark.timeout(15)
def test_add_dry_run_never_regenerates_compose(h):
    """--dry-run promises no nginx change; the bench's own compose must stay untouched too."""
    _add(h, dry_run=True)

    h.bench.generate_compose.assert_not_called()
    h.bench.workers.generate_compose.assert_not_called()
    assert not any("fm start" in p for p in h.prints())


@pytest.mark.timeout(15)
def test_add_regen_failure_warns_and_skips_the_converge_instruction_but_does_not_abort(h):
    h.bench.generate_compose.side_effect = RuntimeError("disk full")

    _add(h, dry_run=False)  # must not raise

    assert any("Could not update" in w and "disk full" in w for w in h.warnings())
    assert not any("fm start" in p for p in h.prints())
    # The certificate itself is unaffected by a compose-regen failure.
    assert f"SSL certificate added for {DOMAIN}" in h.prints()


@pytest.mark.timeout(15)
def test_remove_regenerates_bench_and_workers_compose(h):
    _remove(h)

    h.bench.generate_compose.assert_called_once_with(h.bench.bench_config.export_to_compose_inputs.return_value)
    h.bench.workers.generate_compose.assert_called_once_with()


@pytest.mark.timeout(15)
def test_remove_skips_workers_regen_when_no_workers_compose_exists(h):
    h.bench.workers.compose_file_manager.compose_path.exists.return_value = False

    _remove(h)

    h.bench.generate_compose.assert_called_once()
    h.bench.workers.generate_compose.assert_not_called()


@pytest.mark.timeout(15)
def test_remove_regen_failure_warns_but_does_not_abort(h):
    h.bench.generate_compose.side_effect = RuntimeError("disk full")

    _remove(h)  # must not raise

    assert any("Could not update" in w and "disk full" in w for w in h.warnings())
    assert not any("fm start" in p for p in h.prints())
    assert f"SSL certificate removed for {DOMAIN}" in h.prints()


@pytest.mark.timeout(15)
def test_add_treats_a_failed_host_name_write_as_non_fatal(h):
    """A bench whose site does not exist yet has no site_config.json; that must not fail the add."""
    h.bench.set_bench_site_config.side_effect = RuntimeError("no site_config.json")

    _add(h, dry_run=False)

    assert any("Could not update host_name to https://" in d for d in h.debugs())
    assert f"SSL certificate added for {DOMAIN}" in h.prints()


@pytest.mark.timeout(15)
def test_add_propagates_an_issuance_failure_and_skips_every_write(h):
    h.cert_manager.add_certificate.side_effect = RuntimeError("acmesh exploded")

    with pytest.raises(RuntimeError, match="acmesh exploded"):
        _add(h)

    # spinner must be torn down even on the failure path
    assert h.output.stop.call_count == 1
    h.bench.set_bench_site_config.assert_not_called()
    assert h.prints() == []


# ======================================================================================
# _remove_bench_certificate
# ======================================================================================


@pytest.mark.timeout(15)
def test_remove_refuses_a_domain_absent_from_the_bench_config(h):
    with pytest.raises(typer.Exit) as exc:
        _remove(h, domain="stranger.example.com")

    assert exc.value.exit_code == 1
    # Remove's refusal is the short form: no allowed-domain list, no --add-alias hint.
    assert h.errors() == [f"Domain 'stranger.example.com' is not configured for bench '{BENCH}'"]
    h.cert_manager.remove_certificate_by_domain.assert_not_called()


@pytest.mark.timeout(15)
def test_remove_asks_for_confirmation_when_yes_was_not_passed(h):
    h.output.prompt_ask.return_value = "yes"

    _remove(h, yes=False)

    h.output.prompt_ask.assert_called_once_with(
        prompt=f"Remove SSL certificate for {DOMAIN}?",
        choices=["yes", "no"],
        default="no",
        required_flag="--yes or -y",
    )
    h.cert_manager.remove_certificate_by_domain.assert_called_once_with(DOMAIN)


@pytest.mark.timeout(15)
@pytest.mark.parametrize("answer", ["no", "", "YES", "y"])
def test_remove_aborts_with_exit_zero_on_anything_but_a_literal_yes(h, answer):
    """Only the exact string ``yes`` proceeds; declining is a success exit, not a failure."""
    h.output.prompt_ask.return_value = answer

    with pytest.raises(typer.Exit) as exc:
        _remove(h, yes=False)

    assert exc.value.exit_code == 0
    assert h.prints() == ["Cancelled."]
    assert h.print_emojis() == [":x:"]
    h.cert_manager.remove_certificate_by_domain.assert_not_called()
    h.bench.set_bench_site_config.assert_not_called()


@pytest.mark.timeout(15)
def test_remove_skips_the_prompt_entirely_when_yes_was_passed(h):
    _remove(h, yes=True)

    h.output.prompt_ask.assert_not_called()
    h.cert_manager.remove_certificate_by_domain.assert_called_once_with(DOMAIN)


@pytest.mark.timeout(15)
def test_remove_reverts_host_name_to_http_and_confirms(h):
    _remove(h)

    assert h.site_config_writes() == [(DOMAIN, {"host_name": f"http://{DOMAIN}"})]
    assert h.prints() == [
        f"SSL certificate removed for {DOMAIN}",
        f"Run 'fm start {BENCH}' to apply it (recreates only the services whose definition "
        "changed; running jobs are undisturbed until then).",
    ]
    assert h.spinner_texts() == [f"Removing SSL certificate for {DOMAIN}"]


@pytest.mark.timeout(15)
def test_remove_announces_the_same_head_twice(h):
    """SUSPICION (pinned, not fixed): change_head is issued twice with identical text."""
    _remove(h, yes=True)

    assert h.heads() == [
        f"Removing SSL certificate for {DOMAIN}",
        f"Removing SSL certificate for {DOMAIN}",
    ]


@pytest.mark.timeout(15)
def test_remove_treats_a_failed_host_name_write_as_non_fatal(h):
    h.bench.set_bench_site_config.side_effect = RuntimeError("gone")

    _remove(h)

    assert any("Could not update host_name to http://" in d for d in h.debugs())
    assert f"SSL certificate removed for {DOMAIN}" in h.prints()


@pytest.mark.timeout(15)
def test_remove_swallows_a_certificate_not_found_raised_by_the_host_name_write(h):
    """SUSPICION (pinned): the inner ``except Exception`` shadows the outer handler, so a
    SSLCertificateNotFoundError from set_bench_site_config is downgraded to a debug line and
    the removal still reports success."""
    h.bench.set_bench_site_config.side_effect = SSLCertificateNotFoundError(DOMAIN)

    _remove(h)

    assert h.errors() == []
    assert f"SSL certificate removed for {DOMAIN}" in h.prints()


@pytest.mark.timeout(15)
def test_remove_reports_a_missing_certificate_as_a_single_error(h):
    h.cert_manager.remove_certificate_by_domain.side_effect = SSLCertificateNotFoundError(DOMAIN)

    with pytest.raises(typer.Exit) as exc:
        _remove(h)

    assert exc.value.exit_code == 1
    assert len(h.errors()) == 1
    assert h.errors()[0].startswith("Certificate not found: ")
    # No host_name revert when there was nothing to remove.
    h.bench.set_bench_site_config.assert_not_called()
    assert h.prints() == []


@pytest.mark.timeout(15)
def test_remove_reports_an_unexpected_failure_twice(h):
    """SUSPICION (pinned): the generic handler prints the same exception under two labels."""
    h.cert_manager.remove_certificate_by_domain.side_effect = RuntimeError("boom")

    with pytest.raises(typer.Exit) as exc:
        _remove(h)

    assert exc.value.exit_code == 1
    assert h.errors() == ["Failed to remove certificate: boom", "Error details: boom"]
    h.bench.set_bench_site_config.assert_not_called()


@pytest.mark.timeout(15)
def test_remove_hides_the_original_traceback(h):
    h.cert_manager.remove_certificate_by_domain.side_effect = RuntimeError("boom")

    with pytest.raises(typer.Exit) as exc:
        _remove(h)

    # `raise ... from None` -- the docker/acme noise must not reach the user.
    assert exc.value.__cause__ is None
    assert exc.value.__suppress_context__ is True


# ======================================================================================
# _list_bench_certificates
# ======================================================================================


def _cert_row(
    domain,
    *,
    ssl_type="letsencrypt",
    challenge_type="http01",
    exists=True,
    expiry_date=None,
    days_until_expiry=None,
    needs_renewal=False,
):
    return {
        "domain": domain,
        "ssl_type": ssl_type,
        "challenge_type": challenge_type,
        "exists": exists,
        "expiry_date": expiry_date,
        "days_until_expiry": days_until_expiry,
        "needs_renewal": needs_renewal,
    }


def _rows(table: Table) -> list[tuple]:
    cells = [list(column._cells) for column in table.columns]
    return [tuple(row) for row in zip(*cells, strict=True)]


@pytest.mark.timeout(15)
def test_list_renders_a_fixed_eight_column_certificate_table(h):
    """`DNS Provider` sits beside `Challenge` because it only means anything for a dns01 challenge,
    and because a certificate bound to the wrong Cloudflare account is otherwise invisible here."""
    _list_bench_certificates(h.ctx, BENCH)

    table = h.table()
    assert [c.header for c in table.columns] == [
        "Domain",
        "Type",
        "Challenge",
        "DNS Provider",
        "Status",
        "Expiry",
        "Days Left",
        "Renewal",
    ]


@pytest.mark.timeout(15)
def test_list_is_driven_by_the_config_domains_in_config_order(h):
    # Config order is now site-then-its-aliases, so the row order is the site DOMAIN followed by
    # its alias -- neither alphabetical (ALIAS sorts first) nor certificate-driven (only DOMAIN
    # has one). The reverse order the old bench-level list could express is unreachable.
    h.set_sites({DOMAIN: [ALIAS]})
    h.cert_manager.list_certificates.return_value = [_cert_row(DOMAIN)]

    _list_bench_certificates(h.ctx, BENCH)

    assert [row[0] for row in _rows(h.table())] == [DOMAIN, ALIAS]


@pytest.mark.timeout(15)
def test_list_shows_a_configured_domain_with_no_certificate_as_no_ssl(h):
    h.cert_manager.list_certificates.return_value = []

    _list_bench_certificates(h.ctx, BENCH)

    assert _rows(h.table())[0] == (DOMAIN, "none", "N/A", "N/A", "⚪ No SSL", "N/A", "N/A", "N/A")


@pytest.mark.timeout(15)
def test_list_hides_a_certificate_whose_domain_left_the_bench_config(h):
    """SUSPICION (pinned): a certificate for an un-configured domain is silently invisible,
    so a stale certificate cannot be discovered through ``fm ssl list``."""
    h.set_sites({DOMAIN: []})  # the bench's one site, no aliases
    h.cert_manager.list_certificates.return_value = [_cert_row(DOMAIN), _cert_row("orphan.example.com")]

    _list_bench_certificates(h.ctx, BENCH)

    assert [row[0] for row in _rows(h.table())] == [DOMAIN]


@pytest.mark.timeout(15)
def test_list_reports_an_issued_certificate_with_expiry_and_days_left(h):
    h.set_sites({DOMAIN: []})  # the bench's one site, no aliases
    h.cert_manager.list_certificates.return_value = [
        _cert_row(
            DOMAIN,
            expiry_date=datetime(2026, 3, 4, 5, 6, 7, tzinfo=UTC),
            days_until_expiry=42,
            needs_renewal=False,
        )
    ]

    _list_bench_certificates(h.ctx, BENCH)

    # `N/A` for the provider: this is an http01 certificate, so no DNS credential is involved.
    assert _rows(h.table())[0] == (
        DOMAIN,
        "letsencrypt",
        "http01",
        "N/A",
        "✅ Issued",
        "2026-03-04 05:06",
        "42",
        "✓ OK",
    )


@pytest.mark.timeout(15)
def test_list_flags_a_certificate_that_needs_renewal(h):
    h.set_sites({DOMAIN: []})  # the bench's one site, no aliases
    h.cert_manager.list_certificates.return_value = [
        _cert_row(DOMAIN, expiry_date=datetime(2026, 1, 1, 0, 0, tzinfo=UTC), days_until_expiry=3, needs_renewal=True)
    ]

    _list_bench_certificates(h.ctx, BENCH)

    assert _rows(h.table())[0][-1] == "⚠️ DUE"


@pytest.mark.timeout(15)
def test_list_never_calls_a_custom_certificate_due_for_renewal(h):
    """fm has no ACME account and no stored source bytes for a custom certificate, so `fm ssl
    renew` never acts on it -- 'DUE' would promise that. Name the real action instead."""
    h.set_sites({DOMAIN: []})  # the bench's one site, no aliases
    h.cert_manager.list_certificates.return_value = [
        _cert_row(
            DOMAIN,
            ssl_type="custom",
            challenge_type=None,
            expiry_date=datetime(2026, 1, 1, 0, 0, tzinfo=UTC),
            days_until_expiry=3,
            needs_renewal=True,
        )
    ]

    _list_bench_certificates(h.ctx, BENCH)

    renewal_cell = _rows(h.table())[0][-1]
    assert renewal_cell == "⚠️ re-import"
    assert "DUE" not in renewal_cell


@pytest.mark.timeout(15)
def test_list_shows_a_healthy_custom_certificate_as_manual_not_ok(h):
    """'OK' beside every other type means 'fm has this covered'; for --custom that is never true,
    even well before expiry, so it must read differently from the auto-renewable types."""
    h.set_sites({DOMAIN: []})  # the bench's one site, no aliases
    h.cert_manager.list_certificates.return_value = [
        _cert_row(
            DOMAIN,
            ssl_type="custom",
            challenge_type=None,
            expiry_date=datetime(2026, 1, 1, 0, 0, tzinfo=UTC),
            days_until_expiry=200,
            needs_renewal=False,
        )
    ]

    _list_bench_certificates(h.ctx, BENCH)

    renewal_cell = _rows(h.table())[0][-1]
    assert renewal_cell == "manual"
    assert renewal_cell != "✓ OK"

@pytest.mark.timeout(15)
def test_list_marks_a_configured_but_unissued_certificate_as_not_issued(h):
    h.set_sites({DOMAIN: []})  # the bench's one site, no aliases
    h.cert_manager.list_certificates.return_value = [_cert_row(DOMAIN, exists=False, days_until_expiry=99)]

    _list_bench_certificates(h.ctx, BENCH)

    row = _rows(h.table())[0]
    # The type/challenge survive, but nothing expiry-derived is claimed.
    assert row == (DOMAIN, "letsencrypt", "http01", "N/A", "❌ Not Issued", "N/A", "N/A", "N/A")


@pytest.mark.timeout(15)
def test_list_falls_back_to_na_when_an_issued_certificate_has_no_expiry_date(h):
    h.set_sites({DOMAIN: []})  # the bench's one site, no aliases
    h.cert_manager.list_certificates.return_value = [_cert_row(DOMAIN, exists=True, expiry_date=None)]

    _list_bench_certificates(h.ctx, BENCH)

    assert _rows(h.table())[0] == (DOMAIN, "letsencrypt", "http01", "N/A", "✅ Issued", "N/A", "N/A", "N/A")


@pytest.mark.timeout(15)
@pytest.mark.parametrize("challenge_type", [None, ""])
def test_list_renders_a_missing_challenge_type_as_na(h, challenge_type):
    h.set_sites({DOMAIN: []})  # the bench's one site, no aliases
    h.cert_manager.list_certificates.return_value = [_cert_row(DOMAIN, challenge_type=challenge_type, exists=False)]

    _list_bench_certificates(h.ctx, BENCH)

    assert _rows(h.table())[0][2] == "N/A"


@pytest.mark.timeout(15)
def test_list_lets_the_last_duplicate_certificate_entry_win(h):
    h.set_sites({DOMAIN: []})  # the bench's one site, no aliases
    h.cert_manager.list_certificates.return_value = [
        _cert_row(DOMAIN, ssl_type="dev", exists=False),
        _cert_row(DOMAIN, ssl_type="letsencrypt", exists=False),
    ]

    _list_bench_certificates(h.ctx, BENCH)

    rows = _rows(h.table())
    assert len(rows) == 1
    assert rows[0][1] == "letsencrypt"


@pytest.mark.timeout(15)
def test_list_is_a_pure_read_with_no_status_head_and_no_spinner(h):
    _list_bench_certificates(h.ctx, BENCH)

    assert h.heads() == []
    assert h.spinner_texts() == []
    h.output.display_error.assert_not_called()


# ======================================================================================
# BenchAdminTools harness
# ======================================================================================


class ToolsHarness:
    """A BenchAdminTools on a tmp_path bench, with compose/docker patched at the boundary."""

    def __init__(self, tmp_path: Path, stack: ExitStack, auth=None):
        self.bench_path = tmp_path / "benches" / BENCH
        self.bench_path.mkdir(parents=True)

        self.bench = MagicMock(name="bench")
        self.bench.path = self.bench_path
        self.bench.name = BENCH
        self.bench.bench_config.auth = auth
        self.bench.bench_config.restart_policy.value = "always"

        # A real `[sites]` shape, because the locations are rendered once per site now and each
        # site decides whether it routes them at all.
        self.site = f"{BENCH}.localhost"
        self.bench.bench_config.site_names = [self.site]
        self.bench.bench_config.admin_tools = True
        self.bench.bench_config.serves_admin_tools = lambda s: True
        self.bench.bench_config.auth_for = lambda s: auth or AuthConfig()

        # The nginx conf tree as ensure_fm_nginx_confs() leaves it: our location conf lives
        # in custom/ next to the server-level auth conf, and the htpasswd one level up.
        self.nginx_conf_host = tmp_path / "nginx" / "conf"
        self.custom_dir = self.nginx_conf_host / "custom"
        self.custom_dir.mkdir(parents=True)
        self.htpasswd_dir = self.nginx_conf_host / "http_auth"
        self.htpasswd_dir.mkdir()
        self.auth_conf = self.custom_dir / "auth.conf"
        self.auth_conf.write_text("# fm:auth server level\n")
        self.htpasswd = self.htpasswd_dir / f"{BENCH}.htpasswd"
        self.htpasswd.write_text("admin:hash\n")

        self.nginx_proxy = MagicMock(name="nginx_proxy")
        self.nginx_proxy.dirs = SimpleNamespace(conf=SimpleNamespace(host=self.nginx_conf_host))

        self.output = MagicMock(name="output_handler")

        self.ComposeFile = stack.enter_context(patch(f"{TOOLS_MODULE}.ComposeFile"))
        self.compose = self.ComposeFile.return_value
        self.compose.get_services_list.return_value = ["mailpit", "adminer"]
        self.compose.get_container_names.return_value = {
            "mailpit": "fm__test_local__mailpit",
            "adminer": "fm__test_local__adminer",
        }

        self.DockerClient = stack.enter_context(patch(f"{TOOLS_MODULE}.DockerClient"))
        self.docker = self.DockerClient.return_value

        self.tools = BenchAdminTools(self.bench, self.nginx_proxy, output_handler=self.output)

    # -- convenience readers -----------------------------------------------------------

    @property
    def location_conf(self) -> Path:
        """The tool locations of the harness's one site, which is where they live now: the shared
        `custom/admin-tools.conf` reached every hostname the bench served."""
        return self.custom_dir / self.site / "admin-tools.conf"

    @property
    def adminer_dir(self) -> Path:
        return self.bench_path / "configs" / "adminer"

    @property
    def common_site_config(self) -> Path:
        return self.bench_path / "workspace" / "frappe-bench" / "sites" / "common_site_config.json"

    def write_common_site_config(self, config: dict):
        self.common_site_config.parent.mkdir(parents=True, exist_ok=True)
        self.common_site_config.write_text(json.dumps(config))

    def read_common_site_config(self) -> dict:
        return json.loads(self.common_site_config.read_text())

    def prints(self) -> list[str]:
        return [c.args[0] for c in self.output.print.call_args_list]

    def heads(self) -> list[str]:
        return [c.args[0] for c in self.output.change_head.call_args_list]


@pytest.fixture
def t(tmp_path):
    with ExitStack() as stack:
        yield ToolsHarness(tmp_path, stack)


def _auth(**kwargs):
    from frappe_manager.site_manager.bench_config import AuthConfig

    return AuthConfig(**kwargs)


# ======================================================================================
# BenchAdminTools -- construction
# ======================================================================================


@pytest.mark.timeout(15)
def test_admin_tools_derives_all_its_paths_from_the_bench_and_the_proxy(t):
    assert t.tools.compose_path == t.bench_path / "docker-compose.admin-tools.yml"
    assert t.tools.nginx_config_location_path == t.custom_dir / "admin-tools.conf"
    assert t.tools.adminer_config_path == t.bench_path / "configs" / "adminer"
    assert t.tools.bench_name == BENCH


@pytest.mark.timeout(15)
def test_admin_tools_binds_compose_and_docker_to_the_admin_tools_compose_file(t):
    t.ComposeFile.assert_called_once_with(
        t.tools.compose_path,
        template_name="docker-compose.admin-tools.tmpl",
    )
    t.DockerClient.assert_called_once_with(compose_file_path=t.tools.compose_path, output=t.output)


@pytest.mark.timeout(15)
def test_admin_tools_falls_back_to_a_rich_output_handler(t):
    from frappe_manager.output_manager.rich_output import RichOutputHandler

    tools = BenchAdminTools(t.bench, t.nginx_proxy)

    assert isinstance(tools.output, RichOutputHandler)


# ======================================================================================
# BenchAdminTools -- compose generation and the adminer plugin
# ======================================================================================


@pytest.mark.timeout(15)
def test_generate_compose_configures_the_bench_then_persists_then_syncs_the_plugin(t):
    order = []
    t.compose.configure_bench.side_effect = lambda **_kw: order.append("configure")
    t.compose.set_all_services_restart.side_effect = lambda *_a: order.append("restart")
    t.compose.write_to_file.side_effect = lambda *_a: order.append("write")

    with patch.object(BenchAdminTools, "sync_adminer_plugin", side_effect=lambda: order.append("plugin")):
        t.tools.generate_compose()

    assert order == ["configure", "restart", "write", "plugin"]
    assert t.compose.yml is t.compose.load_template.return_value
    t.compose.configure_bench.assert_called_once_with(
        prefix="fm__test_local",
        version=get_current_fm_version(),
        network_name="site-network",
        auto_save=False,
    )
    t.compose.set_all_services_restart.assert_called_once_with("always")


@pytest.mark.timeout(15)
def test_sync_adminer_plugin_installs_the_real_login_plugin(t):
    from frappe_manager.utils.helpers import get_template_path

    t.tools.sync_adminer_plugin()

    installed = t.adminer_dir / "000-fm-login.php"
    assert installed.read_bytes() == get_template_path("adminer/000-fm-login.php").read_bytes()


@pytest.mark.timeout(15)
def test_sync_adminer_plugin_overwrites_a_stale_plugin_from_an_older_fm(t):
    t.adminer_dir.mkdir(parents=True)
    (t.adminer_dir / "000-fm-login.php").write_text("stale")

    t.tools.sync_adminer_plugin()

    assert (t.adminer_dir / "000-fm-login.php").read_text() != "stale"


@pytest.mark.timeout(15)
def test_create_announces_and_delegates_to_generate_compose(t):
    with patch.object(BenchAdminTools, "generate_compose") as generate:
        t.tools.create()

    generate.assert_called_once_with()
    assert t.heads() == ["Generating admin tools configuration"]
    assert t.prints() == ["Generating admin tools configuration: Done"]


# ======================================================================================
# BenchAdminTools -- nginx location conf and the auth interaction
# ======================================================================================


@pytest.mark.timeout(15)
def test_location_conf_points_both_tools_at_their_prefixed_containers(t):
    t.tools.save_nginx_location_config()

    conf = t.location_conf.read_text()
    assert "set $mailpit_upstream fm__test_local__mailpit:8025;" in conf
    assert "set $adminer_upstream fm__test_local__adminer:8080;" in conf
    assert "location ^~ /mailpit/" in conf
    assert "location ^~ /adminer/" in conf


@pytest.mark.timeout(15)
def test_location_conf_gates_the_tools_itself_when_only_tools_auth_is_on(tmp_path):
    with ExitStack() as stack:
        t = ToolsHarness(tmp_path, stack, auth=_auth(web=False, tools=True))
        t.tools.save_nginx_location_config()

    conf = t.location_conf.read_text()
    assert f"auth_basic_user_file /etc/nginx/http_auth/{BENCH}.htpasswd;" in conf
    assert 'auth_basic "Restricted";' in conf
    assert "auth_basic off;" not in conf


@pytest.mark.timeout(15)
def test_location_conf_opts_out_of_the_inherited_gate_when_only_web_auth_is_on(tmp_path):
    with ExitStack() as stack:
        t = ToolsHarness(tmp_path, stack, auth=_auth(web=True, tools=False))
        t.tools.save_nginx_location_config()

    conf = t.location_conf.read_text()
    assert "auth_basic off;" in conf
    assert "auth_basic_user_file" not in conf


@pytest.mark.timeout(15)
def test_location_conf_emits_no_auth_directives_when_neither_surface_is_on(tmp_path):
    """Nothing to gate and nothing to inherit."""
    with ExitStack() as stack:
        t = ToolsHarness(tmp_path, stack, auth=_auth(web=False, tools=False))
        t.tools.save_nginx_location_config()

    assert "auth_basic" not in t.location_conf.read_text()


@pytest.mark.timeout(15)
def test_the_tools_gate_is_the_benchs_even_when_the_web_surface_is_gated_too(tmp_path):
    """The locations used to inherit the server gate when both surfaces were on, which was sound
    only while one credential pair served both.

    Per-site web auth broke that: a site with its own password would have had that password open
    the bench-wide Adminer, which reaches every schema on the bench. The location-level directive
    overrides the server-level one, so naming the bench's htpasswd here keeps the tools on bench
    credentials whatever a site does."""
    with ExitStack() as stack:
        t = ToolsHarness(tmp_path, stack, auth=_auth(web=True, tools=True))
        t.tools.save_nginx_location_config()

    conf = t.location_conf.read_text()
    assert "auth_basic " in conf
    assert f"auth_basic_user_file /etc/nginx/http_auth/{BENCH}.htpasswd;" in conf


@pytest.mark.timeout(15)
def test_location_conf_carries_the_allow_ip_exemptions(tmp_path):
    with ExitStack() as stack:
        t = ToolsHarness(tmp_path, stack, auth=_auth(web=False, tools=True, allow_ips=["10.0.0.0/8"]))
        t.tools.save_nginx_location_config()

    conf = t.location_conf.read_text()
    assert "satisfy any;" in conf
    assert "allow 10.0.0.0/8;" in conf
    assert "deny all;" in conf


@pytest.mark.timeout(15)
def test_location_conf_defaults_to_a_fresh_auth_config_when_the_bench_has_none(t):
    """auth is None on older benches; the default (web off, tools on) must still gate."""
    assert t.bench.bench_config.auth is None

    t.tools.save_nginx_location_config()

    assert "auth_basic_user_file" in t.location_conf.read_text()


@pytest.mark.timeout(15)
def test_location_conf_creates_the_site_directory_it_needs(t):
    """Was a pinned SUSPICION: the "create the parent" branch called ``mkdir`` on the conf FILE
    path rather than its parent, so it could not create a missing directory and raised instead.

    Rendering per site made the directory always one level deeper than anything else creates, so
    the branch had to start working."""
    import shutil

    shutil.rmtree(t.custom_dir)

    t.tools.save_nginx_location_config()

    assert t.location_conf.is_file()
    assert "/adminer/" in t.location_conf.read_text()


@pytest.mark.timeout(15)
def test_remove_location_conf_drops_only_the_tool_locations(t):
    t.tools.save_nginx_location_config()
    assert t.location_conf.exists()

    t.tools.remove_nginx_location_config()

    assert not t.location_conf.exists()
    # The prior bug: these two are shared with the web surface and owned by
    # ensure_fm_nginx_confs(); admin tools must never remove them.
    assert t.auth_conf.exists()
    assert t.htpasswd.exists()


@pytest.mark.timeout(15)
def test_remove_location_conf_is_a_noop_when_it_was_never_written(t):
    assert not t.location_conf.exists()

    t.tools.remove_nginx_location_config()  # must not raise

    assert t.auth_conf.exists()


# ======================================================================================
# BenchAdminTools -- common_site_config / mailpit
# ======================================================================================


MAILPIT_CONF = {
    "mail_port": 1025,
    "mail_server": "fm__test_local__mailpit",
    "disable_mail_smtp_authentication": 1,
}


@pytest.mark.timeout(15)
def test_reading_common_site_config_refuses_when_the_file_is_missing(t):
    with pytest.raises(BenchException) as exc:
        t.tools._get_common_site_config()

    assert "common_site_config.json not found." in str(exc.value)


@pytest.mark.timeout(15)
def test_configure_mailpit_adds_the_three_keys_and_preserves_the_rest(t):
    t.write_common_site_config({"db_host": "mariadb", "developer_mode": 1})

    t.tools.configure_mailpit_as_default_server()

    config = t.read_common_site_config()
    assert config == {"db_host": "mariadb", "developer_mode": 1, **MAILPIT_CONF}
    assert t.prints() == ["Configured Mailpit as default mail server"]


@pytest.mark.timeout(15)
def test_configure_mailpit_overwrites_a_foreign_mail_server(t):
    t.write_common_site_config({"mail_server": "smtp.example.com", "mail_port": 587})

    t.tools.configure_mailpit_as_default_server()

    assert t.read_common_site_config() == MAILPIT_CONF


@pytest.mark.timeout(15)
def test_remove_mailpit_deletes_only_the_keys_it_owns(t):
    t.write_common_site_config({**MAILPIT_CONF, "db_host": "mariadb"})

    t.tools.remove_mailpit_as_default_server()

    assert t.read_common_site_config() == {"db_host": "mariadb"}
    assert t.prints() == ["Removed Mailpit as default mail server"]


@pytest.mark.timeout(15)
def test_remove_mailpit_keeps_a_user_customised_value(t):
    """Only an exact value match is removed, so a hand-edited mail server survives disable."""
    t.write_common_site_config({**MAILPIT_CONF, "mail_server": "smtp.example.com"})

    t.tools.remove_mailpit_as_default_server()

    assert t.read_common_site_config() == {"mail_server": "smtp.example.com"}


@pytest.mark.timeout(15)
def test_remove_mailpit_tolerates_keys_that_were_never_written(t):
    t.write_common_site_config({"db_host": "mariadb"})

    t.tools.remove_mailpit_as_default_server()

    assert t.read_common_site_config() == {"db_host": "mariadb"}


# ======================================================================================
# BenchAdminTools -- readiness probe
# ======================================================================================


@pytest.mark.timeout(15)
def test_readiness_probes_each_tool_port_from_inside_its_own_container(t):
    t.tools.wait_till_services_started(interval=2, timeout=5)

    assert t.docker.compose.exec.call_args_list == [
        call(service="mailpit", command="timeout 2 nc -z localhost 8025", stream=False),
        call(service="adminer", command="timeout 2 nc -z localhost 8080", stream=False),
    ]


@pytest.mark.timeout(15)
def test_readiness_retries_until_the_probe_succeeds(t):
    t.docker.compose.exec.side_effect = [_docker_failure(), _docker_failure(), None, None]

    t.tools.wait_till_services_started(interval=1, timeout=5)

    assert t.docker.compose.exec.call_count == 4


@pytest.mark.timeout(15)
def test_readiness_gives_up_after_timeout_attempts_and_never_probes_the_second_tool(t):
    t.docker.compose.exec.side_effect = _docker_failure()

    with pytest.raises(AdminToolsFailedToStart):
        t.tools.wait_till_services_started(interval=1, timeout=3)

    # `timeout` is an attempt count, not seconds; mailpit failing aborts before adminer.
    assert t.docker.compose.exec.call_count == 3
    assert {c.kwargs["service"] for c in t.docker.compose.exec.call_args_list} == {"mailpit"}


# ======================================================================================
# BenchAdminTools -- enable / stop / disable
# ======================================================================================


@pytest.mark.timeout(15)
def test_enable_syncs_the_plugin_before_compose_up_so_the_bind_mount_source_exists(t):
    order = []
    t.docker.compose.up.side_effect = lambda **_kw: order.append("up")

    with patch.object(BenchAdminTools, "sync_adminer_plugin", side_effect=lambda: order.append("plugin")):
        t.tools.enable()

    assert order == ["plugin", "up"]


@pytest.mark.timeout(15)
def test_enable_brings_up_every_service_without_pulling(t):
    t.tools.enable(force_recreate_container=True)

    t.docker.compose.up.assert_called_once_with(
        services=[],
        detach=True,
        pull="never",
        force_recreate=True,
    )


@pytest.mark.timeout(15)
def test_enable_waits_then_writes_the_location_conf_then_reloads_nginx(t):
    order = []
    t.nginx_proxy.reload.side_effect = lambda: order.append("reload")

    with (
        patch.object(BenchAdminTools, "wait_till_services_started", side_effect=lambda: order.append("wait")),
        patch.object(BenchAdminTools, "save_nginx_location_config", side_effect=lambda: order.append("conf")),
    ):
        t.tools.enable()

    assert order == ["wait", "conf", "reload"]


@pytest.mark.timeout(15)
def test_enable_leaves_the_mail_server_alone_unless_force_configure(t):
    with patch.object(BenchAdminTools, "configure_mailpit_as_default_server") as configure:
        t.tools.enable(force_configure=False)
    configure.assert_not_called()

    with patch.object(BenchAdminTools, "configure_mailpit_as_default_server") as configure:
        t.tools.enable(force_configure=True)
    configure.assert_called_once_with()


@pytest.mark.timeout(15)
def test_enable_translates_a_docker_failure_and_stops_before_touching_nginx(t):
    t.docker.compose.up.side_effect = _docker_failure()

    with pytest.raises(AdminToolsFailedToStart) as exc:
        t.tools.enable()

    assert exc.value.compose_path == t.tools.compose_path
    # The SUSPICION that used to sit here is fixed: enable() passed an empty service list, so
    # the message never named what failed. It now passes the real list, like stop() always did.
    assert exc.value.services == ["mailpit", "adminer"]
    t.nginx_proxy.reload.assert_not_called()
    assert not t.location_conf.exists()


@pytest.mark.timeout(15)
def test_stop_stops_the_containers_with_a_short_grace_period(t):
    t.tools.stop()

    t.docker.compose.stop.assert_called_once_with(services=[], timeout=2)


@pytest.mark.timeout(15)
def test_stop_translates_a_docker_failure_and_names_the_services(t):
    t.docker.compose.stop.side_effect = _docker_failure()

    with pytest.raises(AdminToolsFailedToStop) as exc:
        t.tools.stop()

    assert exc.value.compose_path == t.tools.compose_path
    assert exc.value.services == ["mailpit", "adminer"]


@pytest.mark.timeout(15)
def test_disable_stops_unwires_nginx_reloads_unconfigures_mail_then_drops_the_plugin(t):
    t.write_common_site_config(dict(MAILPIT_CONF))
    t.tools.sync_adminer_plugin()
    t.tools.save_nginx_location_config()

    order = []
    t.docker.compose.stop.side_effect = lambda **_kw: order.append("stop")
    t.nginx_proxy.reload.side_effect = lambda: order.append("reload")

    with patch.object(
        BenchAdminTools,
        "remove_nginx_location_config",
        side_effect=lambda: order.append("unwire"),
    ):
        t.tools.disable()

    assert order == ["stop", "unwire", "reload"]
    assert t.read_common_site_config() == {}
    assert not t.adminer_dir.exists()


@pytest.mark.timeout(15)
def test_disable_removes_the_location_conf_but_never_the_shared_auth_state(t):
    """The prior bug in full: disabling admin tools must not destroy what
    ensure_fm_nginx_confs() owns."""
    t.write_common_site_config(dict(MAILPIT_CONF))
    t.tools.save_nginx_location_config()

    t.tools.disable()

    assert not t.location_conf.exists()
    assert t.auth_conf.read_text() == "# fm:auth server level\n"
    assert t.htpasswd.read_text() == "admin:hash\n"


@pytest.mark.timeout(15)
def test_disable_tolerates_a_bench_that_never_had_the_tools_enabled(t):
    t.write_common_site_config({"db_host": "mariadb"})

    t.tools.disable()  # no location conf, no adminer dir

    assert t.read_common_site_config() == {"db_host": "mariadb"}
    t.nginx_proxy.reload.assert_called_once_with()


@pytest.mark.timeout(15)
def test_disable_aborts_before_touching_nginx_when_the_containers_will_not_stop(t):
    t.tools.save_nginx_location_config()
    t.docker.compose.stop.side_effect = _docker_failure()

    with pytest.raises(AdminToolsFailedToStop):
        t.tools.disable()

    assert t.location_conf.exists()
    t.nginx_proxy.reload.assert_not_called()


@pytest.mark.timeout(15)
def test_disable_leaves_the_adminer_plugin_behind_when_the_site_config_is_missing(t):
    """SUSPICION (pinned): remove_mailpit_as_default_server() raises on a bench without a
    common_site_config.json, so disable() aborts half-done -- nginx already unwired and
    reloaded, but the adminer plugin directory still present."""
    t.tools.sync_adminer_plugin()
    t.tools.save_nginx_location_config()

    with pytest.raises(BenchException):
        t.tools.disable()

    assert not t.location_conf.exists()
    t.nginx_proxy.reload.assert_called_once_with()
    assert t.adminer_dir.exists()


# ======================================================================================
# BenchAdminTools -- is_running
# ======================================================================================


def _status(name, service, state):
    return {"Name": name, "Service": service, "State": state}


@pytest.mark.timeout(15)
def test_is_running_is_true_only_when_every_service_is_running(t):
    t.docker.compose.get_all_services_status.return_value = [
        _status("fm__test_local__mailpit", "mailpit", "running"),
        _status("fm__test_local__adminer", "adminer", "running"),
    ]

    assert t.tools.is_running() is True


@pytest.mark.timeout(15)
def test_is_running_is_false_when_one_service_is_not_running(t):
    t.docker.compose.get_all_services_status.return_value = [
        _status("fm__test_local__mailpit", "mailpit", "running"),
        _status("fm__test_local__adminer", "adminer", "exited"),
    ]

    assert t.tools.is_running() is False


@pytest.mark.timeout(15)
def test_is_running_is_false_when_a_service_has_no_container_at_all(t):
    t.docker.compose.get_all_services_status.return_value = [
        _status("fm__test_local__mailpit", "mailpit", "running"),
    ]

    assert t.tools.is_running() is False


@pytest.mark.timeout(15)
def test_is_running_ignores_a_running_container_belonging_to_another_bench(t):
    """Statuses are matched by container name, so another bench's adminer cannot count."""
    t.docker.compose.get_all_services_status.return_value = [
        _status("fm__test_local__mailpit", "mailpit", "running"),
        _status("fm__other_local__adminer", "adminer", "running"),
    ]

    assert t.tools.is_running() is False


@pytest.mark.timeout(15)
def test_is_running_is_false_when_the_compose_file_declares_no_services(t):
    """all([]) would be True; the empty-services guard exists to prevent that."""
    t.compose.get_services_list.return_value = []
    t.docker.compose.get_all_services_status.return_value = []

    assert t.tools.is_running() is False


@pytest.mark.timeout(15)
def test_is_running_swallows_a_docker_failure_and_reports_not_running(t):
    t.docker.compose.get_all_services_status.side_effect = _docker_failure()

    assert t.tools.is_running() is False


# ------------------- host_name belongs to the site the domain serves, not the bench's own


@pytest.mark.timeout(15)
def test_add_writes_host_name_to_the_site_the_domain_serves(h):
    """A certificate for a SIBLING site used to rewrite the primary site's `host_name`.

    Frappe builds absolute URLs from `host_name`, so the primary began issuing links, password
    resets and emails pointing at the sibling's domain, while the sibling that actually gained the
    certificate kept its old value. Both sites wrong from one command, and nothing said so.
    """
    h.set_sites({DOMAIN: [], "second.example.com": []})

    _add(h, domain="second.example.com", dry_run=False)

    assert h.site_config_writes() == [
        ("second.example.com", {"host_name": "https://second.example.com"})
    ]


@pytest.mark.timeout(15)
def test_remove_writes_host_name_to_the_site_the_domain_serves(h):
    h.set_sites({DOMAIN: [], "second.example.com": []})
    h.cert_manager.list_certificates.return_value = []

    _remove(h, domain="second.example.com", yes=True)

    assert h.site_config_writes() == [
        ("second.example.com", {"host_name": "http://second.example.com"})
    ]


@pytest.mark.timeout(15)
def test_an_alias_certificate_writes_the_site_that_owns_the_alias(h):
    """An alias is a hostname OF a site, so a certificate for it moves that site's host_name."""
    h.set_sites({DOMAIN: [], "second.example.com": ["www.second.example.com"]})

    _add(h, domain="www.second.example.com", dry_run=False)

    assert h.site_config_writes() == [
        ("second.example.com", {"host_name": "https://www.second.example.com"})
    ]


@pytest.mark.timeout(15)
def test_an_unmapped_domain_leaves_every_host_name_alone(h):
    """Rather than fall back to the bench's own site, which is the bug. No site owns it, so no
    site's `host_name` is the right one to move."""
    h.set_sites({DOMAIN: []})
    h.bench.bench_config.get_site_mappings.return_value = {}

    _add(h, dry_run=False)

    assert h.site_config_writes() == []
