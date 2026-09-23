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

from pathlib import Path
from typing import Any

import tomlkit
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from frappe_manager.output_manager import warn_or_log
from frappe_manager.ssl_manager import LETSENCRYPT_PREFERRED_CHALLENGE
from frappe_manager.ssl_manager.certificate import SSLCertificate
from frappe_manager.ssl_manager.letsencrypt_certificate import (
    build_letsencrypt_certificate,
)


class ExternalDomainConfig(BaseModel):
    """
    Configuration for an external domain SSL certificate.

    Attributes:
        domain: The domain name (e.g., "myapp.example.com")
        ssl_type: Certificate type (always "letsencrypt" for now)
        added_at: ISO 8601 timestamp when certificate was added
        challenge_type: Challenge type ("http01" or "dns01")
        delegation_cname: Optional CNAME for DNS-01 delegation
        acme_client: ACME client to use (currently only "acme.sh" is supported)

    extra="allow", not a plain dataclass: this was the one config surface the forbid/ignore ->
    allow sweep never reached (see bench_config.py / certificate.py:19-23 for the sweep and the
    incident that motivated it). A dataclass constructor raises TypeError on any keyword it does
    not declare, so a `[domains.*]` entry with one stray key made `_load` skip the WHOLE entry
    (`except (KeyError, TypeError): continue`), and the next `add_domain`/`remove_domain` --
    which rebuilds the entire document from named keys -- deleted the entry from disk outright,
    silently dropping that domain's certificate renewal with it. `extra="allow"` keeps the entry
    parseable (the stray key lands in `model_extra`), `_load` warns about it below, and `_save`
    writes `model_extra` back out so the key, and the certificate, both survive.
    """

    model_config = ConfigDict(extra="allow")

    domain: str = Field(description='The domain name (e.g., "myapp.example.com")')
    ssl_type: str = Field(description='Certificate type (always "letsencrypt" for now)')
    added_at: str = Field(description="ISO 8601 timestamp when certificate was added")
    challenge_type: str = Field(description='Challenge type ("http01" or "dns01")')
    delegation_cname: str | None = Field(default=None, description="Optional CNAME for DNS-01 delegation")
    acme_client: str = Field(default="acme.sh", description='ACME client to use (currently only "acme.sh" is supported)')


