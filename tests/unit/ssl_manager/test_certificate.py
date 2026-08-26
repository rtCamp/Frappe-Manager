"""
Unit tests for the SSL certificate models and the parse-time union.

Two things are defended here.

1. `SSLCertificate` itself: the fields a `[[ssl.certificates]]` entry may carry, their defaults,
   and `extra="forbid"` -- a misspelled key is an error rather than a silently ignored one.
2. `CERTIFICATE_ADAPTER`, the discriminated union that is now the ONLY way a certificate is parsed
   from disk. `ssl_type` alone picks the variant, replacing a hand-written kwarg dispatch that
   dropped `hsts` once and `delegation_cname` once by simply forgetting to read them; both are
   pinned below against an entry that also carries every retired key.
"""

import pytest
from pydantic import ValidationError

from frappe_manager.ssl_manager import LETSENCRYPT_PREFERRED_CHALLENGE, SUPPORTED_SSL_TYPES
from frappe_manager.ssl_manager.certificate import (
    RETIRED_CERTIFICATE_KEYS,
    DevCertificate,
    DisabledCertificate,
    SSLCertificate,
)
from frappe_manager.ssl_manager.letsencrypt_certificate import CERTIFICATE_ADAPTER, LetsencryptSSLCertificate
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

    def test_challenge_type_defaults_to_none_on_the_base_model(self):
        """A dev or disabled certificate has no ACME challenge."""
        cert = SSLCertificate(domain="example.com", ssl_type=SUPPORTED_SSL_TYPES.dev)

        assert cert.challenge_type is None
        assert cert.enabled is True

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
        """An ssl_type outside the enum is rejected, and it is `ssl_type` that is blamed."""
        with pytest.raises(ValidationError) as exc_info:
            SSLCertificate(domain="example.com", ssl_type="invalid_type")

        errors = exc_info.value.errors()
        assert [e["type"] for e in errors] == ["enum"]
        assert errors[0]["loc"] == ("ssl_type",)

    def test_a_misspelled_key_is_rejected_rather_than_ignored(self):
        """`extra="forbid"`: a typo in bench_config.toml must not be silently dropped.

        The certificate models are populated by splatting a TOML table, so an accepted-and-ignored
        key means the user's setting never takes effect and nothing says so.
        """
        with pytest.raises(ValidationError) as exc_info:
            SSLCertificate(domain="example.com", ssl_type=SUPPORTED_SSL_TYPES.le, hstss="max-age=31536000")

        errors = exc_info.value.errors()
        assert [e["type"] for e in errors] == ["extra_forbidden"]
        assert errors[0]["loc"] == ("hstss",)

    @pytest.mark.parametrize("domain", TEST_DOMAINS)
    def test_domain_accepts_various_formats(self, domain):
        """Test that domain field accepts various valid formats."""
        cert = SSLCertificate(domain=domain, ssl_type=SUPPORTED_SSL_TYPES.none)

        assert cert.domain == domain


class TestSSLCertificateSerialization:
    """Tests for SSL certificate serialization."""

    def test_model_dump_carries_exactly_the_persisted_fields(self):
        """The dump IS the `[[ssl.certificates]]` entry, so its key set is a contract."""
        cert = SSLCertificate(domain="example.com", ssl_type=SUPPORTED_SSL_TYPES.le, hsts="on")

        assert cert.model_dump() == {
            "domain": "example.com",
            "ssl_type": SUPPORTED_SSL_TYPES.le,
            "challenge_type": None,
            "enabled": True,
            "hsts": "on",
        }

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
            domain="example.com",
            ssl_type=SUPPORTED_SSL_TYPES.le,
            hsts="max-age=31536000; includeSubDomains",
        )

        assert cert.hsts == "max-age=31536000; includeSubDomains"

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


class TestCertificateAdapterVariantSelection:
    """`CERTIFICATE_ADAPTER` is the only reader; `ssl_type` alone chooses the class."""

    @pytest.mark.parametrize(
        ("ssl_type", "expected"),
        [
            ("letsencrypt", LetsencryptSSLCertificate),
            ("dev", DevCertificate),
            # The enum member is `none` but its VALUE, and therefore the discriminator on disk, is
            # the string "disable".
            ("disable", DisabledCertificate),
        ],
    )
    def test_ssl_type_selects_the_variant(self, ssl_type, expected):
        cert = CERTIFICATE_ADAPTER.validate_python({"domain": "app.example.com", "ssl_type": ssl_type})

        assert type(cert) is expected
        assert cert.domain == "app.example.com"

    def test_every_supported_ssl_type_has_a_variant(self):
        """A new SUPPORTED_SSL_TYPES member without a variant would make its benches unreadable."""
        for member in SUPPORTED_SSL_TYPES:
            cert = CERTIFICATE_ADAPTER.validate_python({"domain": "app.example.com", "ssl_type": member.value})

            assert cert.ssl_type is member

    def test_letsencrypt_defaults_are_the_documented_ones(self):
        cert = CERTIFICATE_ADAPTER.validate_python({"domain": "app.example.com", "ssl_type": "letsencrypt"})

        assert cert.challenge_type is LETSENCRYPT_PREFERRED_CHALLENGE.http01
        assert cert.acme_client == "acme.sh"
        assert cert.dns_provider is None
        assert cert.delegation_cname is None

    def test_an_unknown_ssl_type_is_rejected_by_the_discriminator(self):
        with pytest.raises(ValidationError) as exc_info:
            CERTIFICATE_ADAPTER.validate_python({"domain": "app.example.com", "ssl_type": "invalid_type"})

        assert [e["type"] for e in exc_info.value.errors()] == ["union_tag_invalid"]

    def test_a_missing_ssl_type_is_rejected(self):
        with pytest.raises(ValidationError) as exc_info:
            CERTIFICATE_ADAPTER.validate_python({"domain": "app.example.com"})

        assert [e["type"] for e in exc_info.value.errors()] == ["union_tag_not_found"]

    def test_a_misspelled_key_is_rejected_after_the_variant_is_chosen(self):
        with pytest.raises(ValidationError) as exc_info:
            CERTIFICATE_ADAPTER.validate_python(
                {"domain": "app.example.com", "ssl_type": "letsencrypt", "challenge_typo": "dns01"}
            )

        errors = exc_info.value.errors()
        assert [e["type"] for e in errors] == ["extra_forbidden"]
        assert errors[0]["loc"] == ("letsencrypt", "challenge_typo")

    @pytest.mark.parametrize("ssl_type", ["dev", "disable"])
    def test_a_letsencrypt_only_key_is_rejected_on_the_other_variants(self, ssl_type):
        """`delegation_cname` and friends belong to the Let's Encrypt shape alone."""
        with pytest.raises(ValidationError) as exc_info:
            CERTIFICATE_ADAPTER.validate_python(
                {"domain": "app.example.com", "ssl_type": ssl_type, "delegation_cname": "a.fm.gw"}
            )

        assert [e["type"] for e in exc_info.value.errors()] == ["extra_forbidden"]


