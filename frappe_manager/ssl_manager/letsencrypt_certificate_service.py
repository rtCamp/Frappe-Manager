import shlex
from io import StringIO
from pathlib import Path

from certbot import crypto_util
from certbot._internal import cli, storage
from certbot._internal.display import obj as display_obj
from certbot._internal.main import make_or_verify_needed_dirs
from certbot._internal.plugins import disco as plugins_disco
from certbot.errors import AuthorizationError

from frappe_manager.logger import log
from frappe_manager.output_manager import OutputHandler
from frappe_manager.output_manager.rich_output import RichOutputHandler
from frappe_manager.ssl_manager import LETSENCRYPT_PREFERRED_CHALLENGE, SUPPORTED_SSL_TYPES
from frappe_manager.ssl_manager.certificate_exceptions import (
    SSLCertificateChallengeFailed,
    SSLCertificateGenerateFailed,
    SSLCertificateNotFoundError,
)
from frappe_manager.ssl_manager.letsencrypt_certificate import LetsencryptSSLCertificate
from frappe_manager.ssl_manager.ssl_certificate_service import SSLCertificateService


class LetsEncryptCertificateService(SSLCertificateService):
    def __init__(
        self,
        ssl_service_dir: Path,
        webroot_dir: Path,
        output_handler: OutputHandler | None = None,
    ):
        self.webroot_dir = webroot_dir
        self.root_dir = ssl_service_dir / SUPPORTED_SSL_TYPES.le.value

        # certbot dirs
        self.config_dir: Path = self.root_dir / "config"
        self.work_dir: Path = self.root_dir / "work"
        self.logs_dir: Path = self.root_dir / "logs"
        self.dns_config_dir = self.root_dir / "dns_configs"

        self.base_command = f"--work-dir {self.work_dir} --config-dir {self.config_dir} --logs-dir {self.logs_dir}"
        self.logger = log.get_logger()
        self.console_output = StringIO()
        self.output = output_handler or RichOutputHandler()

    def renew_certificate(self, certificate: LetsencryptSSLCertificate):
        renew_certificate_command = (
            self.base_command + f" renew --cert-name {certificate.domain} --non-interactive --force-renewal"
        )

        # Check for staging environment variable
        import os

        if os.getenv("FM_LETSENCRYPT_STAGING", "").lower() in ("1", "true", "yes"):
            renew_certificate_command += " --test-cert"
            self.output.print("[yellow]⚠️  Using Let's Encrypt STAGING server for renewal (test certificates)[/yellow]")

        renew_certificate_config = self._get_le_config(shlex.split(renew_certificate_command), quiet=True)
        make_or_verify_needed_dirs(renew_certificate_config)
        plugins = plugins_disco.PluginsRegistry.find_all()
        renew_certificate_config.func(renew_certificate_config, plugins)

    def _get_le_config(self, cli_args: list[str], quiet: bool = False):
        plugins = plugins_disco.PluginsRegistry.find_all()

        config = cli.prepare_and_parse_args(plugins, cli_args)
        if quiet:
            config.quiet = True
            config.noninteractive_mode = True
            displayer = display_obj.NoninteractiveDisplay(outfile=self.console_output)
            display_obj.set_display(displayer)
        return config

    def get_certificate_paths(self, certificate: LetsencryptSSLCertificate) -> tuple[Path, Path]:
        config = self._get_le_config(shlex.split(self.base_command), quiet=True)
        for renewal_file in storage.renewal_conf_files(config):
            renewal_candidate = storage.RenewableCert(renewal_file, config)
            crypto_util.verify_renewable_cert(renewal_candidate)

            # parsed_certs.append(renewal_candidate)
            if certificate.domain == renewal_candidate.lineagename:
                privkey_path = Path(renewal_candidate.key_path)
                fullchain_path = Path(renewal_candidate.fullchain_path)
                return privkey_path, fullchain_path
        raise SSLCertificateNotFoundError(certificate.domain)

    def remove_certificate(self, certificate: LetsencryptSSLCertificate):
        self.output.change_head("Removing Letsencrypt certificate")
        remove_certificate_command = self.base_command + f" delete --cert-name {certificate.domain}"
        remove_certificate_config = self._get_le_config(shlex.split(remove_certificate_command), quiet=True)
        plugins = plugins_disco.PluginsRegistry.find_all()
        remove_certificate_config.func(remove_certificate_config, plugins)
        self.output.print("Removed Letsencrypt certificate")

    def generate_certificate(self, certificate: LetsencryptSSLCertificate):
        """
        Generate individual certificate for a single domain using certbot.

        Each certificate is issued for a single domain only (no SANs).

        Args:
            certificate: Certificate configuration
        """
        gen_command: str = self.base_command + " certonly "

        self.output.print(f"Using Let's Encrypt {certificate.preferred_challenge.value} challenge.")

        dns_config_path = self.dns_config_dir / f"{certificate.domain}.txt"

        if certificate.preferred_challenge == LETSENCRYPT_PREFERRED_CHALLENGE.http01:
            gen_command += f" --webroot -w {self.webroot_dir}"

        elif certificate.preferred_challenge == LETSENCRYPT_PREFERRED_CHALLENGE.dns01:
            self.dns_config_dir.mkdir(parents=True, exist_ok=True)

            api_creds = certificate.get_cloudflare_dns_credentials(output_handler=self.output)
            dns_config_path.write_text(api_creds)
            dns_config_path.chmod(0o600)

            gen_command += f" --dns-cloudflare --dns-cloudflare-credentials {dns_config_path.absolute()}"

        gen_command += " --keep-until-expiring --expand"
        gen_command += f' --agree-tos -m "{certificate.email}" --no-eff-email'

        # Check for staging environment variable
        import os

        if os.getenv("FM_LETSENCRYPT_STAGING", "").lower() in ("1", "true", "yes"):
            gen_command += " --test-cert"
            self.output.print("[yellow]⚠️  Using Let's Encrypt STAGING server (test certificates)[/yellow]")

        # Generate certificate for single domain only
        gen_command += f" -d '{certificate.domain}'"

        try:
            self.output.change_head("Getting Letsencrypt certificate")
            self.logger.debug(f"Certbot command: {gen_command}")
            config = self._get_le_config(shlex.split(gen_command), quiet=True)
            plugins = plugins_disco.PluginsRegistry.find_all()
            config.func(config, plugins)
            output = "\n".join(line for line in self.console_output.getvalue().split("\n") if not line.startswith("!!"))
            self.output.print(output)

        except AuthorizationError as e:
            self.logger.exception(e)
            output = "\n".join(line for line in self.console_output.getvalue().split("\n") if not line.startswith("!!"))
            self.output.print(output)
            if certificate.preferred_challenge == LETSENCRYPT_PREFERRED_CHALLENGE.dns01 and dns_config_path.exists():
                dns_config_path.unlink()
            raise SSLCertificateChallengeFailed(certificate.preferred_challenge)

        except Exception as e:
            self.logger.exception(e)
            output = "\n".join(line for line in self.console_output.getvalue().split("\n") if not line.startswith("!!"))
            self.output.print(output)
            if certificate.preferred_challenge == LETSENCRYPT_PREFERRED_CHALLENGE.dns01 and dns_config_path.exists():
                dns_config_path.unlink()
            raise SSLCertificateGenerateFailed()

        self.output.print("Acquired Letsencrypt certificate: Done")
        return self.get_certificate_paths(certificate)
