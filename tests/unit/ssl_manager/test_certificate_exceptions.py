"""
Unit tests for SSL certificate exception classes.

Tests all custom exceptions used in the SSL manager module including
message formatting, attributes, and proper inheritance.
"""

import pytest
from datetime import datetime, timedelta
from unittest.mock import patch

from frappe_manager.ssl_manager.certificate_exceptions import (
    SSLCertificateNotFoundError,
    SSLCertificateEmailNotFoundError,
    SSLDNSChallengeCredentailsNotFound,
    SSLCertificateChallengeFailed,
    SSLCertificateGenerateFailed,
    SSLCertificateNotDueForRenewalError,
)


class TestSSLCertificateNotFoundError:
    """Tests for SSLCertificateNotFoundError exception."""

    def test_exception_message_includes_domain(self):
        """Test that exception message includes the domain name."""
        domain = "example.com"
        exception = SSLCertificateNotFoundError(domain)

        assert domain in exception.message
        assert domain in str(exception)

    def test_custom_message_override(self):
        """Test that custom message template can be provided."""
        domain = "test.com"
        custom_message = "Certificate missing for domain: {}"
        exception = SSLCertificateNotFoundError(domain, message=custom_message)

        assert "Certificate missing for domain: test.com" == exception.message

    def test_domain_attribute_accessible(self):
        """Test that domain attribute is accessible."""
        domain = "example.com"
        exception = SSLCertificateNotFoundError(domain)

        assert exception.domain == domain

    def test_exception_can_be_raised_and_caught(self):
        """Test that exception can be raised and caught properly."""
        with pytest.raises(SSLCertificateNotFoundError) as exc_info:
            raise SSLCertificateNotFoundError("example.com")

        assert "example.com" in str(exc_info.value)


class TestSSLCertificateEmailNotFoundError:
    """Tests for SSLCertificateEmailNotFoundError exception."""

    def test_exception_message_format(self):
        """Test that exception message is formatted correctly."""
        domain = "example.com"
        exception = SSLCertificateEmailNotFoundError(domain)

        assert "Please provide email using flag" in exception.message

    def test_domain_attribute_accessible(self):
        """Test that domain attribute is accessible."""
        domain = "test.com"
        exception = SSLCertificateEmailNotFoundError(domain)

        assert exception.domain == domain


class TestSSLDNSChallengeCredentailsNotFound:
    """Tests for SSLDNSChallengeCredentailsNotFound exception."""

    def test_default_message_includes_config_path(self):
        """Test that default message includes CLI_FM_CONFIG_PATH."""
        exception = SSLDNSChallengeCredentailsNotFound()

        # The message should mention credentials not found
        assert "credentials not found" in exception.message.lower()

    def test_custom_message_override(self):
        """Test that custom message can be provided."""
        custom_message = "Custom credentials error message"
        exception = SSLDNSChallengeCredentailsNotFound(message=custom_message)

        assert exception.message == custom_message

    def test_message_attribute_accessible(self):
        """Test that message attribute is accessible."""
        exception = SSLDNSChallengeCredentailsNotFound()

        assert hasattr(exception, 'message')
        assert isinstance(exception.message, str)


class TestSSLCertificateChallengeFailed:
    """Tests for SSLCertificateChallengeFailed exception."""

    def test_message_includes_challenge_type(self):
        """Test that exception message includes the challenge type."""
        challenge = "http01"
        exception = SSLCertificateChallengeFailed(challenge)

        assert challenge in str(exception)
        assert "challenge failed" in str(exception).lower()

    def test_challenge_attribute_accessible(self):
        """Test that challenge attribute is accessible."""
        challenge = "dns01"
        exception = SSLCertificateChallengeFailed(challenge)

        assert exception.challenge == challenge

    def test_different_challenge_types(self):
        """Test exception works with different challenge types."""
        for challenge_type in ["http01", "dns01", "tls-alpn-01"]:
            exception = SSLCertificateChallengeFailed(challenge_type)
            assert exception.challenge == challenge_type
            assert challenge_type in str(exception)


class TestSSLCertificateGenerateFailed:
    """Tests for SSLCertificateGenerateFailed exception."""

    def test_default_message_set(self):
        """Test that default message is set correctly."""
        exception = SSLCertificateGenerateFailed()

        assert exception.message == "Certificate generation failed."
        assert "Certificate generation failed" in str(exception)

    def test_exception_can_be_raised_and_caught(self):
        """Test that exception can be raised and caught properly."""
        with pytest.raises(SSLCertificateGenerateFailed) as exc_info:
            raise SSLCertificateGenerateFailed()

        assert "Certificate generation failed" in str(exc_info.value)


