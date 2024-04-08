from rich import inspect
from datetime import datetime,timedelta
from frappe_manager.ssl_manager.certificate_exceptions import CertificateNotFoundError, DNSMethodNotImplemented
from frappe_manager.ssl_manager.certificate import RenewableSSLCertificate, SSLCertificate
from frappe_manager.ssl_manager.ssl_certificate_base import SSLCertificateService
from frappe_manager.display_manager.DisplayManager import richprint


class CertificateManager:
    certificate: SSLCertificate
    service: SSLCertificateService
    renew_before_days = 30

    def __init__(self,certificate: SSLCertificate, service: SSLCertificateService):

        # Check if the domains and alias domains doesn't contain wildcards
        if certificate.has_wildcard:
            raise DNSMethodNotImplemented

        self.certificate = certificate
        self.service = service

    def _needs_renew(self, certificate: RenewableSSLCertificate)-> bool:

        expiry_date_with_minimum_renew_days = certificate.expiry - timedelta(days=self.renew_before_days)
        today_date = datetime.now()

        if expiry_date_with_minimum_renew_days.tzinfo:
            today_date = today_date.replace(tzinfo=expiry_date_with_minimum_renew_days.tzinfo)

        if not expiry_date_with_minimum_renew_days > today_date:
            return True
        return False

    def get_cert_storage_path(self):
        """
        This function returns search path for both host and container.
        """

        ...
    def add_alias_domain(self):
        ...

    def delete_alias_domain(self):
        ...

    def is_linked(self):
        ...

    def link_to_domain(self, certificate: RenewableSSLCertificate):
        # domain_path =
        ...

    def generate_certificate(self, force = False):
        # checking previous certificate if any
        regenerate_certificate = False

        try:
            existing_certificate: RenewableSSLCertificate = self.service.is_certificate_exists(self.certificate)
            richprint.warning("Previous certificate detected.")

            if force:
                regenerate_certificate = True
                self.service.remove_certificate(existing_certificate)
                richprint.print('Removed Previous Certificate.')
            else:
                if self._needs_renew(existing_certificate):
                    regenerate_certificate = True
                    self.service.remove_certificate(existing_certificate)
                    richprint.print('Removed Previous Certificate.')

                existing_aliases = existing_certificate.alias_domains.sort()
                current_aliases = self.certificate.alias_domains.sort()

                if not existing_aliases == current_aliases:
                    regenerate_certificate = True
                    self.service.remove_certificate(existing_certificate)
                    richprint.print('Removed Previous Certificate.')

        except CertificateNotFoundError:
            regenerate_certificate = True
            pass

        if regenerate_certificate:
            new_cert = self.service.generate_certificate(self.certificate)

        # save certificate
        # get paths of certificate
        # use that to link
