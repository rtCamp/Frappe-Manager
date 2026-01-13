from enum import Enum


class SUPPORTED_SSL_TYPES(str, Enum):
    le = "letsencrypt"
    none = "disable"


class LETSENCRYPT_PREFERRED_CHALLENGE(str, Enum):
    dns01 = "dns01"
    http01 = "http01"


class DNS_PROVIDER(str, Enum):
    """Supported DNS providers for DNS-01 challenge."""

    cloudflare = "cloudflare"
    # route53 = "route53"        # AWS Route53 (future)
    # digitalocean = "digitalocean"  # DigitalOcean (future)
    # google = "google"          # Google Cloud DNS (future)
