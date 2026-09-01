"""Characterization of `frappe_manager/commands/ssl/external_helpers.py`.

This module is the largest one no test imported, and it is also the most duplicated: the
same "build storage config -> link manager -> nginx controller -> (standalone nginx) ->
service factory -> SSLCertificateManager" preamble is spelled out five times across
`_add_external_certificate`, `_remove_external_certificate` and `_renew_external_certificate`,
plus a truncated fourth copy in `_list_external_certificates`.

Those blocks are *near* identical, and a dedup pass will erase exactly the parts that are
not. So the wiring tests below pin the DIFFERENCES on purpose:

  * `SSLStorageConfig(...)`  - identical six kwargs in all four call sites (add/remove/renew/list).
  * `StandaloneNginxConfigManager` - built by add and remove, NOT by renew, NOT by list.
  * `SSLCertificateManager.certificates` - `[]` in add (cert is handed to `add_certificate`
    afterwards), `[cert]` in remove and renew.
  * `services_manager.nginx_controller` - read by add/remove/renew, never by list.
  * spinner text, `change_head` text and failure message differ per function.

Everything external is mocked at its seam: no docker daemon, no acme.sh, no network, no real
certificates. Filesystem access is confined to `tmp_path`.
"""

from contextlib import ExitStack, suppress
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
import typer

from frappe_manager import SSL_RENEW_BEFORE_DAYS
from frappe_manager.commands.ssl import external_helpers
from frappe_manager.output_manager.silent_output import SilentOutputHandler
from frappe_manager.ssl_manager import LETSENCRYPT_PREFERRED_CHALLENGE, SUPPORTED_SSL_TYPES
from frappe_manager.ssl_manager.certificate import RETIRED_CERTIFICATE_KEYS
from frappe_manager.ssl_manager.certificate_exceptions import SSLCertificateNotDueForRenewalError
from frappe_manager.ssl_manager.external_domain_manager import ExternalDomainConfig
from frappe_manager.ssl_manager.letsencrypt_certificate import LetsencryptSSLCertificate
from frappe_manager.ssl_manager.standalone_nginx_config_manager import StandaloneNginxConfigManager

MODULE = "frappe_manager.commands.ssl.external_helpers"
DOMAIN = "app.example.com"


# --------------------------------------------------------------------------------------
# harness
# --------------------------------------------------------------------------------------


def _proxy_dirs(tmp_path: Path) -> SimpleNamespace:
    """Sentinel host/container path pairs, distinct per directory so kwarg swaps are visible."""

    def pair(name: str) -> SimpleNamespace:
        return SimpleNamespace(host=tmp_path / "host" / name, container=Path("/ctr") / name)

    return SimpleNamespace(
        ssl=pair("ssl"),
        certs=pair("certs"),
        vhostd=pair("vhostd"),
        html=pair("html"),
        confd=pair("confd"),
    )


def _expected_storage_kwargs(dirs: SimpleNamespace) -> dict:
    return {
        "ssl_dir": dirs.ssl.host,
        "ssl_dir_container": dirs.ssl.container,
        "certs_dir": dirs.certs.host,
        "certs_dir_container": dirs.certs.container,
        "vhostd_dir": dirs.vhostd.host,
        "webroot_dir": dirs.html.host,
    }


class Harness:
    """All collaborators of external_helpers, patched at the module boundary."""

    def __init__(self, tmp_path: Path, stack: ExitStack):
        self.dirs = _proxy_dirs(tmp_path)
        self.nginx_controller = MagicMock(name="nginx_controller")

        self.services = MagicMock(name="services_manager")
        self.services.path = tmp_path / "services"
        self.services.proxy_storage = SimpleNamespace(dirs=self.dirs)
        # nginx_controller is an attribute read; MagicMock records the access via a property-like
        # sentinel so "did this function even look at it" is assertable.
        self.services.nginx_controller = self.nginx_controller

        self.output = MagicMock(name="output_handler")
        # temporary_stop() reads these two; keep it a deterministic no-op.
        self.output.is_spinner_active = False
        self.output._current_text = None

        self.ctx = MagicMock(name="ctx")
        self.ctx.obj = {"services": self.services}
        self.ctx.get_help.return_value = "USAGE-HELP"

        def p(target):
            return stack.enter_context(patch(f"{MODULE}.{target}"))

        self.get_output_handler = p("get_output_handler")
        self.get_output_handler.return_value = self.output

        self.ExternalDomainConfigManager = p("ExternalDomainConfigManager")
        self.external_manager = self.ExternalDomainConfigManager.return_value
        self.external_manager.domain_exists.return_value = False

        self.SSLStorageConfig = p("SSLStorageConfig")
        self.storage_config = self.SSLStorageConfig.return_value

        self.CertificateLinkManager = p("CertificateLinkManager")
        self.link_manager = self.CertificateLinkManager.return_value

        self.StandaloneNginxConfigManager = p("StandaloneNginxConfigManager")
        self.standalone_nginx = self.StandaloneNginxConfigManager.return_value

        self.SSLCertificateManager = p("SSLCertificateManager")
        self.cert_manager = self.SSLCertificateManager.return_value

        self.create_certificate_service = p("create_certificate_service")

    # -- convenience readers -----------------------------------------------------------

    @property
    def storage_kwargs(self) -> dict:
        assert self.SSLStorageConfig.call_args.args == ()
        return self.SSLStorageConfig.call_args.kwargs

    @property
    def cert_manager_kwargs(self) -> dict:
        assert self.SSLCertificateManager.call_args.args == ()
        return self.SSLCertificateManager.call_args.kwargs

    def prints(self) -> list[str]:
        return [c.args[0] if c.args else c.kwargs["message"] for c in self.output.print.call_args_list]

    def heads(self) -> list[str]:
        return [c.args[0] for c in self.output.change_head.call_args_list]

    def spinner_texts(self) -> list[str]:
        return [c.args[0] for c in self.output.start.call_args_list]

    def config_path(self) -> Path:
        return self.ExternalDomainConfigManager.call_args.args[0]


@pytest.fixture
def h(tmp_path):
    with ExitStack() as stack:
        yield Harness(tmp_path, stack)


def _cert(domain: str = DOMAIN) -> LetsencryptSSLCertificate:
    return LetsencryptSSLCertificate(
        domain=domain,
        ssl_type=SUPPORTED_SSL_TYPES.le,
        challenge_type=LETSENCRYPT_PREFERRED_CHALLENGE.http01,
    )


def _dns_validator(stack: ExitStack) -> MagicMock:
    """`DNSValidator` is imported lazily from its own module inside the function body."""
    return stack.enter_context(patch("frappe_manager.ssl_manager.dns_validator.DNSValidator"))


def _add(h: Harness, **kw):
    kwargs = {
        "challenge": LETSENCRYPT_PREFERRED_CHALLENGE.http01,
        "cname": None,
        "dry_run": False,
        "skip_dns_check": True,
    }
    kwargs.update(kw)
    return external_helpers._add_external_certificate(h.ctx, DOMAIN, **kwargs)


