"""The Let's Encrypt certificate variant, the shared builder, and the parse-time union."""

from typing import Annotated, Literal

from pydantic import Field, TypeAdapter

from frappe_manager.ssl_manager import LETSENCRYPT_PREFERRED_CHALLENGE, SUPPORTED_SSL_TYPES
from frappe_manager.ssl_manager.certificate import DevCertificate, DisabledCertificate, SSLCertificate


class LetsencryptSSLCertificate(SSLCertificate):
    """A Let's Encrypt certificate, issued over HTTP-01 or DNS-01."""

    ssl_type: Literal[SUPPORTED_SSL_TYPES.le] = SUPPORTED_SSL_TYPES.le
    # Non-optional, unlike the base: acme.sh code reads `.challenge_type.value` without a guard, and
    # every Let's Encrypt certificate has a challenge by definition.
    challenge_type: LETSENCRYPT_PREFERRED_CHALLENGE = LETSENCRYPT_PREFERRED_CHALLENGE.http01
    acme_client: str = Field("acme.sh", description="ACME client used for issuance.")
    dns_provider: str | None = Field(
        None,
        description="Label of the `[ssl.dns_providers]` credential set that authenticates this domain's DNS-01 challenge. Absent means the set labelled 'cloudflare'.",
    )
    delegation_cname: str | None = Field(
        None,
        description="Zone that _acme-challenge is delegated to, passed to acme.sh as --challenge-alias. Set by `fm ssl add --cname`.",
    )


# `delegation_cname` used to be a whole subclass, `CustomDomainCertificate`, which is why the class
# could not be chosen from `ssl_type` alone: both Let's Encrypt shapes shared one ssl_type, so the
# reader had to dispatch on whether a key was present. As a plain field it discriminates cleanly, and
# the only production code that cared was one isinstance check guarding a `--challenge-alias` flag
# that already tested this field's truthiness anyway.
CERTIFICATE_ADAPTER: TypeAdapter[SSLCertificate] = TypeAdapter(
    Annotated[
        DisabledCertificate | DevCertificate | LetsencryptSSLCertificate,
        Field(discriminator="ssl_type"),
    ]
)


def build_letsencrypt_certificate(
    domain: str,
    challenge: LETSENCRYPT_PREFERRED_CHALLENGE,
    cname: str | None,
    acme_client: str | None = None,
    dns_provider: str | None = None,
) -> LetsencryptSSLCertificate:
    """Build the Let's Encrypt certificate for a domain.

    Credentials are deliberately absent: they are resolved from `[ssl.dns_providers]` at issuance and
    at renewal, so a certificate never carries one.

    Lives here, in ssl_manager, rather than in the command layer: the command modules and
    ``external_domain_manager`` all build this identical object, and a home under
    ``frappe_manager.commands`` cannot serve the third one. That is not merely a layering preference,
    importing the command layer from ``ssl_manager.external_domain_manager`` is a hard circular
    import (commands.ssl.helpers -> ... -> external_domain_manager, which is still partially
    initialised), verified by ImportError.

    A None ``acme_client`` coalesces to the field's own default rather than being forwarded, because
    forwarding None would replace the default with nothing.
    """
    return LetsencryptSSLCertificate(
        domain=domain,
        challenge_type=challenge,
        acme_client=acme_client or "acme.sh",
        dns_provider=dns_provider,
        delegation_cname=cname,
    )
