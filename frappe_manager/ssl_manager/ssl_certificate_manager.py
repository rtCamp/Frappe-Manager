from datetime import datetime
from frappe_manager.ssl_manager import SUPPORTED_SSL_TYPES
from frappe_manager.ssl_manager.certificate_exceptions import SSLDNSChallengeNotImplemented
from frappe_manager.ssl_manager.certificate import SSLCertificate
from frappe_manager.ssl_manager.no_op_certificate_service import NoOpCertificateService
from frappe_manager.ssl_manager.renewable_certificate import RenewableSSLCertificate
from frappe_manager.ssl_manager.ssl_certificate_service import SSLCertificateService
from frappe_manager.display_manager.DisplayManager import richprint
from frappe_manager.utils.helpers import create_symlink, format_time_remaining


class SSLCertificateManager:
    service: SSLCertificateService

    def __init__(self, certificate: SSLCertificate, service: SSLCertificateService):

        # Check if the domains and alias domains doesn't contain wildcards
        if certificate.has_wildcard:
            raise SSLDNSChallengeNotImplemented

        self.certificate = certificate
        self.service = service

    def renew_certificate(self, recertificate: RenewableSSLCertificate):
        if self.needs_renewal(recertificate):
            self.service.renew_certificate(recertificate)
        else:
            today_date = datetime.now()
            if recertificate.expiry.tzinfo:
                # Ensure that 'today_date' is timezone-aware if 'certificate.expiry' is
                today_date = today_date.replace(tzinfo=recertificate.expiry.tzinfo)
                time_remaining = recertificate.expiry - today_date
                time_remaining_txt = format_time_remaining(time_remaining)
                richprint.print(f"[yellow]{recertificate.domain}[/yellow] certificate is still valid for {time_remaining_txt}.")


    def needs_renewal(self, certificate: RenewableSSLCertificate)-> bool:
        return self.service.needs_renewal(certificate)


    def is_domain_linked(self, recertificate: RenewableSSLCertificate):
        fullchain_linked = False
        key_linked = False

        if recertificate.proxy_fullchain_path.is_symlink():
            symlink_target = recertificate.proxy_fullchain_path.resolve()
            if recertificate.fullchain_path == symlink_target:
                fullchain_linked = True

        if recertificate.proxy_privkey_path.is_symlink():
            symlink_target = recertificate.proxy_privkey_path.resolve()
            if recertificate.fullchain_path == symlink_target:
                key_linked = True

        return fullchain_linked and key_linked

    def create_certificate_to_domain_link(self, recertificate: RenewableSSLCertificate):
        if not recertificate.ssl_type == SUPPORTED_SSL_TYPES.none:
            create_symlink(recertificate.container_privkey_path,recertificate.proxy_privkey_path)
            create_symlink(recertificate.container_fullchain_path,recertificate.proxy_fullchain_path)

    def remove_certificate_to_domain_link(self, recertificate: RenewableSSLCertificate):
        if recertificate.proxy_privkey_path.is_symlink():
            recertificate.proxy_privkey_path.unlink()

        if recertificate.proxy_fullchain_path.is_symlink():
            recertificate.proxy_fullchain_path.unlink()

    def remove_certificate(self, recertificate: RenewableSSLCertificate):
        self.service.remove_certificate(recertificate)

    def generate_certificate(self):
        new_cert = self.service.generate_certificate(self.certificate)
        self.create_certificate_to_domain_link(new_cert)
        return new_cert