# --------------------------------------------------------------------------------------
# shared -- config file location
# --------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "call",
    [
        pytest.param(_add, id="add"),
        pytest.param(
            lambda h: external_helpers._remove_external_certificate(h.ctx, DOMAIN, yes=True),
            id="remove",
        ),
        pytest.param(
            lambda h: external_helpers._renew_external_certificate(h.ctx, DOMAIN, dry_run=False),
            id="renew",
        ),
        pytest.param(lambda h: external_helpers._list_external_certificates(h.ctx), id="list"),
        pytest.param(
            lambda h: external_helpers._renew_all_external_certificates(h.ctx, dry_run=False),
            id="renew_all",
        ),
    ],
)
def test_every_entrypoint_reads_the_same_external_domains_toml(h, call):
    """All five entrypoints resolve <services>/nginx-proxy/external_domains.toml themselves."""
    # domain_exists=True short-circuits add/remove/renew before any collaborator is built.
    h.external_manager.domain_exists.return_value = True
    h.external_manager.list_domains.return_value = []
    with (
        patch(f"{MODULE}._get_non_bench_domains_from_nginx", return_value=[]),
        suppress(typer.Exit),
    ):
        call(h)
    assert h.config_path() == h.services.path / "nginx-proxy" / "external_domains.toml"


# --------------------------------------------------------------------------------------
# _add_external_certificate -- guards
# --------------------------------------------------------------------------------------


def test_add_rejects_domain_that_already_has_a_certificate(h):
    h.external_manager.domain_exists.return_value = True

    with pytest.raises(typer.Exit) as exc:
        _add(h)

    assert exc.value.exit_code == 1
    h.output.display_error.assert_called_once_with(f"Certificate already exists for external domain '{DOMAIN}'")
    assert h.prints() == [
        "To update certificate:",
        f"  1. Remove existing: fm ssl remove --standalone {DOMAIN}",
        f"  2. Add new: fm ssl add --standalone {DOMAIN}",
    ]
    h.SSLStorageConfig.assert_not_called()


def test_add_rejects_cname_without_dns01_and_echoes_help(h):
    with patch.object(typer, "echo") as echo, pytest.raises(typer.Exit) as exc:
        _add(h, cname="deleg.fm.com", challenge=LETSENCRYPT_PREFERRED_CHALLENGE.http01)

    assert exc.value.exit_code == 1
    h.output.display_error.assert_called_once_with("CNAME delegation (--cname) requires DNS-01 challenge")
    echo.assert_called_once_with("USAGE-HELP")
    h.SSLStorageConfig.assert_not_called()


def test_add_allows_cname_with_dns01(h):
    _add(h, cname="deleg.fm.com", challenge=LETSENCRYPT_PREFERRED_CHALLENGE.dns01)

    h.output.display_error.assert_not_called()
    h.cert_manager.add_certificate.assert_called_once()


# --------------------------------------------------------------------------------------
# _add_external_certificate -- certificate object construction
# --------------------------------------------------------------------------------------


def test_add_without_cname_builds_an_undelegated_letsencrypt_certificate(h):
    _add(h, challenge=LETSENCRYPT_PREFERRED_CHALLENGE.dns01)

    cert = h.cert_manager.add_certificate.call_args.args[0]
    assert type(cert) is LetsencryptSSLCertificate
    assert (cert.domain, cert.ssl_type, cert.challenge_type) == (
        DOMAIN,
        SUPPORTED_SSL_TYPES.le,
        LETSENCRYPT_PREFERRED_CHALLENGE.dns01,
    )
    assert cert.delegation_cname is None
    # A standalone certificate carries no credential: they are resolved from the global
    # `[ssl.dns_providers]` at issuance, and a copy here would outlive revocation.
    assert RETIRED_CERTIFICATE_KEYS.isdisjoint(cert.model_dump())


def test_add_with_cname_builds_a_certificate_carrying_the_delegation(h):
    with ExitStack() as stack:
        validator = _dns_validator(stack).return_value
        validator.validate_cname_for_acme.return_value = SimpleNamespace(valid=True, actual_value=None)
        _add(h, cname="deleg.fm.com", challenge=LETSENCRYPT_PREFERRED_CHALLENGE.dns01, skip_dns_check=False)

    cert = h.cert_manager.add_certificate.call_args.args[0]
    assert type(cert) is LetsencryptSSLCertificate
    # The field, not a class, is what makes acme.sh receive --challenge-alias.
    assert cert.delegation_cname == "deleg.fm.com"
    assert cert.challenge_type == LETSENCRYPT_PREFERRED_CHALLENGE.dns01
    assert "Using CNAME delegation: deleg.fm.com" in h.prints()


# --------------------------------------------------------------------------------------
# _add_external_certificate -- DNS pre-flight
# --------------------------------------------------------------------------------------


def test_add_cname_single_validation_success_prints_verified_and_continues(h):
    with ExitStack() as stack:
        validator = _dns_validator(stack).return_value
        validator.validate_cname_for_acme.return_value = SimpleNamespace(valid=True, actual_value=None)
        _add(h, cname="deleg.fm.com", challenge=LETSENCRYPT_PREFERRED_CHALLENGE.dns01, skip_dns_check=False)

        validator.validate_cname_for_acme.assert_called_once_with(DOMAIN, "deleg.fm.com")
        validator.wait_for_cname_propagation.assert_not_called()

    assert "CNAME record verified" in h.prints()
    assert f"Validating DNS configuration for {DOMAIN}" in h.heads()
    h.cert_manager.add_certificate.assert_called_once()


def test_add_cname_validation_failure_with_wrong_target_reports_current_value(h):
    with ExitStack() as stack:
        validator = _dns_validator(stack).return_value
        validator.validate_cname_for_acme.return_value = SimpleNamespace(
            valid=False, actual_value="_acme-challenge.other.com"
        )
        with pytest.raises(typer.Exit) as exc:
            _add(h, cname="deleg.fm.com", challenge=LETSENCRYPT_PREFERRED_CHALLENGE.dns01, skip_dns_check=False)

    assert exc.value.exit_code == 1
    assert [c.args[0] for c in h.output.display_error.call_args_list] == [
        "DNS validation failed",
        "Failed to add certificate: 1",
    ]
    prints = h.prints()
    assert f"  _acme-challenge.{DOMAIN}  →  _acme-challenge.deleg.fm.com" in prints
    assert "Current CNAME:" in prints
    assert f"  _acme-challenge.{DOMAIN}  →  _acme-challenge.other.com" in prints
    assert "The CNAME record exists but points to the wrong target." in prints
    assert "CNAME record not found." not in prints
    assert "To skip this check, use: --skip-dns-check" in prints
    assert "To wait for propagation, use: --wait-for-dns" in prints
    h.SSLStorageConfig.assert_not_called()


def test_add_cname_validation_failure_without_value_reports_missing_record(h):
    with ExitStack() as stack:
        validator = _dns_validator(stack).return_value
        validator.validate_cname_for_acme.return_value = SimpleNamespace(valid=False, actual_value=None)
        with pytest.raises(typer.Exit):
            _add(h, cname="deleg.fm.com", challenge=LETSENCRYPT_PREFERRED_CHALLENGE.dns01, skip_dns_check=False)

    prints = h.prints()
    assert "CNAME record not found." in prints
    assert "Current CNAME:" not in prints
    assert "The CNAME record exists but points to the wrong target." not in prints


def test_add_wait_for_dns_polls_propagation_with_five_minute_budget(h):
    with ExitStack() as stack:
        validator = _dns_validator(stack).return_value
        validator.wait_for_cname_propagation.return_value = SimpleNamespace(
            propagated=True, message="propagated in 42s"
        )
        _add(
            h,
            cname="deleg.fm.com",
            challenge=LETSENCRYPT_PREFERRED_CHALLENGE.dns01,
            skip_dns_check=False,
            wait_for_dns=True,
        )

        validator.wait_for_cname_propagation.assert_called_once_with(
            domain=DOMAIN, challenge_alias="deleg.fm.com", timeout=300, check_interval=30
        )
        validator.validate_cname_for_acme.assert_not_called()

    assert "propagated in 42s" in h.prints()


