"""
External Domain Configuration Manager

Manages SSL certificates for external (non-bench) Docker projects that use
FM's nginx-proxy. Stores configurations in external_domains.toml.

This module allows Frappe Manager to provide SSL management for any Docker
project, not just Frappe benches, by tracking external domain configurations
separately from bench configurations.

Example external_domains.toml structure:
    [domains.myapp_example_com]
    domain = "myapp.example.com"
    ssl_type = "letsencrypt"
    added_at = "2026-01-14T12:00:00"
    challenge_type = "http01"
    acme_client = "acme.sh"
"""

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Optional
import tomlkit

from frappe_manager.ssl_manager import SUPPORTED_SSL_TYPES, LETSENCRYPT_PREFERRED_CHALLENGE
from frappe_manager.ssl_manager.certificate import SSLCertificate
from frappe_manager.ssl_manager.letsencrypt_certificate import CustomDomainCertificate
from frappe_manager.ssl_manager.letsencrypt_certificate import LetsencryptSSLCertificate


@dataclass
class ExternalDomainConfig:
    """
    Configuration for an external domain SSL certificate.

    Attributes:
        domain: The domain name (e.g., "myapp.example.com")
        ssl_type: Certificate type (always "letsencrypt" for now)
        added_at: ISO 8601 timestamp when certificate was added
        challenge_type: Challenge type ("http01" or "dns01")
        delegation_cname: Optional CNAME for DNS-01 delegation
        acme_client: ACME client to use (currently only "acme.sh" is supported)
    """

    domain: str
    ssl_type: str
    added_at: str
    challenge_type: str  # Changed from preferred_challenge to challenge_type
    delegation_cname: Optional[str] = None
    acme_client: str = "acme.sh"


