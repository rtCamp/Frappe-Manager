from pydantic import BaseModel
from typing import List, Optional
from frappe_manager.ssl_manager import SUPPORTED_SSL_TYPES
from frappe_manager.utils.site import is_wildcard_fqdn


class SSLCertificate(BaseModel):
    domain: str
    ssl_type: SUPPORTED_SSL_TYPES
    hsts: str = 'off'
    alias_domains: List[str] = []
    toml_exclude: Optional[set] = {'domain', 'alias_domains', 'toml_exclude'}

    @property
    def has_wildcard(self) -> bool:
        return any(is_wildcard_fqdn(domain) for domain in self.alias_domains)