class TestRetiredKeysAreTolerated:
    """A pre-migration entry still loads, and the keys that are NOT retired survive intact.

    `fm list`, `fm bake` and `fm switch` skip the migration gate, so a hard failure on an
    unmigrated file would take `fm list` down for every bench on the host. Dropping the retired
    keys instead keeps the tool usable until `fm migrate` rewrites the file.
    """

    def _pre_migration_entry(self) -> dict:
        entry = {
            "domain": "a.gg.com",
            "ssl_type": "letsencrypt",
            "challenge_type": "dns01",
            "hsts": "max-age=31536000; includeSubDomains",
            "delegation_cname": "a-gg-com.fm.gw",
            "dns_provider": "acct-b",
            "acme_client": "certbot",
            "enabled": True,
        }
        # Every retired key at once, with values shaped like the ones fm used to write.
        entry.update(
            {
                "api_token": "cf_token",
                "api_key": "cf_key",
                "email": "ops@example.com",
                "preferred_challenge": "dns01",
                "status": "pending",
                "cert_path": "/etc/nginx/certs/a.gg.com.crt",
                "key_path": "/etc/nginx/certs/a.gg.com.key",
                "issued_date": "2026-01-01T00:00:00",
                "last_renewal_attempt": None,
                "toml_exclude": ["domain", "toml_exclude"],
            }
        )
        assert set(entry) >= RETIRED_CERTIFICATE_KEYS, "the fixture must carry every retired key"
        return entry

    def test_an_entry_carrying_every_retired_key_still_loads(self):
        cert = CERTIFICATE_ADAPTER.validate_python(self._pre_migration_entry())

        assert type(cert) is LetsencryptSSLCertificate

    def test_hsts_and_delegation_cname_survive(self):
        """Each of these was silently lost once by the reader this union replaced."""
        cert = CERTIFICATE_ADAPTER.validate_python(self._pre_migration_entry())

        assert cert.hsts == "max-age=31536000; includeSubDomains"
        assert cert.delegation_cname == "a-gg-com.fm.gw"

    def test_the_retired_keys_are_gone_from_the_parsed_certificate(self):
        """Dropped, not carried: the next export must not write them back to disk."""
        cert = CERTIFICATE_ADAPTER.validate_python(self._pre_migration_entry())

        dumped = cert.model_dump()

        assert RETIRED_CERTIFICATE_KEYS.isdisjoint(dumped)
        assert dumped == {
            "domain": "a.gg.com",
            "ssl_type": SUPPORTED_SSL_TYPES.le,
            "challenge_type": LETSENCRYPT_PREFERRED_CHALLENGE.dns01,
            "enabled": True,
            "hsts": "max-age=31536000; includeSubDomains",
            "acme_client": "certbot",
            "dns_provider": "acct-b",
            "delegation_cname": "a-gg-com.fm.gw",
        }

    @pytest.mark.parametrize("retired", sorted(RETIRED_CERTIFICATE_KEYS))
    def test_each_retired_key_is_tolerated_on_its_own(self, retired):
        cert = CERTIFICATE_ADAPTER.validate_python(
            {"domain": "a.gg.com", "ssl_type": "letsencrypt", retired: "whatever"}
        )

        assert not hasattr(cert, retired)

    @pytest.mark.parametrize("ssl_type", ["dev", "disable"])
    def test_retired_keys_are_tolerated_on_every_variant(self, ssl_type):
        """A dev or disabled entry carried `status` and `toml_exclude` too."""
        cert = CERTIFICATE_ADAPTER.validate_python(
            {"domain": "a.gg.com", "ssl_type": ssl_type, "status": "pending", "toml_exclude": ["domain"]}
        )

        assert cert.ssl_type.value == ssl_type
        assert not hasattr(cert, "status")

    def test_tolerance_does_not_extend_to_an_unknown_key(self):
        """Only the enumerated retired keys are dropped; anything else is still a typo."""
        with pytest.raises(ValidationError) as exc_info:
            CERTIFICATE_ADAPTER.validate_python(
                {"domain": "a.gg.com", "ssl_type": "letsencrypt", "api_tokenn": "cf_token"}
            )

        assert [e["type"] for e in exc_info.value.errors()] == ["extra_forbidden"]