def test_add_wait_for_dns_timeout_aborts_before_touching_nginx(h):
    with ExitStack() as stack:
        validator = _dns_validator(stack).return_value
        validator.wait_for_cname_propagation.return_value = SimpleNamespace(propagated=False, message="nope")
        with pytest.raises(typer.Exit) as exc:
            _add(
                h,
                cname="deleg.fm.com",
                challenge=LETSENCRYPT_PREFERRED_CHALLENGE.dns01,
                skip_dns_check=False,
                wait_for_dns=True,
            )

    assert exc.value.exit_code == 1
    # The Exit(1) is raised INSIDE the function's own try block, so `except Exception` catches
    # it and appends a second, less useful error line. Pinned as-is (suspected wart).
    assert [c.args[0] for c in h.output.display_error.call_args_list] == [
        "DNS propagation timeout",
        "Failed to add certificate: 1",
    ]
    assert "CNAME record did not propagate within 5 minutes." in h.prints()
    h.StandaloneNginxConfigManager.assert_not_called()


def test_add_http01_checks_a_record_and_only_warns_when_missing(h):
    with ExitStack() as stack:
        validator = _dns_validator(stack).return_value
        validator.validate_a_record.return_value = SimpleNamespace(valid=False, actual_value=None)
        _add(h, challenge=LETSENCRYPT_PREFERRED_CHALLENGE.http01, skip_dns_check=False)

        validator.validate_a_record.assert_called_once_with(DOMAIN)

    h.output.warning.assert_called_once_with(f"Domain {DOMAIN} doesn't have an A record")
    assert f"Make sure {DOMAIN} points to this server's IP address." in h.prints()
    # A missing A record is NOT fatal: issuance still proceeds.
    h.cert_manager.add_certificate.assert_called_once()


def test_add_http01_valid_a_record_reports_resolved_ip(h):
    with ExitStack() as stack:
        validator = _dns_validator(stack).return_value
        validator.validate_a_record.return_value = SimpleNamespace(valid=True, actual_value="203.0.113.7")
        _add(h, challenge=LETSENCRYPT_PREFERRED_CHALLENGE.http01, skip_dns_check=False)

    assert "Domain resolves to 203.0.113.7" in h.prints()
    h.output.warning.assert_not_called()


def test_add_dns01_without_cname_skips_the_a_record_check(h):
    """The A-record pre-flight is gated on http01, not merely on skip_dns_check."""
    with ExitStack() as stack:
        validator_cls = _dns_validator(stack)
        _add(h, challenge=LETSENCRYPT_PREFERRED_CHALLENGE.dns01, skip_dns_check=False)
        validator_cls.assert_not_called()


def test_add_skip_dns_check_bypasses_validator_entirely(h):
    with ExitStack() as stack:
        validator_cls = _dns_validator(stack)
        _add(h, challenge=LETSENCRYPT_PREFERRED_CHALLENGE.http01, skip_dns_check=True)
        validator_cls.assert_not_called()


# --------------------------------------------------------------------------------------
# _add_external_certificate -- the duplicated wiring block (copy #1)
# --------------------------------------------------------------------------------------


def test_add_builds_storage_config_from_proxy_storage_dirs(h):
    _add(h)
    assert h.storage_kwargs == _expected_storage_kwargs(h.dirs)


def test_add_wires_link_manager_and_standalone_nginx_from_that_storage(h):
    _add(h)

    h.CertificateLinkManager.assert_called_once_with(h.storage_config)
    h.StandaloneNginxConfigManager.assert_called_once_with(
        conf_dir=h.dirs.confd.host,
        webroot_dir_container=h.dirs.html.container,
        certs_dir_container=h.dirs.certs.container,
    )


def test_add_constructs_cert_manager_with_an_empty_certificate_list(h):
    """The distinguishing detail of copy #1: add starts empty and passes the cert to
    add_certificate(), while remove/renew seed `certificates=[cert]`."""
    _add(h)

    kwargs = h.cert_manager_kwargs
    assert kwargs["certificates"] == []
    assert kwargs["link_manager"] is h.link_manager
    assert kwargs["nginx_controller"] is h.nginx_controller
    assert kwargs["storage_config"] is h.storage_config
    assert kwargs["output_handler"] is h.output
    assert kwargs["config_save_callback"] is None


def test_add_service_factory_delegates_to_create_certificate_service(h):
    _add(h)

    factory = h.cert_manager_kwargs["service_factory"]
    cert, cfg, handler = object(), object(), object()
    assert factory(cert, cfg, handler) is h.create_certificate_service.return_value
    h.create_certificate_service.assert_called_once_with(cert, cfg, handler)


# --------------------------------------------------------------------------------------
# _add_external_certificate -- happy-path ordering
# --------------------------------------------------------------------------------------


def test_add_orders_http_config_reload_issue_https_config_reload(h):
    order = []
    h.standalone_nginx.create_http_config.side_effect = lambda d: order.append(("http", d))
    h.standalone_nginx.create_https_config.side_effect = lambda d: order.append(("https", d))
    h.nginx_controller.reload.side_effect = lambda: order.append(("reload", None))
    h.cert_manager.add_certificate.side_effect = lambda _cert, dry_run: order.append(("issue", dry_run))

    _add(h, dry_run=False)

    assert order == [
        ("http", DOMAIN),
        ("reload", None),
        ("issue", False),
        ("https", DOMAIN),
        ("reload", None),
    ]
    assert h.nginx_controller.reload.call_count == 2


def test_add_uses_a_spinner_labelled_for_generation(h):
    _add(h)
    assert h.spinner_texts() == [f"Generating SSL certificate for {DOMAIN}"]
    assert h.output.stop.call_count == 1


def test_add_persists_the_external_domain_config_on_success(h):
    _add(h, challenge=LETSENCRYPT_PREFERRED_CHALLENGE.dns01, cname="deleg.fm.com", skip_dns_check=True)

    h.external_manager.add_domain.assert_called_once()
    saved = h.external_manager.add_domain.call_args.args[0]
    assert isinstance(saved, ExternalDomainConfig)
    assert saved.domain == DOMAIN
    assert saved.ssl_type == "letsencrypt"
    assert saved.challenge_type == "dns01"
    assert saved.delegation_cname == "deleg.fm.com"
    assert saved.acme_client == "acme.sh"
    # added_at is an ISO timestamp produced at save time.
    assert datetime.fromisoformat(saved.added_at)


def test_add_stores_lowercased_challenge_and_null_cname_for_http01(h):
    _add(h, challenge=LETSENCRYPT_PREFERRED_CHALLENGE.http01)

    saved = h.external_manager.add_domain.call_args.args[0]
    assert saved.challenge_type == "http01"
    assert saved.delegation_cname is None


