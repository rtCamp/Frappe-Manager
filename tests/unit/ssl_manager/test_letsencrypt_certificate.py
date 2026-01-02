"""
Unit tests for LetsencryptSSLCertificate model.

Tests the Let's Encrypt certificate Pydantic model including validation,
credentials handling, and challenge-specific logic.
"""

import pytest
from pydantic import ValidationError
from unittest.mock import patch

from frappe_manager.ssl_manager import SUPPORTED_SSL_TYPES, LETSENCRYPT_PREFERRED_CHALLENGE
from frappe_manager.ssl_manager.letsencrypt_certificate import LetsencryptSSLCertificate
from frappe_manager.ssl_manager.certificate_exceptions import SSLDNSChallengeCredentailsNotFound
from tests.unit.ssl_manager.conftest import VALID_EMAILS, INVALID_EMAILS


class TestHTTP01Challenge:
    """Tests for HTTP-01 challenge configuration."""

    def test_create_valid_http01_certificate_no_credentials_required(self):
        """Test that HTTP-01 certificate doesn't require DNS credentials."""
        cert = LetsencryptSSLCertificate(
            domain="example.com",
            ssl_type=SUPPORTED_SSL_TYPES.le,
            preferred_challenge=LETSENCRYPT_PREFERRED_CHALLENGE.http01,
            email="admin@example.com"
        )
        
        assert cert.domain == "example.com"
        assert cert.preferred_challenge == LETSENCRYPT_PREFERRED_CHALLENGE.http01
        assert cert.email == "admin@example.com"
        assert cert.api_token is None
        assert cert.api_key is None

    def test_http01_with_optional_credentials_still_valid(self):
        """Test that HTTP-01 accepts optional credentials even though not needed."""
        cert = LetsencryptSSLCertificate(
            domain="example.com",
            ssl_type=SUPPORTED_SSL_TYPES.le,
            preferred_challenge=LETSENCRYPT_PREFERRED_CHALLENGE.http01,
            email="admin@example.com",
            api_token="optional_token"
        )
        
        assert cert.api_token == "optional_token"

    def test_http01_inherits_from_ssl_certificate(self):
        """Test that LetsencryptSSLCertificate inherits from SSLCertificate."""
        from frappe_manager.ssl_manager.certificate import SSLCertificate
        
        cert = LetsencryptSSLCertificate(
            domain="example.com",
            ssl_type=SUPPORTED_SSL_TYPES.le,
            preferred_challenge=LETSENCRYPT_PREFERRED_CHALLENGE.http01,
            email="admin@example.com"
        )
        
        assert isinstance(cert, SSLCertificate)

    def test_http01_toml_exclude_correct(self):
        """Test that toml_exclude contains expected fields."""
        cert = LetsencryptSSLCertificate(
            domain="example.com",
            ssl_type=SUPPORTED_SSL_TYPES.le,
            preferred_challenge=LETSENCRYPT_PREFERRED_CHALLENGE.http01,
            email="admin@example.com"
        )
        
        assert 'domain' in cert.toml_exclude
        assert 'toml_exclude' in cert.toml_exclude


class TestDNS01ChallengeValidation:
    """Tests for DNS-01 challenge validation."""

    def test_dns01_with_api_token_valid(self):
        """Test that DNS-01 with API token is valid."""
        cert = LetsencryptSSLCertificate(
            domain="example.com",
            ssl_type=SUPPORTED_SSL_TYPES.le,
            preferred_challenge=LETSENCRYPT_PREFERRED_CHALLENGE.dns01,
            email="admin@example.com",
            api_token="test_token_123"
        )
        
        assert cert.api_token == "test_token_123"
        assert cert.api_key is None

    def test_dns01_with_api_key_valid(self):
        """Test that DNS-01 with API key is valid."""
        cert = LetsencryptSSLCertificate(
            domain="example.com",
            ssl_type=SUPPORTED_SSL_TYPES.le,
            preferred_challenge=LETSENCRYPT_PREFERRED_CHALLENGE.dns01,
            email="admin@example.com",
            api_key="test_key_456"
        )
        
        assert cert.api_key == "test_key_456"
        assert cert.api_token is None

    def test_dns01_with_both_credentials_valid(self):
        """Test that DNS-01 with both credentials is valid."""
        cert = LetsencryptSSLCertificate(
            domain="example.com",
            ssl_type=SUPPORTED_SSL_TYPES.le,
            preferred_challenge=LETSENCRYPT_PREFERRED_CHALLENGE.dns01,
            email="admin@example.com",
            api_token="test_token_123",
            api_key="test_key_456"
        )
        
        assert cert.api_token == "test_token_123"
        assert cert.api_key == "test_key_456"

    def test_dns01_without_credentials_raises_exception(self):
        """Test that DNS-01 without credentials raises exception."""
        with pytest.raises(SSLDNSChallengeCredentailsNotFound):
            LetsencryptSSLCertificate(
                domain="example.com",
                ssl_type=SUPPORTED_SSL_TYPES.le,
                preferred_challenge=LETSENCRYPT_PREFERRED_CHALLENGE.dns01,
                email="admin@example.com"
            )

    def test_dns01_with_none_api_token_and_key_raises_exception(self):
        """Test that DNS-01 with explicit None values raises exception."""
        with pytest.raises(SSLDNSChallengeCredentailsNotFound):
            LetsencryptSSLCertificate(
                domain="example.com",
                ssl_type=SUPPORTED_SSL_TYPES.le,
                preferred_challenge=LETSENCRYPT_PREFERRED_CHALLENGE.dns01,
                email="admin@example.com",
                api_token=None,
                api_key=None
            )

    def test_dns01_with_empty_string_credentials_raises_exception(self):
        """Test that DNS-01 with empty string credentials is invalid."""
        # Empty strings should be treated as None
        with pytest.raises(SSLDNSChallengeCredentailsNotFound):
            LetsencryptSSLCertificate(
                domain="example.com",
                ssl_type=SUPPORTED_SSL_TYPES.le,
                preferred_challenge=LETSENCRYPT_PREFERRED_CHALLENGE.dns01,
                email="admin@example.com",
                api_token="",
                api_key=""
            )

    def test_model_validator_called_after_initialization(self):
        """Test that model validator is called during initialization."""
        # This should trigger validator and raise exception
        with pytest.raises(SSLDNSChallengeCredentailsNotFound):
            LetsencryptSSLCertificate(
                domain="example.com",
                ssl_type=SUPPORTED_SSL_TYPES.le,
                preferred_challenge=LETSENCRYPT_PREFERRED_CHALLENGE.dns01,
                email="admin@example.com"
            )

    def test_challenge_type_enum_validation(self):
        """Test that challenge type must be valid enum value."""
        with pytest.raises(ValidationError):
            LetsencryptSSLCertificate(
                domain="example.com",
                ssl_type=SUPPORTED_SSL_TYPES.le,
                preferred_challenge="invalid_challenge",
                email="admin@example.com"
            )


