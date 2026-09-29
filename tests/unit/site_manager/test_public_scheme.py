"""Contract of `frappe_manager/site_manager/modules/public_scheme.py`.

fm answers "is this domain HTTPS?" from three Python call sites (HSTS gating, the maintenance
cookie's Secure flag, printed URLs); this module is the one place that decision is made, so a
front holding the certificate instead of fm no longer understates TLS.
"""

from pathlib import Path

import pytest

from frappe_manager.site_manager.modules.public_scheme import host_has_trusted_front, public_scheme
from frappe_manager.site_manager.modules.realip import build_proxy_realip_conf, PROXY_CONF_FILENAME


class TestPublicScheme:
    @pytest.mark.parametrize(
        "domain_has_certificate,has_trusted_front",
        [(True, False), (False, True), (True, True)],
    )
    def test_https_when_either_input_is_true(self, domain_has_certificate, has_trusted_front):
        assert public_scheme(domain_has_certificate, has_trusted_front) == "https"

    def test_http_only_when_both_are_false(self):
        assert public_scheme(False, False) == "http"


class TestHostHasTrustedFront:
    def test_false_with_no_conf_d_content(self, tmp_path: Path):
        assert host_has_trusted_front(tmp_path) is False

    def test_true_once_a_range_is_trusted(self, tmp_path: Path):
        conf = build_proxy_realip_conf(["203.0.113.0/24"], "CF-Connecting-IP", recursive=True)
        (tmp_path / PROXY_CONF_FILENAME).write_text(conf)
        assert host_has_trusted_front(tmp_path) is True

    def test_false_for_a_foreign_file_with_the_same_name(self, tmp_path: Path):
        # Ownership matters, not presence: an operator's own file at this path must not be
        # read as an fm trust grant.
        (tmp_path / PROXY_CONF_FILENAME).write_text("set_real_ip_from 10.0.0.0/8;\n")
        assert host_has_trusted_front(tmp_path) is False