def test_add_dry_run_never_writes_an_https_vhost_and_saves_nothing(h):
    """Regression (was: ...still_runs_the_full_nginx_and_issue_dance...).

    The previous version of this test pinned `create_https_config` being called on a dry run.
    That was the bug itself, not a contract: `add_certificate(dry_run=True)` deliberately skips
    the symlinks, so the HTTPS vhost references cert files that do not exist -- a fatal error
    for the SHARED nginx-proxy conf.d, and unreachable through `fm ssl remove/list` because
    the domain was never written to external_domains.toml.
    """
    _add(h, dry_run=True)

    h.cert_manager.add_certificate.assert_called_once()
    assert h.cert_manager.add_certificate.call_args.kwargs == {"dry_run": True}
    h.standalone_nginx.create_https_config.assert_not_called()
    # the step-1 ACME-challenge vhost is withdrawn again, and nginx reloaded to forget it
    h.standalone_nginx.remove_config.assert_called_once_with(DOMAIN)
    assert h.nginx_controller.reload.call_count == 2
    h.external_manager.add_domain.assert_not_called()
    assert f"SSL certificate added for {DOMAIN}" not in h.prints()


def test_add_dry_run_leaves_the_shared_confd_directory_exactly_as_it_found_it(h):
    """The blast-radius test, with the REAL config writer instead of a mock.

    A leftover `<domain>.conf` naming absent certificate files breaks `nginx -s reload` for
    every bench the global proxy fronts, and breaks its next start outright.
    """
    confd = h.dirs.confd.host
    with patch(f"{MODULE}.StandaloneNginxConfigManager", StandaloneNginxConfigManager):
        _add(h, dry_run=True)

    assert not (confd / f"{DOMAIN}.conf").exists()
    assert sorted(p.name for p in confd.iterdir()) == []


def test_add_without_dry_run_does_write_the_real_https_vhost(h):
    """Counterpart of the dry-run test: the guard must not disarm the real path."""
    confd = h.dirs.confd.host
    with patch(f"{MODULE}.StandaloneNginxConfigManager", StandaloneNginxConfigManager):
        _add(h, dry_run=False)

    written = (confd / f"{DOMAIN}.conf").read_text()
    assert f"ssl_certificate /ctr/certs/{DOMAIN}.crt;" in written


def test_add_success_prints_the_docker_compose_instructions(h):
    _add(h)

    prints = h.prints()
    assert f"SSL certificate added for {DOMAIN}" in prints
    assert f"         VIRTUAL_HOST: {DOMAIN}" in prints
    assert "         - fm-global-frontend-network" in prints
    assert f"3. Access your app at: https://{DOMAIN}" in prints


def test_add_head_before_https_step_interpolates_the_domain(h):
    """Was pinned as an unformatted literal (missing f prefix); the placeholder is now substituted.

    The old assertions characterized the defect rather than the contract, so they are inverted.
    """
    _add(h)
    assert f"Enabling HTTPS for {DOMAIN}" in h.heads()
    assert "Enabling HTTPS for {domain}" not in h.heads()


# --------------------------------------------------------------------------------------
# _add_external_certificate -- failure + cleanup paths
# --------------------------------------------------------------------------------------


def test_add_issuance_failure_removes_nginx_config_and_exits(h):
    h.cert_manager.add_certificate.side_effect = RuntimeError("acme boom")

    with pytest.raises(typer.Exit) as exc:
        _add(h)

    assert exc.value.exit_code == 1
    h.standalone_nginx.remove_config.assert_called_once_with(DOMAIN)
    # one reload for the http config, one for the cleanup
    assert h.nginx_controller.reload.call_count == 2
    h.standalone_nginx.create_https_config.assert_not_called()
    h.external_manager.add_domain.assert_not_called()
    h.output.display_error.assert_called_once_with("Failed to add certificate: acme boom")
    assert "Cleaning up after certificate generation failure" in h.heads()
    # Issuance cleanup does NOT try to remove a certificate: there is none yet.
    h.cert_manager.remove_certificate_by_domain.assert_not_called()


def test_add_issuance_failure_with_failing_cleanup_debugs_and_still_exits(h):
    h.cert_manager.add_certificate.side_effect = RuntimeError("acme boom")
    h.standalone_nginx.remove_config.side_effect = OSError("cleanup boom")

    with pytest.raises(typer.Exit):
        _add(h)

    h.output.debug.assert_called_once_with("Failed to clean up nginx config: cleanup boom")
    # the original error, not the cleanup error, is what the user is told about
    h.output.display_error.assert_called_once_with("Failed to add certificate: acme boom")


def test_add_https_config_failure_also_revokes_the_issued_certificate(h):
    h.standalone_nginx.create_https_config.side_effect = RuntimeError("https boom")

    with pytest.raises(typer.Exit) as exc:
        _add(h)

    assert exc.value.exit_code == 1
    # This is the difference from the issuance-failure path: the cert already exists.
    h.cert_manager.remove_certificate_by_domain.assert_called_once_with(DOMAIN)
    h.standalone_nginx.remove_config.assert_called_once_with(DOMAIN)
    assert "Cleaning up after HTTPS configuration failure" in h.heads()
    assert "Cleaned up nginx configuration and certificate" in h.prints()
    h.output.display_error.assert_called_once_with("Failed to add certificate: https boom")
    h.external_manager.add_domain.assert_not_called()


def test_add_https_reload_failure_takes_the_same_cleanup_path(h):
    h.nginx_controller.reload.side_effect = [None, RuntimeError("reload boom")]

    with pytest.raises(typer.Exit):
        _add(h)

    h.cert_manager.remove_certificate_by_domain.assert_called_once_with(DOMAIN)
    h.output.display_error.assert_called_once_with("Failed to add certificate: reload boom")


def test_add_https_cleanup_failure_uses_its_own_debug_message(h):
    h.standalone_nginx.create_https_config.side_effect = RuntimeError("https boom")
    h.cert_manager.remove_certificate_by_domain.side_effect = OSError("revoke boom")

    with pytest.raises(typer.Exit):
        _add(h)

    h.output.debug.assert_called_once_with("Failed to clean up after HTTPS config failure: revoke boom")
    # Regression: a failing certificate removal must not abort the rest of the cleanup. The whole
    # point of this handler is to delete the nginx config that would otherwise be orphaned, and
    # remove_certificate_by_domain has several ways to blow up here (unregistered domain, or its
    # own nginx restart, which is likely to fail precisely when an HTTPS reload just did).
    h.standalone_nginx.remove_config.assert_called_once_with(DOMAIN)
    # one reload for the http config, one for the cleanup
    assert h.nginx_controller.reload.call_count == 2


def test_add_value_error_reports_the_same_message_as_any_other_exception(h):
    h.standalone_nginx.create_http_config.side_effect = ValueError("bad value")

    with pytest.raises(typer.Exit) as exc:
        _add(h)

    assert exc.value.exit_code == 1
    h.output.display_error.assert_called_once_with("Failed to add certificate: bad value")


# --------------------------------------------------------------------------------------
# _remove_external_certificate
# --------------------------------------------------------------------------------------


def test_remove_rejects_unknown_domain(h):
    h.external_manager.domain_exists.return_value = False

    with pytest.raises(typer.Exit) as exc:
        external_helpers._remove_external_certificate(h.ctx, DOMAIN, yes=True)

    assert exc.value.exit_code == 1
    h.output.display_error.assert_called_once_with(f"Certificate does not exist for external domain '{DOMAIN}'")
    h.SSLStorageConfig.assert_not_called()


def test_remove_prompts_when_not_confirmed_and_cancels_with_exit_zero(h):
    h.external_manager.domain_exists.return_value = True
    h.output.prompt_ask.return_value = "no"

    with pytest.raises(typer.Exit) as exc:
        external_helpers._remove_external_certificate(h.ctx, DOMAIN, yes=False)

    assert exc.value.exit_code == 0
    h.output.prompt_ask.assert_called_once_with(
        prompt=f"Remove SSL certificate for {DOMAIN}?",
        choices=["yes", "no"],
        default="no",
        required_flag="--yes or -y",
    )
    assert "Cancelled." in h.prints()
    h.cert_manager.remove_certificate_by_domain.assert_not_called()


