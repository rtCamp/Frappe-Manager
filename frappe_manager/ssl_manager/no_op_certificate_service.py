from pathlib import Path

from frappe_manager.output_manager import OutputHandler
from frappe_manager.output_manager.rich_output import RichOutputHandler
from frappe_manager.ssl_manager.certificate import SSLCertificate
from frappe_manager.ssl_manager.ssl_certificate_service import SSLCertificateService


class NoOpCertificateService(SSLCertificateService):
    def __init__(self, root_dir: Path = Path("/dev/null"), output_handler: OutputHandler | None = None):
        self.root_dir = root_dir
        self.output = output_handler or RichOutputHandler()

    def renew_certificate(self):
        pass

    def remove_certificate(self, certificate: "SSLCertificate"):
        self.output.warning(f"{certificate.domain} doesn't have certificate issued")

    def generate_certificate(
        self, certificate: "SSLCertificate", alias_domains: list[str] | None = None,
    ) -> tuple[Path, Path]:
        return Path("/dev/null"), Path("/dev/null")
