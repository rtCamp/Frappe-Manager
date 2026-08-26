"""The DNS-01 credential set model, shared by bench config and global fm config.

It lives here rather than in `site_manager/bench_config.py` because that module imports
`FMConfigManager` from `metadata_manager`, so `metadata_manager` cannot import from it without a
cycle, and both scopes now store labelled credential sets. Keep this module's imports limited to
`pydantic`, `tomlkit` and `frappe_manager.ssl_manager` for the same reason.
"""

import tomlkit
from pydantic import BaseModel, ConfigDict, EmailStr, Field

from frappe_manager.ssl_manager import DNS_PROVIDER


class DNSProviderConfig(BaseModel):
    """A labelled set of DNS provider credentials for the DNS-01 challenge."""

    model_config = ConfigDict(extra="forbid")

    # The table key is a free-form label (an account name, or a least-privilege token's purpose), so
    # the provider type has to be carried in the entry itself.
    provider: DNS_PROVIDER = Field(DNS_PROVIDER.cloudflare, description="DNS provider this credential set is for.")
    email: EmailStr | None = Field(None, description="DNS provider account email (if required).")
    api_token: str | None = Field(None, description="DNS provider API Token.")
    api_key: str | None = Field(None, description="DNS provider API Key.")

    @property
    def exists(self) -> bool:
        """Check if any DNS credentials are configured."""
        return bool(self.api_token or self.api_key)

    def get_toml_doc(self):
        """Convert to TOML document."""
        model_dict = self.model_dump(exclude_none=True)
        toml_doc = tomlkit.document()

        for key, value in model_dict.items():
            toml_doc[key] = value
        return toml_doc

    @classmethod
    def import_from_toml_doc(cls, toml_doc):
        """Import from TOML document."""
        return cls(**toml_doc)
