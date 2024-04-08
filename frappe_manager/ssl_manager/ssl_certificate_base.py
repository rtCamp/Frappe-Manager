from typing import List, Literal, runtime_checkable, Protocol, Union, Optional, Tuple
from pathlib import Path
from dataclasses import dataclass
from certbot.main import main

import shlex

from rich import inspect
from frappe_manager import CLI_DIR
from frappe_manager.services_manager.services import ServicesManager
from frappe_manager.ssl_manager import SUPPORTED_SSL_TYPES
from frappe_manager.ssl_manager.certificate import RenewableSSLCertificate, SSLCertificate
from frappe_manager.ssl_manager.certificate_exceptions import CertificateGenerateFailed, CertificateNotFoundError
from frappe_manager.ssl_manager.nginxproxymanager import NginxProxyManager

from certbot._internal.plugins import disco as plugins_disco
from certbot._internal import cli, storage
from certbot._internal import cert_manager
from certbot._internal.main import make_displayer
from certbot._internal.display import obj as display_obj

from certbot import crypto_util

@runtime_checkable
class SSLCertificateService(Protocol):
    root_dir: Path
    proxymanager: NginxProxyManager

    def is_certificate_exists(self, certificate:SSLCertificate) -> RenewableSSLCertificate:
        ...

    def renew_certificate(self):
        ...

    def remove_certificate(self, certificate: RenewableSSLCertificate) -> bool:
        ...

    def generate_certificate(self, certificate: SSLCertificate) -> RenewableSSLCertificate:
        ...



def ssl_service_factory(services: ServicesManager, ssl_type: SUPPORTED_SSL_TYPES) -> Optional[SSLCertificateService]:
    if ssl_type == SUPPORTED_SSL_TYPES.le:
        proxy_manager = NginxProxyManager(services=services)
        # TODO check if emails exists and provide email
        email = 'alok@gg.com'
        certificate_service = LetsEncryptCertificateService(email=email,proxy_manager=proxy_manager)
        return certificate_service
    return None

class LetsEncryptCertificateService(SSLCertificateService):
    def __init__(
        self, email: str,
        proxy_manager: NginxProxyManager,
        preferred_challenge: Literal[ 'http', 'dns'] = 'http',
        wildcard: bool = False,
        staging: bool = False
    ):
        self.email = email
        self.preferred_challenge = preferred_challenge
        self.wildcard = wildcard
        self.staging = staging
        self.proxymanager: NginxProxyManager = proxy_manager

        # current this is configuring the
        self.webroot_dir: Path = self.proxymanager.dirs.html.host
        self.root_dir = self.proxymanager.dirs.ssl.host / 'le'

        print(self.root_dir)

        # certbot dirs
        self.config_dir: Path = self.root_dir / "config"
        self.work_dir: Path = self.root_dir / 'work'
        self.logs_dir: Path = self.root_dir / 'logs'

        self.base_command = f"--work-dir {self.work_dir} --config-dir {self.config_dir} --logs-dir {self.logs_dir}"

    def renew_certificate(self):
        ...

    def _get_le_config(self, cli_args: Optional[List[str]], quiet: bool = False):
        plugins = plugins_disco.PluginsRegistry.find_all()
        config = cli.prepare_and_parse_args(plugins, cli_args)
        if quiet:
            config.quiet = True
            config.noninteractive_mode = True
            import os
            devnull = open(os.devnull, "w")  # pylint: disable=consider-using-with
            displayer = display_obj.NoninteractiveDisplay(devnull)
            display_obj.set_display(displayer)
        return config

    def is_certificate_exists(self, certificate:SSLCertificate) -> RenewableSSLCertificate:
        config = self._get_le_config(shlex.split(self.base_command))

        # parsed_certs = []
        # parse_failures = []

        for renewal_file in storage.renewal_conf_files(config):
            renewal_candidate = storage.RenewableCert(renewal_file, config)
            crypto_util.verify_renewable_cert(renewal_candidate)

            # parsed_certs.append(renewal_candidate)
            if certificate.domain == renewal_candidate.lineagename:
                alias_domains = cert_manager.domains_for_certname(config,renewal_candidate.lineagename)

                privkey_path = Path(renewal_candidate.key_path)
                fullchain_path = Path(renewal_candidate.fullchain_path)
                expiry = renewal_candidate.target_expiry
                alias_domains = alias_domains if alias_domains else []

                # remove main domain from alias domains
                if len(alias_domains) > 0:
                    try:
                        alias_domains.remove(certificate.domain)
                    except ValueError:
                        pass

                existing_certificate = RenewableSSLCertificate(certificate.domain,alias_domains,privkey_path,fullchain_path,expiry,False)
                return existing_certificate

            # except Exception as e:  # pylint: disable=broad-except
            #     # since the certificate is not valide by crypt_util
            #     we will delete the certificate
            #     # TODO handle this parse faileures cases.
            #     #inspect(parse_failures)
            #     # TODO log myself and Figure OUT
            #     # logger.warning("Renewal configuration file %s produced an "
            #     #             "unexpected error: %s. Skipping.", renewal_file, e)
            #     # logger.debug("Traceback was:\n%s", traceback.format_exc())
            #     parse_failures.append(renewal_file)

        raise CertificateNotFoundError(certificate.domain)

    def remove_certificate(self, certificate: SSLCertificate):
        remove_certificate_command = self.base_command + f' delete --cert-name {certificate.domain}'
        remove_certificate_config = self._get_le_config(shlex.split(remove_certificate_command),quiet=True)
        cert_manager.delete(remove_certificate_config)

    def generate_certificate(self, certificate: SSLCertificate):
        gen_command: str = self.base_command + f" certonly --webroot -w {self.webroot_dir} "

        # basic options
        gen_command += f' --keep-until-expiring'

        # TODO get email from config, if not found then
        # or get email from user and save it site config
        # configure email
        gen_command += ' --staging'
        gen_command += f' --agree-tos -m "{self.email}" --no-eff-email'

        gen_command += ' --quiet -n --expand'

        all_domains = [f'{certificate.domain}'] + certificate.alias_domains

        # alias domains
        for alias_domain in all_domains:
            gen_command += f" -d '{alias_domain}'"

        # TODO check if the certificate exits
        # Add location config

        print(gen_command)

        try:
            for host in all_domains:
                self.proxymanager.add_location_configuration(host,force=True)
            # TODO removing or cleanup of the location configuration will be done by renew command
            # self.proxymanager.remove_all_location_configurations()
            output = main(shlex.split(gen_command))
        except Exception as e:
            raise CertificateGenerateFailed(certificate.domain,exception=e)

        generated_cert = self.is_certificate_exists(certificate)

        return generated_cert

# class SelfSSLCertificateService(SSLCertificateService):
#     def __init__(self):
