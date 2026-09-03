"""`format_ssl_certificate_time_remaining` is the one place fm turns a certificate's expiry date
into words, for every caller that reports one: the "not due for renewal" message, a custom
certificate's "expired"/"expires soon" messages, and `fm info`'s SSL card.

The interesting part is the sign. A future expiry (still valid) and a past one (already expired)
both compute the SAME magnitude of elapsed time, but must not read the same way: "expired -65 days
3 hours 10 mins" is what a bare, unsigned timedelta.days produces, and is not a sentence. This pins
both sides of that boundary.
"""

from datetime import UTC, datetime, timedelta

from frappe_manager.utils.helpers import format_ssl_certificate_time_remaining


class TestFutureExpiry:
    """Not yet expired: bare magnitude, no directional word."""

    def test_reads_as_a_bare_duration(self):
        expiry = datetime.now(UTC) + timedelta(days=25, hours=3, minutes=10)

        text = format_ssl_certificate_time_remaining(expiry)

        # Exact minute is timing-sensitive (test setup and the call are microseconds apart, which
        # can round a :10 down to :09); the day/hour magnitude and the absence of a sign or "ago"
        # are what this test actually defends.
        assert text.startswith("25 days 3 hours ")
        assert text.endswith(" mins")
        assert "ago" not in text
        assert "-" not in text

    def test_naive_datetime_input_still_works(self):
        """`get_certificate_expiry_date` can return a naive datetime; today_date is built from
        the SAME tzinfo (None), so this must not raise on the subtraction."""
        expiry = datetime.now() + timedelta(days=5)

        text = format_ssl_certificate_time_remaining(expiry)

        assert "ago" not in text


class TestPastExpiry:
    """Already expired: same magnitude, with 'ago' appended -- never a bare negative count."""

    def test_reads_naturally_with_ago(self):
        expiry = datetime.now(UTC) - timedelta(days=65, hours=3, minutes=10)

        text = format_ssl_certificate_time_remaining(expiry)

        assert text == "65 days 3 hours 10 mins ago"
        assert "-" not in text

    def test_the_expired_caller_reads_as_a_full_sentence(self):
        """The exact shape `custom_certificate_service.py` builds: f"expired {remaining}."."""
        expiry = datetime.now(UTC) - timedelta(days=65)

        message = f"expired {format_ssl_certificate_time_remaining(expiry)}."

        assert message == "expired 65 days 0 hours 0 mins ago."


class TestBoundary:
    """The instant an expiry date crosses from future to past."""

    def test_a_few_seconds_in_the_future_has_no_ago(self):
        expiry = datetime.now(UTC) + timedelta(seconds=5)

        assert "ago" not in format_ssl_certificate_time_remaining(expiry)

    def test_a_few_seconds_in_the_past_has_ago(self):
        expiry = datetime.now(UTC) - timedelta(seconds=5)

        assert format_ssl_certificate_time_remaining(expiry).endswith("ago")