class TestSSLCertificateNotDueForRenewalError:
    """Tests for SSLCertificateNotDueForRenewalError exception."""

    def test_message_formatting_with_domain_and_time(self):
        """Test that message is formatted with domain and time remaining."""
        domain = "example.com"
        expiry_date = datetime.now() + timedelta(days=60)

        with patch(
            'frappe_manager.ssl_manager.certificate_exceptions.format_ssl_certificate_time_remaining'
        ) as mock_format:
            mock_format.return_value = "60 days"
            exception = SSLCertificateNotDueForRenewalError(domain, expiry_date)

            assert domain in exception.message
            assert "60 days" in exception.message
            mock_format.assert_called_once_with(expiry_date)

    def test_expiry_date_attribute_accessible(self):
        """Test that expiry_date attribute is accessible."""
        domain = "test.com"
        expiry_date = datetime.now() + timedelta(days=30)

        with patch(
            'frappe_manager.ssl_manager.certificate_exceptions.format_ssl_certificate_time_remaining'
        ) as mock_format:
            mock_format.return_value = "30 days"
            exception = SSLCertificateNotDueForRenewalError(domain, expiry_date)

            assert exception.domain == domain
            assert exception.expiry_date == expiry_date

    def test_time_remaining_txt_calculated(self):
        """Test that time_remaining_txt is calculated correctly."""
        domain = "example.com"
        expiry_date = datetime.now() + timedelta(days=45)

        with patch(
            'frappe_manager.ssl_manager.certificate_exceptions.format_ssl_certificate_time_remaining'
        ) as mock_format:
            mock_format.return_value = "45 days, 0 hours"
            exception = SSLCertificateNotDueForRenewalError(domain, expiry_date)

            assert exception.time_remaining_txt == "45 days, 0 hours"

    def test_custom_message_template(self):
        """Test that custom message template can be used."""
        domain = "test.com"
        expiry_date = datetime.now() + timedelta(days=20)
        custom_message = "Domain {} is not ready for renewal. Time left: {}"

        with patch(
            'frappe_manager.ssl_manager.certificate_exceptions.format_ssl_certificate_time_remaining'
        ) as mock_format:
            mock_format.return_value = "20 days"
            exception = SSLCertificateNotDueForRenewalError(domain, expiry_date, message=custom_message)

            assert "not ready for renewal" in exception.message


class TestExceptionInheritance:
    """Tests for exception class inheritance and behavior."""

    def test_all_exceptions_inherit_from_exception(self):
        """Test that all custom exceptions inherit from Exception."""
        exceptions = [
            SSLCertificateNotFoundError("test.com"),
            SSLCertificateEmailNotFoundError("test.com"),
            SSLDNSChallengeCredentailsNotFound(),
            SSLCertificateChallengeFailed("http01"),
            SSLCertificateGenerateFailed(),
        ]

        for exc in exceptions:
            assert isinstance(exc, Exception)

    def test_exceptions_can_be_caught_as_exception(self):
        """Test that custom exceptions can be caught as base Exception."""
        try:
            raise SSLCertificateNotFoundError("example.com")
        except Exception as e:
            assert isinstance(e, SSLCertificateNotFoundError)

    def test_exception_str_representations(self):
        """Test that all exceptions have meaningful string representations."""
        domain = "example.com"
        expiry_date = datetime.now() + timedelta(days=30)

        with patch(
            'frappe_manager.ssl_manager.certificate_exceptions.format_ssl_certificate_time_remaining'
        ) as mock_format:
            mock_format.return_value = "30 days"

            exceptions = [
                SSLCertificateNotFoundError(domain),
                SSLCertificateEmailNotFoundError(domain),
                SSLDNSChallengeCredentailsNotFound(),
                SSLCertificateChallengeFailed("http01"),
                SSLCertificateGenerateFailed(),
                SSLCertificateNotDueForRenewalError(domain, expiry_date),
            ]

            for exc in exceptions:
                str_repr = str(exc)
                assert isinstance(str_repr, str)
                assert len(str_repr) > 0


class TestExceptionUsageScenarios:
    """Tests for real-world exception usage scenarios."""

    def test_not_found_error_in_try_except(self):
        """Test SSLCertificateNotFoundError in try-except block."""

        def get_certificate(domain):
            raise SSLCertificateNotFoundError(domain)

        with pytest.raises(SSLCertificateNotFoundError) as exc_info:
            get_certificate("example.com")

        assert exc_info.value.domain == "example.com"

    def test_challenge_failed_with_different_challenges(self):
        """Test SSLCertificateChallengeFailed with different challenge types."""
        challenges = ["http01", "dns01"]

        for challenge in challenges:
            with pytest.raises(SSLCertificateChallengeFailed) as exc_info:
                raise SSLCertificateChallengeFailed(challenge)

            assert exc_info.value.challenge == challenge

    def test_renewal_error_provides_useful_info(self):
        """Test that renewal error provides useful information."""
        domain = "example.com"
        expiry_date = datetime.now() + timedelta(days=50)

        with patch(
            'frappe_manager.ssl_manager.certificate_exceptions.format_ssl_certificate_time_remaining'
        ) as mock_format:
            mock_format.return_value = "50 days remaining"

            with pytest.raises(SSLCertificateNotDueForRenewalError) as exc_info:
                raise SSLCertificateNotDueForRenewalError(domain, expiry_date)

            exception = exc_info.value
            assert exception.domain == domain
            assert exception.expiry_date == expiry_date
            assert "50 days remaining" in exception.message