class TestEmailValidation:
    """Tests for email field validation."""

    @pytest.mark.parametrize("email", VALID_EMAILS)
    def test_valid_email_formats(self, email):
        """Test that various valid email formats are accepted."""
        cert = LetsencryptSSLCertificate(
            domain="example.com",
            ssl_type=SUPPORTED_SSL_TYPES.le,
            preferred_challenge=LETSENCRYPT_PREFERRED_CHALLENGE.http01,
            email=email
        )
        
        assert cert.email == email

    @pytest.mark.parametrize("email", INVALID_EMAILS)
    def test_invalid_email_raises_validation_error(self, email):
        """Test that invalid email formats raise validation error."""
        with pytest.raises(ValidationError) as exc_info:
            LetsencryptSSLCertificate(
                domain="example.com",
                ssl_type=SUPPORTED_SSL_TYPES.le,
                preferred_challenge=LETSENCRYPT_PREFERRED_CHALLENGE.http01,
                email=email
            )
        
        # Error should mention email field
        assert 'email' in str(exc_info.value).lower()

    def test_email_field_required(self):
        """Test that email field is required."""
        with pytest.raises(ValidationError) as exc_info:
            LetsencryptSSLCertificate(
                domain="example.com",
                ssl_type=SUPPORTED_SSL_TYPES.le,
                preferred_challenge=LETSENCRYPT_PREFERRED_CHALLENGE.http01
            )
        
        assert 'email' in str(exc_info.value).lower()