def test_remove_proceeds_when_prompt_answered_yes(h):
    h.external_manager.domain_exists.return_value = True
    h.output.prompt_ask.return_value = "yes"

    external_helpers._remove_external_certificate(h.ctx, DOMAIN, yes=False)

    h.cert_manager.remove_certificate_by_domain.assert_called_once_with(DOMAIN)


def test_remove_with_yes_flag_never_prompts(h):
    h.external_manager.domain_exists.return_value = True

    external_helpers._remove_external_certificate(h.ctx, DOMAIN, yes=True)

    h.output.prompt_ask.assert_not_called()


def test_remove_builds_the_same_storage_config_and_standalone_nginx_as_add(h):
    h.external_manager.domain_exists.return_value = True

    external_helpers._remove_external_certificate(h.ctx, DOMAIN, yes=True)

    assert h.storage_kwargs == _expected_storage_kwargs(h.dirs)
    h.CertificateLinkManager.assert_called_once_with(h.storage_config)
    h.StandaloneNginxConfigManager.assert_called_once_with(
        conf_dir=h.dirs.confd.host,
        webroot_dir_container=h.dirs.html.container,
        certs_dir_container=h.dirs.certs.container,
    )


def test_remove_seeds_cert_manager_with_the_reconstructed_certificate(h):
    h.external_manager.domain_exists.return_value = True
    cert = _cert()
    h.external_manager.to_ssl_certificate.return_value = cert

    external_helpers._remove_external_certificate(h.ctx, DOMAIN, yes=True)

    kwargs = h.cert_manager_kwargs
    assert kwargs["certificates"] == [cert]
    assert kwargs["nginx_controller"] is h.nginx_controller
    assert kwargs["config_save_callback"] is None
    h.external_manager.to_ssl_certificate.assert_called_once_with(DOMAIN)


def test_remove_service_factory_delegates_to_create_certificate_service(h):
    h.external_manager.domain_exists.return_value = True

    external_helpers._remove_external_certificate(h.ctx, DOMAIN, yes=True)

    factory = h.cert_manager_kwargs["service_factory"]
    cert, cfg, handler = object(), object(), object()
    assert factory(cert, cfg, handler) is h.create_certificate_service.return_value
    h.create_certificate_service.assert_called_once_with(cert, cfg, handler)


def test_remove_orders_cert_removal_config_removal_reload_then_config_prune(h):
    h.external_manager.domain_exists.return_value = True
    order = []
    h.cert_manager.remove_certificate_by_domain.side_effect = lambda d: order.append(("cert", d))
    h.standalone_nginx.remove_config.side_effect = lambda d: order.append(("nginx-config", d))
    h.nginx_controller.reload.side_effect = lambda: order.append(("reload", None))
    h.external_manager.remove_domain.side_effect = lambda d: order.append(("toml", d))

    external_helpers._remove_external_certificate(h.ctx, DOMAIN, yes=True)

    assert order == [
        ("cert", DOMAIN),
        ("nginx-config", DOMAIN),
        ("reload", None),
        ("toml", DOMAIN),
    ]
    assert f"SSL certificate removed for {DOMAIN}" in h.prints()
    assert h.spinner_texts() == [f"Removing SSL certificate for {DOMAIN}"]


def test_remove_aborts_when_domain_config_vanished(h):
    h.external_manager.domain_exists.return_value = True
    h.external_manager.get_domain.return_value = None

    with pytest.raises(typer.Exit) as exc:
        external_helpers._remove_external_certificate(h.ctx, DOMAIN, yes=True)

    assert exc.value.exit_code == 1
    h.output.display_error.assert_called_once_with(
        f"Failed to remove certificate: Domain config not found for {DOMAIN}"
    )
    h.external_manager.to_ssl_certificate.assert_not_called()


def test_remove_aborts_when_certificate_object_cannot_be_rebuilt(h):
    h.external_manager.domain_exists.return_value = True
    h.external_manager.to_ssl_certificate.return_value = None

    with pytest.raises(typer.Exit) as exc:
        external_helpers._remove_external_certificate(h.ctx, DOMAIN, yes=True)

    assert exc.value.exit_code == 1
    h.output.display_error.assert_called_once_with(
        f"Failed to remove certificate: Could not create certificate object for {DOMAIN}"
    )
    h.SSLCertificateManager.assert_not_called()


def test_remove_failure_leaves_the_toml_entry_in_place(h):
    h.external_manager.domain_exists.return_value = True
    h.cert_manager.remove_certificate_by_domain.side_effect = RuntimeError("rm boom")

    with pytest.raises(typer.Exit) as exc:
        external_helpers._remove_external_certificate(h.ctx, DOMAIN, yes=True)

    assert exc.value.exit_code == 1
    h.output.display_error.assert_called_once_with("Failed to remove certificate: rm boom")
    h.external_manager.remove_domain.assert_not_called()


# --------------------------------------------------------------------------------------
# _renew_external_certificate
# --------------------------------------------------------------------------------------


def test_renew_rejects_unknown_domain_with_a_list_hint(h):
    h.external_manager.domain_exists.return_value = False

    with pytest.raises(typer.Exit) as exc:
        external_helpers._renew_external_certificate(h.ctx, DOMAIN, dry_run=False)

    assert exc.value.exit_code == 1
    h.output.display_error.assert_called_once_with(f"No external certificate found for domain '{DOMAIN}'")
    assert h.prints() == ["To list external certificates: fm ssl list --standalone"]


def test_renew_builds_storage_config_and_link_manager_but_no_standalone_nginx(h):
    """Copy #4's distinguishing detail: renew never touches the standalone nginx config."""
    h.external_manager.domain_exists.return_value = True

    external_helpers._renew_external_certificate(h.ctx, DOMAIN, dry_run=False)

    assert h.storage_kwargs == _expected_storage_kwargs(h.dirs)
    h.CertificateLinkManager.assert_called_once_with(h.storage_config)
    h.StandaloneNginxConfigManager.assert_not_called()
    h.standalone_nginx.create_https_config.assert_not_called()
    h.nginx_controller.reload.assert_not_called()


def test_renew_seeds_cert_manager_with_the_reconstructed_certificate(h):
    h.external_manager.domain_exists.return_value = True
    cert = _cert()
    h.external_manager.to_ssl_certificate.return_value = cert

    external_helpers._renew_external_certificate(h.ctx, DOMAIN, dry_run=False)

    kwargs = h.cert_manager_kwargs
    assert kwargs["certificates"] == [cert]
    assert kwargs["link_manager"] is h.link_manager
    assert kwargs["nginx_controller"] is h.nginx_controller
    assert kwargs["storage_config"] is h.storage_config
    assert kwargs["output_handler"] is h.output
    assert kwargs["config_save_callback"] is None


def test_renew_service_factory_delegates_to_create_certificate_service(h):
    h.external_manager.domain_exists.return_value = True

    external_helpers._renew_external_certificate(h.ctx, DOMAIN, dry_run=False)

    factory = h.cert_manager_kwargs["service_factory"]
    cert, cfg, handler = object(), object(), object()
    assert factory(cert, cfg, handler) is h.create_certificate_service.return_value
    h.create_certificate_service.assert_called_once_with(cert, cfg, handler)


