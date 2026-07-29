"""Real-IP nginx conf rendering (bench hop + global-proxy hop)."""

import pytest

from frappe_manager.site_manager.modules.realip import (
    CLOUDFLARE_FALLBACK_RANGES,
    build_bench_realip_conf,
    build_proxy_realip_conf,
    is_fm_realip_conf,
    validate_cidrs,
)


class TestBenchConf:
    def test_trusts_subnet_and_reads_x_real_ip(self):
        conf = build_bench_realip_conf("10.1.0.0/16")
        assert "set_real_ip_from 10.1.0.0/16;" in conf
        assert "real_ip_header X-Real-IP;" in conf
        assert is_fm_realip_conf(conf)


class TestProxyConf:
    def test_renders_ranges_header_and_recursive(self):
        conf = build_proxy_realip_conf(["203.0.113.0/24", "2400:cb00::/32"], "CF-Connecting-IP", recursive=True)
        assert "set_real_ip_from 203.0.113.0/24;" in conf
        assert "set_real_ip_from 2400:cb00::/32;" in conf
        assert "real_ip_header CF-Connecting-IP;" in conf
        assert "real_ip_recursive on;" in conf
        assert is_fm_realip_conf(conf)

    def test_marker_guards_foreign_files(self):
        assert not is_fm_realip_conf("# hand-written nginx conf\n")


class TestValidateCidrs:
    def test_normalizes_bare_ip_and_cidr(self):
        assert validate_cidrs(["10.1.0.1", "203.0.113.0/24"]) == ["10.1.0.1/32", "203.0.113.0/24"]

    def test_rejects_garbage_naming_the_entry(self):
        with pytest.raises(ValueError, match="not-an-ip"):
            validate_cidrs(["not-an-ip"])

    def test_rejects_nginx_injection(self):
        with pytest.raises(ValueError):
            validate_cidrs(["10.0.0.0/8; real_ip_header X-Evil"])

    def test_vendored_cloudflare_ranges_are_valid(self):
        # The fallback list is used verbatim when the live fetch fails; every
        # entry must parse.
        assert len(validate_cidrs(list(CLOUDFLARE_FALLBACK_RANGES))) == len(CLOUDFLARE_FALLBACK_RANGES)
