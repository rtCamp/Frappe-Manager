import datetime
from typing import List
from pathlib import Path
from dataclasses import dataclass, field
from frappe_manager.utils.site import is_fqdn, is_wildcard_fqdn

@dataclass
class SSLCertificate:
    domain: str
    alias_domains: list = field(default_factory=list)
    has_wildcard: bool = False

    def __post_init__(self):
        if not self.has_wildcard:
            for domain in self.alias_domains:
                contains_wildcard = is_wildcard_fqdn(domain)
                if not contains_wildcard:
                    self.has_wildcard = True
                    break

@dataclass
class RenewableSSLCertificate:
    domain: str
    privkey_path: Path
    fullchain_path: Path
    expiry: datetime.datetime
    has_wildcard: bool
    alias_domains: list = field(default_factory=list)

    # def __post_init__(self):
    #     for domain in self.alias_domains:
    #         contains_wildcard = is_fqdn(domain)
    #         if not contains_wildcard:
    #             self.has_wildcard = True
    #             break