class ExternalDomainConfigManager:
    """
    Manages external domain SSL configurations stored in external_domains.toml.

    This manager handles SSL certificate configurations for domains not associated
    with Frappe benches, enabling FM's SSL management to work with any Docker project
    that uses FM's nginx-proxy (via fm-frontend-network).

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

        if not self.config_path.exists():
            self._save({})

    def _load(self) -> tuple[dict[str, ExternalDomainConfig], dict[str, Any]]:
        """
        Load all external domains from TOML file.

        Returns:
            (domains, unparsed). `domains` maps domain name -> ExternalDomainConfig for every
            entry that could be built. `unparsed` maps the entry's original TOML table key (e.g.
            "myapp_example_com") -> its raw tomlkit value, for every entry that could NOT be
            built (a required field is missing or the entry is the wrong shape). `_save` writes
            `unparsed` back out untouched so a later add/remove does not delete it. A stray extra
            key alone no longer lands here: ExternalDomainConfig is extra="allow", so it still
            builds and the key survives as `model_extra`, warned about below instead.
        """
        if not self.config_path.exists():
            return {}, {}

        try:
            data = tomlkit.parse(self.config_path.read_text())
        except Exception:
            return {}, {}

        domains: dict[str, ExternalDomainConfig] = {}
        unparsed: dict[str, Any] = {}

        for key, value in data.get("domains", {}).items():
            try:
                # Backward compatibility: rename preferred_challenge to challenge_type
                if "preferred_challenge" in value and "challenge_type" not in value:
                    value["challenge_type"] = value.pop("preferred_challenge")

                # Backward compatibility: remove email field if present (discontinued June 2025)
                value.pop("email", None)

                config = ExternalDomainConfig(**value)
            except (KeyError, TypeError, ValidationError):
                # A required field is missing, or the entry is not a table at all: there is no
                # usable object to build, but the operator's line is not deleted for that -- it is
                # carried through to `_save` verbatim so an unrelated add/remove elsewhere in this
                # file never erases it. Read paths warn, they never raise (see warn_or_log):
                # raising here would take fm ssl list/renew/add down over one other domain's typo.
                unparsed[key] = value
                warn_or_log(
                    "external_domain_manager",
                    f"external_domains.toml: '[domains.{key}]' could not be read (a required "
                    "field is missing or malformed); fm will not manage or renew its certificate "
                    "until this is fixed. The entry is kept as-is on disk.",
                )
                continue

            if config.model_extra:
                warn_or_log(
                    "external_domain_manager",
                    f"external_domains.toml: '{config.domain}' has unrecognised key(s) "
                    f"{', '.join(sorted(config.model_extra))}; check for a typo, since fm will not use them.",
                )

            domains[config.domain] = config

        return domains, unparsed

    def _save(self, domains: dict[str, ExternalDomainConfig], unparsed: dict[str, Any] | None = None):
        """
        Save all external domains to TOML file.

        Args:
            domains: Dictionary mapping domain names to ExternalDomainConfig objects
            unparsed: Raw entries `_load` could not build (see its docstring), written back
                verbatim under their original TOML key. Rebuilding the whole document from
                `domains` alone is exactly the mechanism that used to delete these outright.
        """
        doc = tomlkit.document()
        domains_table = tomlkit.table()

        for domain, config in domains.items():
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

            # Retained unknown keys (extra="allow"): fm never deletes a key it does not
            # understand, so whatever this entry carried beyond the six known fields goes back
            # out unchanged instead of being pruned by this whole-document rebuild.
            for extra_key, extra_value in (config.model_extra or {}).items():
                domain_table[extra_key] = extra_value

            domains_table[safe_key] = domain_table

        # Entries `_load` could not build at all: preserved verbatim, never overwritten by a
        # same-keyed valid entry (which cannot happen -- a parsed entry keys by its own domain
        # name, an unparsed one by its original TOML key, and the two are only ever equal by
        # coincidence, in which case keeping the parsed, known-good copy is correct).
        for raw_key, raw_value in (unparsed or {}).items():
            if raw_key not in domains_table:
                domains_table[raw_key] = raw_value

        doc["domains"] = domains_table

        with open(self.config_path, "w") as f:
            f.write(tomlkit.dumps(doc))

    def add_domain(self, config: ExternalDomainConfig):
        """
        Add a new external domain configuration.

        Args:
            config: External domain configuration to add

        Raises:
            ValueError: If domain already exists in external domains
        """
        domains, unparsed = self._load()

        if config.domain in domains:
            raise ValueError(f"Domain {config.domain} already exists in external domains")

        domains[config.domain] = config
        self._save(domains, unparsed)

    def remove_domain(self, domain: str) -> bool:
        """
        Remove an external domain configuration.

        Args:
            domain: Domain name to remove

        Returns:
            True if domain was removed, False if domain was not found
        """
        domains, unparsed = self._load()

        if domain not in domains:
            return False

        del domains[domain]
        self._save(domains, unparsed)
        return True

    def get_domain(self, domain: str) -> ExternalDomainConfig | None:
        """
        Get configuration for a specific domain.

        Args:
            domain: Domain name to retrieve

        Returns:
            ExternalDomainConfig or None if domain not found
        """
        domains, _unparsed = self._load()
        return domains.get(domain)

    def list_domains(self) -> list[ExternalDomainConfig]:
        """
        List all external domain configurations.

        Returns:
            List of all external domain configurations sorted by domain name
        """
        domains, _unparsed = self._load()
        return sorted(domains.values(), key=lambda d: d.domain)

    def domain_exists(self, domain: str) -> bool:
        """
        Check if a domain exists in external domains.

        Args:
            domain: Domain name to check

        Returns:
            True if domain exists, False otherwise
        """
        domains, _unparsed = self._load()
        return domain in domains

    def to_ssl_certificate(self, domain: str) -> SSLCertificate | None:
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

        if config.challenge_type == "dns01":
            challenge_type = LETSENCRYPT_PREFERRED_CHALLENGE.dns01
        else:
            challenge_type = LETSENCRYPT_PREFERRED_CHALLENGE.http01

        # The shared builder now lives in ssl_manager (below the command layer), so all three call
        # sites -- the two SSL commands and this one -- build the certificate through one function.
        # It could not live in commands.ssl.helpers: importing that from here is a hard circular
        # import, since commands.ssl imports this module.
        return build_letsencrypt_certificate(
            config.domain,
            challenge_type,
            config.delegation_cname,
            acme_client=config.acme_client,
        )
