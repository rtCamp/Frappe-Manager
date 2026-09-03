"""
The SSL certificate models, and the keys that no longer belong in one.

`SSLCertificate` is the shared base and the type every caller annotates against. The concrete
variants below narrow `ssl_type` to a single value each, which is what lets a certificate be parsed
by a discriminated union instead of the hand-written value dispatch that used to pick the class: that
dispatch silently downgraded a certificate whenever it forgot to read a key, and it did so twice,
losing `hsts` once and `delegation_cname` once.
"""

from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from frappe_manager.ssl_manager import LETSENCRYPT_PREFERRED_CHALLENGE, SUPPORTED_SSL_TYPES

# Keys fm used to write into a `[[ssl.certificates]]` entry and no longer does. They are dropped
# before validation rather than rejected, and this is deliberately the ONLY tolerance of an old shape
# left in the reader: `extra="forbid"` is what turns a misspelled key into an error instead of a
# silently ignored one, but `fm list`, `fm bake` and `fm switch` skip the migration gate
# (migration_constants.py:19-26), so failing hard here would take `fm list` down for every bench on
# the host because one file had not been migrated yet. The migration removes these from disk; this
# only keeps the tool usable until it runs. Nothing here is INTERPRETED: a retired key never changes
# behaviour, it just stops being an error.
RETIRED_CERTIFICATE_KEYS = frozenset(
    {
        # Credentials were never certificate state. They belong to `[ssl.dns_providers]`, selected by
        # a certificate's `dns_provider` label, and a copy on the certificate outlived revocation.
        "api_token",
        "api_key",
        "email",
        # Renamed to challenge_type in 0.19.0.
        "preferred_challenge",
        # Issuance state acme.sh owns. Nothing ever assigned these, so `status` reached every file as
        # the frozen literal "pending" and the expiry properties keyed off an always-None cert_path.
        "status",
        "cert_path",
        "key_path",
        "issued_date",
        "last_renewal_attempt",
        # A serialization detail stored as data; the dumper always hardcoded its own exclude set.
        "toml_exclude",
    }
)


class SSLCertificate(BaseModel):
    """One domain's TLS configuration, as recorded in `[[ssl.certificates]]`."""

    model_config = ConfigDict(extra="forbid")

    domain: str = Field(description="Hostname this certificate covers.")
    ssl_type: SUPPORTED_SSL_TYPES = Field(
        description="'letsencrypt', 'dev' for a certificate from fm's local CA, or 'disable' for none."
    )
    # Optional on the base because a dev or disabled certificate has no ACME challenge, while
    # `get_dns_credentials_for_certificate` reads this on whatever it is handed. The Let's Encrypt
    # variant narrows it to a real value.
    challenge_type: LETSENCRYPT_PREFERRED_CHALLENGE | None = Field(
        None, description="ACME validation method: 'http01' or 'dns01'. Let's Encrypt only."
    )
    enabled: bool = Field(default=True, description="Whether this certificate participates in issuance.")
    hsts: str = Field("off", description="Strict-Transport-Security value the proxy sends, or 'off'.")

    @model_validator(mode="before")
    @classmethod
    def _drop_retired_keys(cls, data):
        if isinstance(data, dict):
            return {key: value for key, value in data.items() if key not in RETIRED_CERTIFICATE_KEYS}
        return data


class DevCertificate(SSLCertificate):
    """A certificate from fm's local CA (`fm ssl add --dev`)."""

    ssl_type: Literal[SUPPORTED_SSL_TYPES.dev] = SUPPORTED_SSL_TYPES.dev


class DisabledCertificate(SSLCertificate):
    """No certificate for this domain. Accepted on read; fm drops the domain rather than writing one."""

    ssl_type: Literal[SUPPORTED_SSL_TYPES.none] = SUPPORTED_SSL_TYPES.none


class CustomCertificate(SSLCertificate):
    """A bring-your-own certificate, copied in verbatim (`fm ssl add --custom`). fm issues nothing
    for this variant: no ACME account, no challenge, no local CA -- the operator is the CA.

    `cert_source`/`key_source`/`ca_source` carry the `--cert`/`--key`/`--ca` files ONLY for the
    lifetime of the `fm ssl add --custom` call that constructs this object: `exclude=True` keeps
    every one of them out of `model_dump()`, and therefore out of `[[ssl.certificates]]` on disk
    (see `ssl_certificate_to_toml_doc`, which dumps the model verbatim). A certificate read back
    from bench_config.toml -- which is every certificate `CustomCertificateService.renew_certificate`
    or a later `fm ssl add`/`list`/`bake` run ever sees -- therefore always has these as None. That
    is deliberate, not an oversight: fm does not remember a host path to the operator's original
    files, the same choice already made for `db_ssl_ca` (see db_tls.py). A recorded path would go
    stale the moment the bench moves host, gets restored elsewhere, or the operator deletes the
    original file after import, with no way for fm to notice.

    Named `*_source`, not `cert_path`/`key_path`: those two are already RETIRED_CERTIFICATE_KEYS
    (acme.sh-issuance leftovers from before this variant existed), so the shared
    `_drop_retired_keys` before-validator this class inherits would silently strip them from every
    constructor call -- including `CustomCertificateService.generate_certificate`'s -- and every
    import would look like it had no source files at all.
    """

    ssl_type: Literal[SUPPORTED_SSL_TYPES.custom] = SUPPORTED_SSL_TYPES.custom

    cert_source: Path | None = Field(
        None, exclude=True, description="Source certificate file from --cert. Not persisted."
    )
    key_source: Path | None = Field(
        None, exclude=True, description="Source private key file from --key. Not persisted."
    )
    ca_source: Path | None = Field(
        None, exclude=True, description="Source CA bundle file from --ca, if given. Not persisted."
    )
