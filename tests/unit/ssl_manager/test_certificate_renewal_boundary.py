"""A certificate is due for renewal the moment it enters the renewal window.

`list_certificates` computes `needs_renewal = not (expiry - SSL_RENEW_BEFORE_DAYS) > now`.
The instant the threshold equals now -- i.e. the certificate has exactly
SSL_RENEW_BEFORE_DAYS of life left -- it must already report as needing renewal. Being
one comparison lazy at that instant means the renewal cron that runs at that moment says
"nothing to do", and fm silently waits another cycle with the window shrinking.

The clock is frozen so the boundary is exact rather than a race against wall time.
"""

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from frappe_manager import SSL_RENEW_BEFORE_DAYS

MODULE = "frappe_manager.ssl_manager.ssl_certificate_manager"
# Naive on purpose: the code under test calls `datetime.now()` without a timezone.
NOW = datetime(2026, 1, 1, 12, 0, 0)  # noqa: DTZ001


class _FrozenClock(datetime):
    """`datetime` stand-in whose `now()` never moves."""

    @classmethod
    def now(cls, tz=None):
        return NOW if tz is None else NOW.replace(tzinfo=tz)


@pytest.fixture
def listing(mocker, ssl_certificate_manager):
    """Return a callable: expiry date -> the single entry `list_certificates` reports."""
    ssl_certificate_manager.link_manager.get_certificate_paths.return_value = (
        Path("/privkey.pem"),
        Path("/fullchain.pem"),
    )
    mocker.patch(f"{MODULE}.datetime", _FrozenClock)
    expiry = mocker.patch(f"{MODULE}.get_certificate_expiry_date")

    def _list(expiry_date):
        expiry.return_value = expiry_date
        results = ssl_certificate_manager.list_certificates()
        assert len(results) == 1
        return results[0]

    return _list


class TestRenewalWindowBoundary:
    def test_certificate_with_exactly_the_threshold_left_is_due(self, listing):
        info = listing(NOW + timedelta(days=SSL_RENEW_BEFORE_DAYS))

        assert info["needs_renewal"] is True
        assert info["days_until_expiry"] == SSL_RENEW_BEFORE_DAYS

    def test_one_second_more_life_is_not_yet_due(self, listing):
        info = listing(NOW + timedelta(days=SSL_RENEW_BEFORE_DAYS, seconds=1))

        assert info["needs_renewal"] is False

    def test_one_second_inside_the_window_is_due(self, listing):
        info = listing(NOW + timedelta(days=SSL_RENEW_BEFORE_DAYS) - timedelta(seconds=1))

        assert info["needs_renewal"] is True

    def test_expired_certificate_is_due_with_negative_days(self, listing):
        info = listing(NOW - timedelta(days=2))

        assert info["needs_renewal"] is True
        assert info["days_until_expiry"] < 0

    def test_timezone_aware_expiry_at_the_edge_is_also_due(self, listing):
        """An aware expiry date is compared against an aware `now`, same verdict."""
        aware_expiry = NOW.replace(tzinfo=UTC) + timedelta(days=SSL_RENEW_BEFORE_DAYS)

        info = listing(aware_expiry)

        assert info["needs_renewal"] is True
