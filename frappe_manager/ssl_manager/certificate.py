
from pydantic import BaseModel

from frappe_manager.ssl_manager import SUPPORTED_SSL_TYPES


class SSLCertificate(BaseModel):
    domain: str
    ssl_type: SUPPORTED_SSL_TYPES
    hsts: str = "off"
    toml_exclude: set | None = {"domain", "toml_exclude"}
