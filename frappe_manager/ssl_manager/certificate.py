from datetime import datetime
from pydantic import BaseModel
from pathlib import Path
from typing import List,Optional
from frappe_manager.ssl_manager import SUPPORTED_SSL_TYPES
from frappe_manager.utils.site import is_wildcard_fqdn
from frappe_manager.utils.helpers import  change_parent, get_certificate_expiry_date

class SSLCertificate(BaseModel):
    domain: str
    ssl_type: SUPPORTED_SSL_TYPES
    hsts: str = 'off'
    alias_domains: List[str] = []
    toml_exclude: Optional[set] = {'domain','alias_domains','toml_exclude'}

    @property
    def has_wildcard(self) -> bool:
        return any(is_wildcard_fqdn(domain) for domain in self.alias_domains)

class RenewableSSLCertificate(BaseModel):
    domain: str
    ssl_type: SUPPORTED_SSL_TYPES
    hsts: str = 'off'
    privkey_path: Path
    fullchain_path: Path
    proxy_certs_dir: Path
    ssl_service_container_dir: Path
    alias_domains: List[str] = []
    toml_exclude: Optional[set] = {'domain','alias_domains','toml_exclude'}

    # @model_validator(mode='before')
    # def configure_expiry(cls, values):
    #     if 'fullchain_path' in values:
    #         if 'expiry' not in values or values['expiry'] is None:
    #             values['expiry'] = get_expiry_date(Path(values['fullchain_path']))
    #     return values


    @property
    def expiry(self) -> datetime:
        return get_certificate_expiry_date(Path(self.fullchain_path))

    @property
    def proxy_fullchain_path(self) -> Path:
        return self.proxy_certs_dir / f"{self.domain}.crt"

    @property
    def proxy_privkey_path(self) -> Path:
        return self.proxy_certs_dir / f"{self.domain}.key"

    @property
    def container_fullchain_path(self) -> Path:
        return change_parent(self.fullchain_path, self.ssl_service_container_dir, target_subpath=f'ssl/{self.ssl_type.value}')

    @property
    def container_privkey_path(self) -> Path:
        return change_parent(self.privkey_path, self.ssl_service_container_dir, target_subpath=f'ssl/{self.ssl_type.value}')

    @property
    def has_wildcard(self) -> bool:
        return any(is_wildcard_fqdn(domain) for domain in self.alias_domains)