@pytest.mark.parametrize("dry_run", [True, False])
@pytest.mark.parametrize("force", [True, False])
def test_renew_forwards_dry_run_and_force_as_keywords(h, dry_run, force):
    h.external_manager.domain_exists.return_value = True

    external_helpers._renew_external_certificate(h.ctx, DOMAIN, dry_run=dry_run, force=force)

    h.cert_manager.renew_certificate.assert_called_once_with(domain=DOMAIN, dry_run=dry_run, force=force)
    assert h.spinner_texts() == [f"Renewing certificate for {DOMAIN}"]
    assert f"Certificate renewal for {DOMAIN} completed" in h.prints()


def test_renew_defaults_force_to_false(h):
    h.external_manager.domain_exists.return_value = True

    external_helpers._renew_external_certificate(h.ctx, DOMAIN, dry_run=False)

    assert h.cert_manager.renew_certificate.call_args.kwargs["force"] is False


def test_renew_aborts_when_certificate_object_cannot_be_rebuilt(h):
    h.external_manager.domain_exists.return_value = True
    h.external_manager.to_ssl_certificate.return_value = None

    with pytest.raises(typer.Exit) as exc:
        external_helpers._renew_external_certificate(h.ctx, DOMAIN, dry_run=False)

    assert exc.value.exit_code == 1
    h.output.display_error.assert_called_once_with(
        f"Failed to renew certificate: Could not create certificate object for {DOMAIN}"
    )
    h.SSLCertificateManager.assert_not_called()


def test_renew_failure_message_differs_from_remove_and_add(h):
    h.external_manager.domain_exists.return_value = True
    h.cert_manager.renew_certificate.side_effect = RuntimeError("renew boom")

    with pytest.raises(typer.Exit) as exc:
        external_helpers._renew_external_certificate(h.ctx, DOMAIN, dry_run=False)

    assert exc.value.exit_code == 1
    h.output.display_error.assert_called_once_with("Failed to renew certificate: renew boom")
    assert f"Certificate renewal for {DOMAIN} completed" not in h.prints()


def test_renew_treats_a_not_due_certificate_as_a_warning_not_a_failure(h):
    """`renew_certificate` raises this *before* any acme.sh call for a healthy certificate.

    The bench path (renew.py) warns and exits 0; the standalone path used to fall into its
    blanket handler and turn a healthy certificate into display_error + exit 1.
    """
    h.external_manager.domain_exists.return_value = True
    not_due = SSLCertificateNotDueForRenewalError(DOMAIN, datetime.now(UTC) + timedelta(days=60))
    h.cert_manager.renew_certificate.side_effect = not_due

    assert external_helpers._renew_external_certificate(h.ctx, DOMAIN, dry_run=False) is None

    h.output.warning.assert_called_once_with(not_due.message)
    h.output.display_error.assert_not_called()
    assert f"Certificate renewal for {DOMAIN} completed" not in h.prints()


def test_renew_all_does_not_report_healthy_domains_as_failures(h):
    """Consequence of the above for `--standalone --all`: the degraded
    'Failed to renew <domain>: 1' (the caught object was the typer.Exit) is gone."""
    h.external_manager.domain_exists.return_value = True
    h.external_manager.list_domains.return_value = [SimpleNamespace(domain=DOMAIN)]
    h.cert_manager.renew_certificate.side_effect = SSLCertificateNotDueForRenewalError(
        DOMAIN, datetime.now(UTC) + timedelta(days=60)
    )

    external_helpers._renew_all_external_certificates(h.ctx, dry_run=False)

    warnings = [c.args[0] for c in h.output.warning.call_args_list]
    assert not [w for w in warnings if w.startswith("Failed to renew")]


# --------------------------------------------------------------------------------------
# _renew_all_external_certificates
# --------------------------------------------------------------------------------------


def test_renew_all_with_no_domains_returns_without_renewing(h):
    h.external_manager.list_domains.return_value = []

    with patch(f"{MODULE}._renew_external_certificate") as one:
        external_helpers._renew_all_external_certificates(h.ctx, dry_run=False)

    one.assert_not_called()
    assert h.prints() == ["No external SSL certificates to renew"]
    h.output.change_head.assert_not_called()


def test_renew_all_forwards_positional_args_for_every_domain(h):
    h.external_manager.list_domains.return_value = [
        SimpleNamespace(domain="a.example.com"),
        SimpleNamespace(domain="b.example.com"),
    ]

    with patch(f"{MODULE}._renew_external_certificate") as one:
        external_helpers._renew_all_external_certificates(h.ctx, dry_run=True, force=True)

    assert [c.args for c in one.call_args_list] == [
        (h.ctx, "a.example.com", True, True),
        (h.ctx, "b.example.com", True, True),
    ]
    assert "Renewing 2 external certificate(s)" in h.heads()


def test_renew_all_warns_and_continues_when_one_domain_fails(h):
    """Best effort across the fleet, then a nonzero exit.

    The old assertions stopped at the warning and let the function return normally, which is the
    defect: a cron-driven `fm ssl renew --standalone --all` reported success while certificates
    expired. Every domain is still attempted; the failure is reported at the end.
    """
    h.external_manager.list_domains.return_value = [
        SimpleNamespace(domain="a.example.com"),
        SimpleNamespace(domain="b.example.com"),
    ]

    with patch(f"{MODULE}._renew_external_certificate") as one:
        one.side_effect = [RuntimeError("first failed"), None]
        with pytest.raises(typer.Exit) as exc:
            external_helpers._renew_all_external_certificates(h.ctx, dry_run=False)

    assert exc.value.exit_code == 1
    h.output.warning.assert_called_once_with("Failed to renew a.example.com: first failed")
    assert one.call_count == 2
    h.output.display_error.assert_called_once_with("Failed to renew 1 of 2 certificate(s): a.example.com")


def test_renew_all_propagates_a_typer_exit_from_a_single_domain(h):
    """`typer.Exit` subclasses RuntimeError, so a blanket `except Exception` used to catch it,
    report the bare exit code as the reason and then return 0. It is now caught separately and
    counted as a failure."""
    h.external_manager.list_domains.return_value = [SimpleNamespace(domain="a.example.com")]

    with patch(f"{MODULE}._renew_external_certificate") as one:
        one.side_effect = typer.Exit(1)
        with pytest.raises(typer.Exit) as exc:
            external_helpers._renew_all_external_certificates(h.ctx, dry_run=False)

    assert exc.value.exit_code == 1
    reason = h.output.warning.call_args.args[0].split(":", 1)[1].strip()
    assert reason
    assert reason != "1"
    h.output.display_error.assert_called_once_with("Failed to renew 1 of 1 certificate(s): a.example.com")


def test_renew_all_real_error_message_survives_the_new_typer_exit_arm(h):
    """The separate `except typer.Exit` must not shadow a genuine exception's message."""
    h.external_manager.list_domains.return_value = [SimpleNamespace(domain="a.example.com")]

    with patch(f"{MODULE}._renew_external_certificate") as one:
        one.side_effect = OSError("acme.sh not found")
        with pytest.raises(typer.Exit) as exc:
            external_helpers._renew_all_external_certificates(h.ctx, dry_run=False)

    assert exc.value.exit_code == 1
    h.output.warning.assert_called_once_with("Failed to renew a.example.com: acme.sh not found")


