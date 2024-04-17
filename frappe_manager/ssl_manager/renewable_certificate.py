from typing import Optional
from pathlib import Path
from datetime import datetime
from pathlib import Path
from pathlib import Path
from frappe_manager.ssl_manager.certificate import RenewableSSLCertificate
from frappe_manager.utils.helpers import  change_parent, get_certificate_expiry_date
from pydantic import EmailStr, model_validator

class RenewableLetsencryptSSLCertificate(RenewableSSLCertificate):
    privkey_path: Path
    fullchain_path: Path
    proxy_certs_dir: Path
    ssl_service_container_dir: Path
    email: EmailStr
    toml_exclude: Optional[set] = {'alias_domains', 'expiry','domain','toml_exclude'}


    # @model_validator(mode='before')
    # def configure_expiry(cls, values):
    #     if 'fullchain_path' in values:
    #         if 'expiry' not in values or values['expiry'] is None:
    #             values['expiry'] = get_certificate_expiry_date(Path(values['fullchain_path']))
        # return values


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

