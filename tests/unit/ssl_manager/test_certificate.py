"""
Unit tests for SSLCertificate model.

Tests the basic SSL certificate Pydantic model including validation,
defaults, serialization, and edge cases.
"""

import pytest
from pydantic import ValidationError

from frappe_manager.ssl_manager import SUPPORTED_SSL_TYPES
from frappe_manager.ssl_manager.certificate import SSLCertificate
from tests.unit.ssl_manager.conftest import TEST_DOMAINS


class TestSSLCertificateValidation:
    """Tests for SSL certificate model validation."""

    def test_create_valid_certificate_with_letsencrypt_type(self):
        """Test creating a valid certificate with Let's Encrypt type."""
        cert = SSLCertificate(domain="example.com", ssl_type=SUPPORTED_SSL_TYPES.le)

        assert cert.domain == "example.com"
        assert cert.ssl_type == SUPPORTED_SSL_TYPES.le
        assert cert.hsts == "off"  # default value

    def test_create_valid_certificate_with_disable_type(self):
        """Test creating a valid certificate with disabled SSL."""
        cert = SSLCertificate(domain="test.com", ssl_type=SUPPORTED_SSL_TYPES.none)

        assert cert.domain == "test.com"
        assert cert.ssl_type == SUPPORTED_SSL_TYPES.none

    def test_default_hsts_value_is_off(self):
        """Test that hsts defaults to 'off'."""
        cert = SSLCertificate(domain="example.com", ssl_type=SUPPORTED_SSL_TYPES.none)

        assert cert.hsts == "off", "Default HSTS value should be 'off'"

    def test_toml_exclude_contains_expected_fields(self):
        """Test that toml_exclude set contains expected fields."""
        cert = SSLCertificate(domain="example.com", ssl_type=SUPPORTED_SSL_TYPES.none)

        assert "domain" in cert.toml_exclude
        assert "toml_exclude" in cert.toml_exclude

    def test_domain_field_required(self):
        """Test that domain field is required."""
        with pytest.raises(ValidationError) as exc_info:
            SSLCertificate(ssl_type=SUPPORTED_SSL_TYPES.none)

        assert "domain" in str(exc_info.value)

    def test_ssl_type_field_required(self):
        """Test that ssl_type field is required."""
        with pytest.raises(ValidationError) as exc_info:
            SSLCertificate(domain="example.com")

        assert "ssl_type" in str(exc_info.value)

    def test_invalid_ssl_type_raises_validation_error(self):
        """Test that invalid SSL type raises validation error."""
        with pytest.raises(ValidationError) as exc_info:
            SSLCertificate(domain="example.com", ssl_type="invalid_type")

        error_msg = str(exc_info.value)
        assert "ssl_type" in error_msg

    @pytest.mark.parametrize("domain", TEST_DOMAINS)
    def test_domain_accepts_various_formats(self, domain):
        """Test that domain field accepts various valid formats."""
        cert = SSLCertificate(domain=domain, ssl_type=SUPPORTED_SSL_TYPES.none)

        assert cert.domain == domain


class TestSSLCertificateSerialization:
    """Tests for SSL certificate serialization."""

    def test_model_dump_includes_all_non_excluded_fields(self):
        """Test that model_dump includes non-excluded fields."""
        cert = SSLCertificate(domain="example.com", ssl_type=SUPPORTED_SSL_TYPES.le, hsts="on")

        data = cert.model_dump()

        assert "ssl_type" in data
        assert "hsts" in data
        assert data["ssl_type"] == SUPPORTED_SSL_TYPES.le
        assert data["hsts"] == "on"

    def test_model_dump_excludes_toml_exclude_fields(self):
        """Test that model_dump can exclude toml_exclude fields."""
        cert = SSLCertificate(domain="example.com", ssl_type=SUPPORTED_SSL_TYPES.none)

        data = cert.model_dump(exclude=cert.toml_exclude)

        assert "domain" not in data
        assert "toml_exclude" not in data

    def test_model_dump_json_works(self):
        """Test that model can be serialized to JSON."""
        cert = SSLCertificate(domain="example.com", ssl_type=SUPPORTED_SSL_TYPES.le)

        json_str = cert.model_dump_json()

        assert isinstance(json_str, str)
        assert "example.com" in json_str
        assert "letsencrypt" in json_str

    def test_serialize_and_deserialize_roundtrip(self):
        """Test that certificate can be serialized and deserialized."""
        original = SSLCertificate(domain="test.example.com", ssl_type=SUPPORTED_SSL_TYPES.le, hsts="max-age=31536000")

        # Serialize to dict
        data = original.model_dump()

        # Deserialize from dict
        restored = SSLCertificate(**data)

        assert restored.domain == original.domain
        assert restored.ssl_type == original.ssl_type
        assert restored.hsts == original.hsts


class TestSSLCertificateEdgeCases:
    """Tests for edge cases and special scenarios."""

    def test_empty_string_domain_accepted(self):
        """Test that empty string domain is accepted (though not recommended)."""
        # Pydantic 2 allows empty strings by default
        cert = SSLCertificate(domain="", ssl_type=SUPPORTED_SSL_TYPES.none)
        assert cert.domain == ""

    def test_hsts_custom_value(self):
        """Test that custom HSTS value is accepted."""
        cert = SSLCertificate(
            domain="example.com", ssl_type=SUPPORTED_SSL_TYPES.le, hsts="max-age=31536000; includeSubDomains",
        )

        assert cert.hsts == "max-age=31536000; includeSubDomains"

    def test_toml_exclude_is_set_type(self):
        """Test that toml_exclude is a set."""
        cert = SSLCertificate(domain="example.com", ssl_type=SUPPORTED_SSL_TYPES.none)

        assert isinstance(cert.toml_exclude, set)

    def test_certificate_with_wildcard_domain(self):
        """Test certificate with wildcard domain."""
        cert = SSLCertificate(domain="*.example.com", ssl_type=SUPPORTED_SSL_TYPES.le)

        assert cert.domain == "*.example.com"

    def test_certificate_with_localhost(self):
        """Test certificate with localhost domain."""
        cert = SSLCertificate(domain="localhost", ssl_type=SUPPORTED_SSL_TYPES.none)

        assert cert.domain == "localhost"

    def test_ssl_type_enum_string_value(self):
        """Test that SSL type enum has correct string values."""
        cert = SSLCertificate(domain="example.com", ssl_type=SUPPORTED_SSL_TYPES.le)

        assert cert.ssl_type.value == "letsencrypt"

        cert_none = SSLCertificate(domain="example.com", ssl_type=SUPPORTED_SSL_TYPES.none)

        assert cert_none.ssl_type.value == "disable"
