from pathlib import Path
from datetime import timedelta, datetime
from frappe_manager import SSL_RENEW_BEFORE_DAYS
from frappe_manager.ssl_manager import SUPPORTED_SSL_TYPES
from frappe_manager.ssl_manager.letsencrypt_certificate_service import LetsEncryptCertificateService
from frappe_manager.ssl_manager.no_op_certificate_service import NoOpCertificateService
from frappe_manager.ssl_manager.certificate_exceptions import (
    SSLCertificateNotDueForRenewalError,
    SSLCertificateNotFoundError,
)
from frappe_manager.ssl_manager.certificate import SSLCertificate
from frappe_manager.ssl_manager.nginxproxymanager import NginxProxyManager
from frappe_manager.ssl_manager.ssl_certificate_service import SSLCertificateService
from frappe_manager.utils.helpers import (
    create_symlink,
    get_certificate_expiry_date,
)


class SSLCertificateManager:
    service: SSLCertificateService
    proxy_manager: NginxProxyManager

    def __init__(self, certificate: SSLCertificate, webroot_dir: Path, proxy_manager: NginxProxyManager):
        self.certificate = certificate
        self.proxy_manager = proxy_manager
        self.webroot_dir = webroot_dir
        self.service = self.ssl_service_factory()

    def ssl_service_factory(self):
        # initializing ssl service
        if self.certificate.ssl_type == SUPPORTED_SSL_TYPES.le:
            webroot_dir = self.webroot_dir
            certificate_service = LetsEncryptCertificateService(self.proxy_manager.dirs.ssl.host, webroot_dir)
            return certificate_service

        certificate_service = NoOpCertificateService(Path('/dev/null'))
        return certificate_service

    def set_certificate(self, certificate: SSLCertificate):
        self.certificate = certificate
        self.service = self.ssl_service_factory()

    def has_certificate(self):
        try:
            self.get_certificate_paths()
            return True
        except SSLCertificateNotFoundError:
            return False

    def get_certificate_paths(self):
        try:
            privkey_file_name: str = f"{self.certificate.domain}.key"
            fullchain_file_name: str = f"{self.certificate.domain}.crt"

            paths = []
            for file_name in [privkey_file_name, fullchain_file_name]:
                path = self.proxy_manager.dirs.certs.host / file_name
                path = path.readlink()
                to_remove = Path(self.proxy_manager.dirs.ssl.container)
                relative_path = path.relative_to(to_remove)
                path = self.proxy_manager.dirs.ssl.host / relative_path
                paths.append(path)
            return paths
        except FileNotFoundError:
            raise SSLCertificateNotFoundError(self.certificate.domain)

    def renew_certificate(self):
        if self.needs_renewal():
            self.service.renew_certificate(self.certificate)
            self.proxy_manager.restart()
        else:
            raise SSLCertificateNotDueForRenewalError(self.certificate.domain, self.get_certficate_expiry())

    def get_certficate_expiry(self):
        privkey_path, fullchain_path = self.get_certificate_paths()
        return get_certificate_expiry_date(fullchain_path)

    def needs_renewal(self) -> bool:
        expiry_date_with_minimum_renew_days = self.get_certficate_expiry() - timedelta(days=SSL_RENEW_BEFORE_DAYS)
        today_date = datetime.now()
        if expiry_date_with_minimum_renew_days.tzinfo:
            today_date = today_date.replace(tzinfo=expiry_date_with_minimum_renew_days.tzinfo)

        if not expiry_date_with_minimum_renew_days > today_date:
            return True
        return False

    def __create_certificate_to_domain_link(self, privkey_path: Path, fullchain_path: Path):
        if not self.certificate.ssl_type == SUPPORTED_SSL_TYPES.none:
            create_symlink(self.__get_cert_container_privkey_path(privkey_path), self.get_cert_proxy_privkey_path())
            create_symlink(
                self.__get_cert_container_fullchain_path(fullchain_path), self.get_cert_proxy_fullchain_path()
            )

    def remove_certificate_to_domain_link(self):
        host_cert_proxy_privkey_path = self.get_cert_proxy_privkey_path()
        host_cert_proxy_fullchain_path = self.get_cert_proxy_fullchain_path()

        try:
            host_cert_proxy_fullchain_path.unlink()
            host_cert_proxy_privkey_path.unlink()
        except:
            pass

    def remove_certificate(self):
        self.remove_certificate_to_domain_link()
        self.service.remove_certificate(self.certificate)
        self.proxy_manager.restart()

    def generate_certificate(self):
        privkey_path, fullchain_path = self.service.generate_certificate(self.certificate)
        self.__create_certificate_to_domain_link(privkey_path, fullchain_path)
        self.proxy_manager.restart()

    def get_cert_proxy_fullchain_path(self) -> Path:
        return self.proxy_manager.dirs.certs.host / f"{self.certificate.domain}.crt"

    def get_cert_proxy_privkey_path(self) -> Path:
        return self.proxy_manager.dirs.certs.host / f"{self.certificate.domain}.key"

    def __get_cert_container_privkey_path(self, privkey_path: Path) -> Path:
        part_to_remove = Path(self.proxy_manager.dirs.ssl.host / self.certificate.ssl_type.value)
        relative_path = privkey_path.relative_to(part_to_remove)
        path = self.proxy_manager.dirs.ssl.container / self.certificate.ssl_type.value / relative_path
        return path

    def __get_cert_container_fullchain_path(self, fullchain_path: Path) -> Path:
        part_to_remove = Path(self.proxy_manager.dirs.ssl.host / self.certificate.ssl_type.value)
        relative_path = fullchain_path.relative_to(part_to_remove)
        path = self.proxy_manager.dirs.ssl.container / self.certificate.ssl_type.value / relative_path
        return path