class ExternalDomainConfigManager:
    """
    Manages external domain SSL configurations stored in external_domains.toml.

    This manager handles SSL certificate configurations for domains not associated
    with Frappe benches, enabling FM's SSL management to work with any Docker project
    that uses FM's nginx-proxy (via fm-global-frontend-network).

    Storage location: <services_path>/nginx-proxy/external_domains.toml

    Usage:
        manager = ExternalDomainConfigManager(config_path)
        manager.add_domain(ExternalDomainConfig(...))
        domains = manager.list_domains()
        manager.remove_domain("example.com")
    """

    def __init__(self, config_path: Path):
        """
        Initialize the external domain config manager.

        Args:
            config_path: Path to external_domains.toml file
        """
        self.config_path = config_path
        self.config_path.parent.mkdir(parents=True, exist_ok=True)

        # Create empty config if doesn't exist
        if not self.config_path.exists():
            self._save({})

    def _load(self) -> dict[str, ExternalDomainConfig]:
        """
        Load all external domains from TOML file.

        Returns:
            Dictionary mapping domain names to ExternalDomainConfig objects
        """
        if not self.config_path.exists():
            return {}

        try:
            data = tomlkit.parse(self.config_path.read_text())
        except Exception:
            # If file is corrupted or empty, return empty dict
            return {}

        domains = {}

        for key, value in data.get("domains", {}).items():
            try:
                # Backward compatibility: rename preferred_challenge to challenge_type
                if "preferred_challenge" in value and "challenge_type" not in value:
                    value["challenge_type"] = value.pop("preferred_challenge")

                # Backward compatibility: remove email field if present (discontinued June 2025)
                value.pop("email", None)

                domains[value["domain"]] = ExternalDomainConfig(**value)
            except (KeyError, TypeError):
                # Skip invalid entries
                continue

        return domains

    def _save(self, domains: dict[str, ExternalDomainConfig]):
        """
        Save all external domains to TOML file.

        Args:
            domains: Dictionary mapping domain names to ExternalDomainConfig objects
        """
        doc = tomlkit.document()
        domains_table = tomlkit.table()

        for domain, config in domains.items():
            # Normalize domain to valid TOML key (replace dots/hyphens with underscores)
            safe_key = domain.replace(".", "_").replace("-", "_")

            domain_table = tomlkit.table()
            domain_table["domain"] = config.domain
            domain_table["ssl_type"] = config.ssl_type
            # Email field removed - Let's Encrypt discontinued notifications (June 2025)
            domain_table["added_at"] = config.added_at
            domain_table["challenge_type"] = config.challenge_type
            domain_table["acme_client"] = config.acme_client

            if config.delegation_cname:
                domain_table["delegation_cname"] = config.delegation_cname

            domains_table[safe_key] = domain_table

        doc["domains"] = domains_table

        with open(self.config_path, 'w') as f:
            f.write(tomlkit.dumps(doc))

    def add_domain(self, config: ExternalDomainConfig):
        """
        Add a new external domain configuration.

        Args:
            config: External domain configuration to add

        Raises:
            ValueError: If domain already exists in external domains
        """
        domains = self._load()

        if config.domain in domains:
            raise ValueError(f"Domain {config.domain} already exists in external domains")

        domains[config.domain] = config
        self._save(domains)

    def remove_domain(self, domain: str) -> bool:
        """
        Remove an external domain configuration.

        Args:
            domain: Domain name to remove

        Returns:
            True if domain was removed, False if domain was not found
        """
        domains = self._load()

        if domain not in domains:
            return False

        del domains[domain]
        self._save(domains)
        return True

    def get_domain(self, domain: str) -> Optional[ExternalDomainConfig]:
        """
        Get configuration for a specific domain.

        Args:
            domain: Domain name to retrieve

        Returns:
            ExternalDomainConfig or None if domain not found
        """
        domains = self._load()
        return domains.get(domain)

    def list_domains(self) -> list[ExternalDomainConfig]:
        """
        List all external domain configurations.

        Returns:
            List of all external domain configurations sorted by domain name
        """
        domains = self._load()
        return sorted(domains.values(), key=lambda d: d.domain)

    def domain_exists(self, domain: str) -> bool:
        """
        Check if a domain exists in external domains.

        Args:
            domain: Domain name to check

        Returns:
            True if domain exists, False otherwise
        """
        return domain in self._load()

    def to_ssl_certificate(self, domain: str) -> Optional[SSLCertificate]:
        """
        Convert external domain config to SSLCertificate object.

        This method creates an SSLCertificate object from the stored configuration,
        which can then be used with SSLCertificateManager for certificate operations.

        Args:
            domain: Domain name

        Returns:
            LetsencryptSSLCertificate or CustomDomainCertificate object, or None if domain not found
        """
        config = self.get_domain(domain)
        if not config:
            return None

        # Parse challenge type
        if config.challenge_type == "dns01":
            challenge_type = LETSENCRYPT_PREFERRED_CHALLENGE.dns01
        else:
            challenge_type = LETSENCRYPT_PREFERRED_CHALLENGE.http01

        # Create certificate object with CNAME delegation if present
        if config.delegation_cname:
            return CustomDomainCertificate(
                domain=config.domain,
                ssl_type=SUPPORTED_SSL_TYPES.le,
                # Email removed - Let's Encrypt discontinued notifications (June 2025)
                # Cloudflare credentials loaded from FM config at runtime
                api_token=None,
                api_key=None,
                challenge_type=challenge_type,
                delegation_cname=config.delegation_cname,
                acme_client=config.acme_client,
            )
        else:
            return LetsencryptSSLCertificate(
                domain=config.domain,
                ssl_type=SUPPORTED_SSL_TYPES.le,
                # Email removed - Let's Encrypt discontinued notifications (June 2025)
                # Cloudflare credentials loaded from FM config at runtime
                api_token=None,
                api_key=None,
                challenge_type=challenge_type,
                acme_client=config.acme_client,
            )

    def get_count(self) -> int:
        """
        Get the total number of external domains configured.

        Returns:
            Number of external domains
        """
        return len(self._load())
