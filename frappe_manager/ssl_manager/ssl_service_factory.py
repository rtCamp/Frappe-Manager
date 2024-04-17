from logging import root
from typing import Union
from pathlib import Path
from frappe_manager.compose_project.compose_project import ComposeProject
from frappe_manager.ssl_manager import SUPPORTED_SSL_TYPES
from frappe_manager.ssl_manager.certificate import SSLCertificate
from frappe_manager.ssl_manager.letsencrypt_certificate import LetsencryptSSLCertificate
from frappe_manager.ssl_manager.letsencrypt_certificate_service import LetsEncryptCertificateService
from frappe_manager.ssl_manager.nginxproxymanager import NginxProxyManager
from frappe_manager.ssl_manager.no_op_certificate_service import NoOpCertificateService
from frappe_manager.ssl_manager.renewable_certificate import RenewableLetsencryptSSLCertificate
from frappe_manager.ssl_manager.ssl_certificate_service import SSLCertificateService


def ssl_service_factory(proxy_service_name: str, services_compose_project: ComposeProject,webroot_dir: Path, certificate: Union[SSLCertificate,LetsencryptSSLCertificate, RenewableLetsencryptSSLCertificate]) -> SSLCertificateService:
    if certificate.ssl_type == SUPPORTED_SSL_TYPES.le:
        proxy_manager = NginxProxyManager(proxy_service_name,services_compose_project)
        webroot_dir = webroot_dir
        certificate_service = LetsEncryptCertificateService(proxy_manager,webroot_dir)
        return certificate_service
    elif certificate.ssl_type == SUPPORTED_SSL_TYPES.none:
        certificate_service = NoOpCertificateService(Path('/dev/null'))
        return certificate_service
