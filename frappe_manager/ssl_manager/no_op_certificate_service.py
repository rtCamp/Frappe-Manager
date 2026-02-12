from pathlib import Path

from frappe_manager.logger.contextual import ContextualLogger
from frappe_manager.output_manager import OutputHandler
from frappe_manager.output_manager.rich_output import RichOutputHandler
from frappe_manager.ssl_manager.certificate import SSLCertificate
from frappe_manager.ssl_manager.ssl_certificate_service import SSLCertificateService


class NoOpCertificateService(SSLCertificateService):
    def __init__(
        self,
        logger: ContextualLogger,
        root_dir: Path = Path("/dev/null"),
        output_handler: OutputHandler | None = None,
    ):
        self.logger = logger.child(component="noop_ssl")
        self.root_dir = root_dir
        self.output = output_handler or RichOutputHandler()

    def renew_certificate(self, certificate: "SSLCertificate", dry_run: bool = False) -> bool:
        return True

    def remove_certificate(self, certificate: "SSLCertificate") -> bool:
        self.output.warning(f"{certificate.domain} doesn't have certificate issued")
        return False

    def generate_certificate(self, certificate: "SSLCertificate", dry_run: bool = False) -> tuple[Path, Path]:
        return Path("/dev/null"), Path("/dev/null")