def test_renew_all_exits_zero_when_every_domain_succeeds(h):
    h.external_manager.list_domains.return_value = [
        SimpleNamespace(domain="a.example.com"),
        SimpleNamespace(domain="b.example.com"),
    ]

    with patch(f"{MODULE}._renew_external_certificate"):
        assert external_helpers._renew_all_external_certificates(h.ctx, dry_run=False) is None

    h.output.display_error.assert_not_called()


def test_renew_all_treats_a_domain_that_exited_zero_as_a_success(h):
    """A `typer.Exit(0)` is a clean early return, not a failed certificate."""
    h.external_manager.list_domains.return_value = [SimpleNamespace(domain="a.example.com")]

    with patch(f"{MODULE}._renew_external_certificate") as one:
        one.side_effect = typer.Exit(0)
        assert external_helpers._renew_all_external_certificates(h.ctx, dry_run=False) is None

    h.output.display_error.assert_not_called()


# --------------------------------------------------------------------------------------
# _get_non_bench_domains_from_nginx
# --------------------------------------------------------------------------------------


def _nginx_conf(*domains: str) -> str:
    blocks = []
    for d in domains:
        blocks.append(f"# {d}/\nupstream {d} {{\n\tserver 172.18.0.5:80;\n}}")
    return "\n".join(blocks) + "\n"


class _Completed:
    def __init__(self, returncode=0, stdout=""):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = ""


@pytest.fixture
def nginx_probe(h):
    """Patched docker-exec seam plus bench discovery for the nginx scan."""
    with ExitStack() as stack:
        run = stack.enter_context(patch(f"{MODULE}.subprocess.run"))
        bench_service_cls = stack.enter_context(patch(f"{MODULE}.BenchService"))
        bench_cls = stack.enter_context(patch(f"{MODULE}.Bench"))
        h.services.compose_file_manager.get_container_names.return_value = {
            "global-nginx-proxy": "fm-global-nginx-proxy"
        }
        bench_service_cls.return_value.get_bench_names.return_value = []
        yield SimpleNamespace(run=run, bench_service_cls=bench_service_cls, bench_cls=bench_cls)


@pytest.mark.timeout(15)
def test_nginx_scan_issues_a_docker_exec_cat_of_default_conf(h, nginx_probe):
    nginx_probe.run.return_value = _Completed(stdout=_nginx_conf("x.example.com"))

    result = external_helpers._get_non_bench_domains_from_nginx(h.services)

    assert result == ["x.example.com"]
    call = nginx_probe.run.call_args
    assert call.args[0] == [
        "docker",
        "exec",
        "fm-global-nginx-proxy",
        "cat",
        "/etc/nginx/conf.d/default.conf",
    ]
    assert call.kwargs == {"capture_output": True, "text": True, "timeout": 10}


@pytest.mark.timeout(15)
def test_nginx_scan_returns_empty_without_running_docker_when_container_is_unknown(h, nginx_probe):
    h.services.compose_file_manager.get_container_names.return_value = {}

    assert external_helpers._get_non_bench_domains_from_nginx(h.services) == []
    nginx_probe.run.assert_not_called()


@pytest.mark.timeout(15)
def test_nginx_scan_returns_empty_on_nonzero_exit_even_with_parsable_stdout(h, nginx_probe):
    nginx_probe.run.return_value = _Completed(returncode=1, stdout=_nginx_conf("x.example.com"))

    assert external_helpers._get_non_bench_domains_from_nginx(h.services) == []


@pytest.mark.timeout(15)
def test_nginx_scan_only_matches_trailing_slash_comment_lines(h, nginx_probe):
    nginx_probe.run.return_value = _Completed(
        stdout="\n".join(
            [
                "# b.example.com/",
                "upstream b.example.com {",
                "# not-a-domain",  # no trailing slash
                "#c.example.com/",  # no space after '#'
                "  # d.example.com/",  # not at line start
                "# a.example.com/path/",  # non-greedy group keeps the inner slash
                "server_name e.example.com/;",
            ]
        )
    )

    assert external_helpers._get_non_bench_domains_from_nginx(h.services) == [
        "a.example.com/path",
        "b.example.com",
    ]


@pytest.mark.timeout(15)
def test_nginx_scan_deduplicates_and_sorts(h, nginx_probe):
    nginx_probe.run.return_value = _Completed(stdout="# z.example.com/\n# a.example.com/\n# z.example.com/\n")

    assert external_helpers._get_non_bench_domains_from_nginx(h.services) == [
        "a.example.com",
        "z.example.com",
    ]


@pytest.mark.timeout(15)
def test_nginx_scan_subtracts_every_domain_of_every_bench(h, nginx_probe):
    nginx_probe.run.return_value = _Completed(
        stdout=_nginx_conf("external.example.com", "bench.localhost", "alias.localhost")
    )
    nginx_probe.bench_service_cls.return_value.get_bench_names.return_value = ["bench.localhost"]
    bench = nginx_probe.bench_cls.get_object.return_value
    # alias.localhost is an alias OF the site bench.localhost, so both are bench hostnames.
    bench.bench_config.domains = ["bench.localhost", "alias.localhost"]

    assert external_helpers._get_non_bench_domains_from_nginx(h.services) == ["external.example.com"]

    nginx_probe.bench_cls.get_object.assert_called_once()
    args, kwargs = nginx_probe.bench_cls.get_object.call_args
    assert args == ("bench.localhost", h.services)
    assert isinstance(kwargs["output_handler"], SilentOutputHandler)


@pytest.mark.timeout(15)
def test_nginx_scan_skips_benches_that_fail_to_load_and_keeps_their_domains(h, nginx_probe):
    nginx_probe.run.return_value = _Completed(stdout=_nginx_conf("broken.localhost", "ok.localhost"))
    nginx_probe.bench_service_cls.return_value.get_bench_names.return_value = [
        "broken.localhost",
        "ok.localhost",
    ]

    def get_object(name, _services, output_handler):
        if name == "broken.localhost":
            raise RuntimeError("bench is unreadable")
        return SimpleNamespace(
            bench_config=SimpleNamespace(domains=["ok.localhost"]),
        )

    nginx_probe.bench_cls.get_object.side_effect = get_object

    # The unreadable bench's own domain survives the filter: it was never subtracted.
    assert external_helpers._get_non_bench_domains_from_nginx(h.services) == ["broken.localhost"]


@pytest.mark.timeout(15)
def test_nginx_scan_swallows_docker_failures(h, nginx_probe):
    nginx_probe.run.side_effect = OSError("docker missing")

    assert external_helpers._get_non_bench_domains_from_nginx(h.services) == []


@pytest.mark.timeout(15)
def test_nginx_scan_swallows_bench_discovery_failures(h, nginx_probe):
    nginx_probe.run.return_value = _Completed(stdout=_nginx_conf("x.example.com"))
    nginx_probe.bench_service_cls.side_effect = RuntimeError("no benches dir")

    assert external_helpers._get_non_bench_domains_from_nginx(h.services) == []


# --------------------------------------------------------------------------------------
# _list_external_certificates
# --------------------------------------------------------------------------------------


@pytest.fixture
def listing(h):
    """Patched table + expiry seams for the listing view."""
    with ExitStack() as stack:
        table_cls = stack.enter_context(patch(f"{MODULE}.Table"))
        scan = stack.enter_context(patch(f"{MODULE}._get_non_bench_domains_from_nginx", return_value=[]))
        expiry = stack.enter_context(patch(f"{MODULE}.get_certificate_expiry_date"))
        h.link_manager.get_certificate_paths.return_value = (Path("/k.pem"), Path("/f.pem"))
        yield SimpleNamespace(
            table_cls=table_cls,
            table=table_cls.return_value,
            scan=scan,
            expiry=expiry,
            rows=lambda: [c.args for c in table_cls.return_value.add_row.call_args_list],
        )