class TestGetCloudflareCredentials:
    """Tests for get_cloudflare_dns_credentials() method."""

    def test_get_credentials_with_api_key_only(self, mocker):
        """Test credentials output with API key only."""
        mock_richprint = mocker.patch('frappe_manager.ssl_manager.letsencrypt_certificate.richprint')
        
        cert = LetsencryptSSLCertificate(
            domain="example.com",
            ssl_type=SUPPORTED_SSL_TYPES.le,
            preferred_challenge=LETSENCRYPT_PREFERRED_CHALLENGE.dns01,
            email="admin@example.com",
            api_key="test_api_key"
        )
        
        creds = cert.get_cloudflare_dns_credentials()
        
        assert "dns_cloudflare_email = admin@example.com" in creds
        assert "dns_cloudflare_api_key = test_api_key" in creds
        assert "dns_cloudflare_api_token" not in creds
        mock_richprint.print.assert_called_with('Using Cloudflare GLOBAL API KEY')

    def test_get_credentials_with_api_token_only(self, mocker):
        """Test credentials output with API token only."""
        mock_richprint = mocker.patch('frappe_manager.ssl_manager.letsencrypt_certificate.richprint')
        
        cert = LetsencryptSSLCertificate(
            domain="example.com",
            ssl_type=SUPPORTED_SSL_TYPES.le,
            preferred_challenge=LETSENCRYPT_PREFERRED_CHALLENGE.dns01,
            email="admin@example.com",
            api_token="test_api_token"
        )
        
        creds = cert.get_cloudflare_dns_credentials()
        
        assert "dns_cloudflare_api_token = test_api_token" in creds
        assert "dns_cloudflare_api_key" not in creds
        mock_richprint.print.assert_called_with('Using Cloudflare API Token')

    def test_get_credentials_with_both_credentials(self, mocker):
        """Test credentials output with both API key and token."""
        mock_richprint = mocker.patch('frappe_manager.ssl_manager.letsencrypt_certificate.richprint')
        
        cert = LetsencryptSSLCertificate(
            domain="example.com",
            ssl_type=SUPPORTED_SSL_TYPES.le,
            preferred_challenge=LETSENCRYPT_PREFERRED_CHALLENGE.dns01,
            email="admin@example.com",
            api_token="test_token",
            api_key="test_key"
        )
        
        creds = cert.get_cloudflare_dns_credentials()
        
        # When both are present, both should be in output
        assert "dns_cloudflare_email" in creds
        assert "dns_cloudflare_api_key" in creds
        assert "dns_cloudflare_api_token" in creds

    def test_get_credentials_without_any_raises_exception(self):
        """Test that get_cloudflare_dns_credentials without credentials raises exception."""
        # Create cert with http01 (no credentials required during init)
        cert = LetsencryptSSLCertificate(
            domain="example.com",
            ssl_type=SUPPORTED_SSL_TYPES.le,
            preferred_challenge=LETSENCRYPT_PREFERRED_CHALLENGE.http01,
            email="admin@example.com"
        )
        
        # Manually set to have no credentials
        cert.api_token = None
        cert.api_key = None
        
        with pytest.raises(SSLDNSChallengeCredentailsNotFound):
            cert.get_cloudflare_dns_credentials()

    def test_credentials_output_format_correct(self):
        """Test that credentials are formatted correctly for certbot."""
        cert = LetsencryptSSLCertificate(
            domain="example.com",
            ssl_type=SUPPORTED_SSL_TYPES.le,
            preferred_challenge=LETSENCRYPT_PREFERRED_CHALLENGE.dns01,
            email="test@example.com",
            api_token="my_token"
        )
        
        creds = cert.get_cloudflare_dns_credentials()
        
        # Should be proper format with newlines
        lines = [line for line in creds.split('\n') if line.strip()]
        assert len(lines) >= 1
        assert all('=' in line for line in lines)


class TestLetsencryptCertificateFieldsAndInheritance:
    """Tests for fields, inheritance, and model behavior."""

    def test_all_required_fields_present(self):
        """Test that certificate has all required fields."""
        cert = LetsencryptSSLCertificate(
            domain="example.com",
            ssl_type=SUPPORTED_SSL_TYPES.le,
            preferred_challenge=LETSENCRYPT_PREFERRED_CHALLENGE.http01,
            email="admin@example.com"
        )
        
        assert hasattr(cert, 'domain')
        assert hasattr(cert, 'ssl_type')
        assert hasattr(cert, 'preferred_challenge')
        assert hasattr(cert, 'email')
        assert hasattr(cert, 'api_token')
        assert hasattr(cert, 'api_key')
        assert hasattr(cert, 'hsts')
        assert hasattr(cert, 'toml_exclude')

    def test_ssl_type_must_be_letsencrypt(self):
        """Test that ssl_type should be letsencrypt for LE certificates."""
        cert = LetsencryptSSLCertificate(
            domain="example.com",
            ssl_type=SUPPORTED_SSL_TYPES.le,
            preferred_challenge=LETSENCRYPT_PREFERRED_CHALLENGE.http01,
            email="admin@example.com"
        )
        
        assert cert.ssl_type == SUPPORTED_SSL_TYPES.le
        assert cert.ssl_type.value == "letsencrypt"

    def test_certificate_serialization(self):
        """Test that certificate can be serialized."""
        cert = LetsencryptSSLCertificate(
            domain="example.com",
            ssl_type=SUPPORTED_SSL_TYPES.le,
            preferred_challenge=LETSENCRYPT_PREFERRED_CHALLENGE.dns01,
            email="admin@example.com",
            api_token="test_token"
        )
        
        data = cert.model_dump()
        
        assert 'preferred_challenge' in data
        assert 'email' in data
        assert data['preferred_challenge'] == LETSENCRYPT_PREFERRED_CHALLENGE.dns01

    def test_wildcard_domain_with_dns01(self):
        """Test that wildcard domains work with DNS-01 challenge."""
        cert = LetsencryptSSLCertificate(
            domain="*.example.com",
            ssl_type=SUPPORTED_SSL_TYPES.le,
            preferred_challenge=LETSENCRYPT_PREFERRED_CHALLENGE.dns01,
            email="admin@example.com",
            api_token="test_token"
        )
        
        assert cert.domain == "*.example.com"
        assert cert.preferred_challenge == LETSENCRYPT_PREFERRED_CHALLENGE.dns01

    def test_hsts_inherited_from_parent(self):
        """Test that HSTS field is inherited from parent SSLCertificate."""
        cert = LetsencryptSSLCertificate(
            domain="example.com",
            ssl_type=SUPPORTED_SSL_TYPES.le,
            preferred_challenge=LETSENCRYPT_PREFERRED_CHALLENGE.http01,
            email="admin@example.com",
            hsts="max-age=31536000"
        )
        
        assert cert.hsts == "max-age=31536000"