def _ssl_domain(domain: str, ssl_type: str = "letsencrypt") -> SimpleNamespace:
    return SimpleNamespace(domain=domain, ssl_type=ssl_type)


def test_list_with_nothing_configured_prints_the_getting_started_hint(h, listing):
    h.external_manager.list_domains.return_value = []

    external_helpers._list_external_certificates(h.ctx)

    assert h.prints() == [
        "No external domains or SSL certificates configured",
        "",
        "To add an external certificate:",
        "  fm ssl add --standalone <domain>",
    ]
    listing.table_cls.assert_not_called()
    h.output.print_data.assert_not_called()
    h.SSLStorageConfig.assert_not_called()


def test_list_builds_storage_and_link_manager_but_never_the_nginx_collaborators(h, listing):
    """Copy #4b: the listing view stops after the link manager."""
    h.external_manager.list_domains.return_value = [_ssl_domain(DOMAIN)]
    listing.expiry.return_value = datetime.now(UTC) + timedelta(days=60)

    external_helpers._list_external_certificates(h.ctx)

    assert h.storage_kwargs == _expected_storage_kwargs(h.dirs)
    h.CertificateLinkManager.assert_called_once_with(h.storage_config)
    h.StandaloneNginxConfigManager.assert_not_called()
    h.SSLCertificateManager.assert_not_called()
    h.create_certificate_service.assert_not_called()
    h.nginx_controller.reload.assert_not_called()


def test_list_table_columns_are_fixed(h, listing):
    h.external_manager.list_domains.return_value = [_ssl_domain(DOMAIN)]
    listing.expiry.return_value = datetime.now(UTC) + timedelta(days=60)

    external_helpers._list_external_certificates(h.ctx)

    assert [c.args[0] for c in listing.table.add_column.call_args_list] == [
        "Domain",
        "Type",
        "Status",
        "Expiry",
        "Days Left",
        "Renewal",
    ]
    h.output.print_data.assert_called_once_with(listing.table)


def test_list_marks_a_healthy_certificate_ok(h, listing):
    h.external_manager.list_domains.return_value = [_ssl_domain(DOMAIN)]
    expiry_date = datetime.now(UTC) + timedelta(days=SSL_RENEW_BEFORE_DAYS + 1, seconds=60)
    listing.expiry.return_value = expiry_date

    external_helpers._list_external_certificates(h.ctx)

    assert listing.rows() == [
        (
            DOMAIN,
            "letsencrypt",
            "✅ Issued",
            expiry_date.strftime("%Y-%m-%d %H:%M"),
            str(SSL_RENEW_BEFORE_DAYS + 1),
            "✓ OK",
        )
    ]
    listing.expiry.assert_called_once_with(Path("/f.pem"))


def test_list_marks_renewal_due_at_exactly_the_threshold(h, listing):
    """`days_left <= SSL_RENEW_BEFORE_DAYS` is inclusive."""
    h.external_manager.list_domains.return_value = [_ssl_domain(DOMAIN)]
    listing.expiry.return_value = datetime.now(UTC) + timedelta(days=SSL_RENEW_BEFORE_DAYS, seconds=60)

    external_helpers._list_external_certificates(h.ctx)

    row = listing.rows()[0]
    assert row[2] == "✅ Issued"
    assert row[4] == str(SSL_RENEW_BEFORE_DAYS)
    assert row[5] == "⚠️ DUE"


def test_list_reports_negative_days_for_an_expired_certificate(h, listing):
    """An already-expired cert still reports "✅ Issued"; only Days Left / Renewal reveal it.

    `timedelta.days` floors toward -inf, so "expired 2 days ago" prints -3.
    """
    h.external_manager.list_domains.return_value = [_ssl_domain(DOMAIN)]
    listing.expiry.return_value = datetime.now(UTC) - timedelta(days=2)

    external_helpers._list_external_certificates(h.ctx)

    row = listing.rows()[0]
    assert row[2] == "✅ Issued"
    assert row[4] == "-3"
    assert row[5] == "⚠️ DUE"


def test_list_marks_status_unknown_when_expiry_cannot_be_parsed(h, listing):
    h.external_manager.list_domains.return_value = [_ssl_domain(DOMAIN)]
    listing.expiry.return_value = None

    external_helpers._list_external_certificates(h.ctx)

    assert listing.rows() == [(DOMAIN, "letsencrypt", "⚠️ Unknown", "N/A", "N/A", "N/A")]
    h.output.debug.assert_not_called()


def test_list_marks_status_missing_when_the_certificate_lookup_raises(h, listing):
    h.external_manager.list_domains.return_value = [_ssl_domain(DOMAIN)]
    h.link_manager.get_certificate_paths.side_effect = FileNotFoundError("gone")

    external_helpers._list_external_certificates(h.ctx)

    assert listing.rows() == [(DOMAIN, "letsencrypt", "❌ Missing", "N/A", "N/A", "N/A")]
    h.output.debug.assert_called_once_with(f"Error getting certificate status for {DOMAIN}: gone")


def test_list_preserves_the_configured_ssl_type_in_the_type_column(h, listing):
    h.external_manager.list_domains.return_value = [_ssl_domain(DOMAIN, ssl_type="dev")]
    listing.expiry.return_value = None

    external_helpers._list_external_certificates(h.ctx)

    assert listing.rows()[0][1] == "dev"


def test_list_appends_detected_domains_without_ssl_and_a_tip(h, listing):
    h.external_manager.list_domains.return_value = [_ssl_domain(DOMAIN)]
    listing.expiry.return_value = None
    listing.scan.return_value = ["plain.example.com"]

    external_helpers._list_external_certificates(h.ctx)

    assert listing.rows()[-1] == ("plain.example.com", "none", "🔓 No SSL", "N/A", "N/A", "N/A")
    assert h.prints() == [
        "\n[fm.warn]💡 Tip: Add SSL certificates for non-SSL domains:[/fm.warn]",
        "[fm.muted]  fm ssl add --standalone <domain>[/fm.muted]",
    ]


def test_list_hides_detected_domains_that_already_have_a_certificate(h, listing):
    h.external_manager.list_domains.return_value = [_ssl_domain(DOMAIN)]
    listing.expiry.return_value = None
    listing.scan.return_value = [DOMAIN, "plain.example.com"]

    external_helpers._list_external_certificates(h.ctx)

    assert [row[0] for row in listing.rows()] == [DOMAIN, "plain.example.com"]


def test_list_shows_detected_domains_even_with_no_certificates_configured(h, listing):
    h.external_manager.list_domains.return_value = []
    listing.scan.return_value = ["plain.example.com"]

    external_helpers._list_external_certificates(h.ctx)

    assert listing.rows() == [("plain.example.com", "none", "🔓 No SSL", "N/A", "N/A", "N/A")]
    assert "No external domains or SSL certificates configured" not in h.prints()


def test_list_omits_the_tip_when_every_detected_domain_has_ssl(h, listing):
    h.external_manager.list_domains.return_value = [_ssl_domain(DOMAIN)]
    listing.expiry.return_value = None
    listing.scan.return_value = [DOMAIN]

    external_helpers._list_external_certificates(h.ctx)

    assert h.prints() == []
    h.output.print_data.assert_called_once_with(listing.table)
