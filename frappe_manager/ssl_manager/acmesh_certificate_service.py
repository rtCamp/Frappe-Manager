"""
acme.sh-based SSL certificate service implementation.

Provides certificate issuance, renewal, and management using acme.sh client,
following dependency injection pattern for testability and maintainability.
"""

import os
import subprocess
import shutil
from pathlib import Path
from typing import Tuple, Optional, Dict

from frappe_manager.output_manager import OutputHandler
from frappe_manager.output_manager.rich_output import RichOutputHandler
from frappe_manager.ssl_manager.ssl_certificate_service import SSLCertificateService
from frappe_manager.ssl_manager.certificate import SSLCertificate
from frappe_manager.ssl_manager.letsencrypt_certificate import CustomDomainCertificate, LetsencryptSSLCertificate
from frappe_manager.ssl_manager.certificate_exceptions import (
    SSLCertificateGenerateFailed,
    SSLCertificateNotFoundError,
)
from frappe_manager.utils.subprocess import stream_command_output

LETSENCRYPT_PRODUCTION_SERVER = "https://acme-v02.api.letsencrypt.org/directory"
LETSENCRYPT_STAGING_SERVER = "https://acme-staging-v02.api.letsencrypt.org/directory"


class AcmeShCertificateService:
    """
    SSL certificate service using acme.sh client.

    Supports:
    - HTTP-01 challenge (webroot validation)
    - DNS-01 challenge (Cloudflare API)
    - CNAME delegation for custom domains

    Follows dependency injection pattern with OutputHandler for display operations.
    """

    # Class variable to cache acme.sh installation status
    _acmesh_installed = False

    def __init__(
        self,
        ssl_service_dir: Path,
        webroot_dir: Path,
        acmesh_home: Optional[Path] = None,
        output_handler: OutputHandler | None = None,
    ):
        """
        Initialize acme.sh certificate service.

        Args:
            ssl_service_dir: Base directory for SSL certificates storage
            webroot_dir: Directory for HTTP-01 challenge files (.well-known/acme-challenge)
            acmesh_home: acme.sh installation directory (defaults to ssl_service_dir/acmesh/.acme.sh)
            output_handler: Output handler for display operations (defaults to RichOutputHandler)
        """
        self.webroot_dir = webroot_dir
        self.root_dir = ssl_service_dir / "acmesh"
        # Default acmesh_home to ssl_service_dir/acmesh/.acme.sh for centralized management
        # This keeps all acme.sh files (binary, config, working certs) in FM's ssl directory
        self.acmesh_home = acmesh_home or (ssl_service_dir / "acmesh" / ".acme.sh")
        self.acmesh_bin = self.acmesh_home / "acme.sh"
        self.output = output_handler or RichOutputHandler()

        # Ensure root directory exists
        self.root_dir.mkdir(parents=True, exist_ok=True)

        # Ensure acme.sh is installed
        self._ensure_acmesh_installed()

    def _ensure_acmesh_installed(self):
        """
        Install acme.sh if not present.

        Uses class-level caching to avoid repeated installation checks.
        """
        if AcmeShCertificateService._acmesh_installed and self.acmesh_bin.exists():
            return

        if self.acmesh_bin.exists():
            self.output.debug(f"acme.sh found at {self.acmesh_bin}")
            AcmeShCertificateService._acmesh_installed = True
            return

        self.output.change_head("Installing acme.sh")
        try:
            # Install acme.sh with default noreply email
            # Email notifications discontinued by Let's Encrypt (June 2025)
            install_cmd = f"curl -s https://get.acme.sh | sh -s email=noreply@acme.sh --home {self.acmesh_home}"
            result = subprocess.run(install_cmd, shell=True, check=True, capture_output=True, text=True)

            # Enable logging in account.conf (commented out by default)
            # This ensures all acme.sh operations are logged to acme.sh.log for debugging
            account_conf = self.acmesh_home / "account.conf"
            if account_conf.exists():
                content = account_conf.read_text()
                # Uncomment LOG_FILE and LOG_LEVEL if they are commented
                content = content.replace("#LOG_FILE=", "LOG_FILE=")
                content = content.replace("#LOG_LEVEL=", "LOG_LEVEL=")
                account_conf.write_text(content)
                self.output.debug(f"Enabled logging in {account_conf}")

            self.output.print("acme.sh installed successfully")
            AcmeShCertificateService._acmesh_installed = True
        except subprocess.CalledProcessError as e:
            error_msg = f"Failed to install acme.sh: {e.stderr}"
            self.output.display_error(error_msg)
            raise Exception(error_msg)

    def _run_acmesh_command(self, args: list, env: Optional[Dict[str, str]] = None) -> subprocess.CompletedProcess:
        """
        Run acme.sh command with proper environment.

        Args:
            args: Command arguments (without acme.sh binary path)
            env: Additional environment variables

        Returns:
            CompletedProcess result
        """
        cmd = [str(self.acmesh_bin)] + args
        self.output.debug(f"Running acme.sh: {' '.join(args)}")

        command_env = os.environ.copy()
        command_env["LE_WORKING_DIR"] = str(self.acmesh_home)

        if env:
            command_env.update(env)

        result = subprocess.run(cmd, capture_output=True, text=True, env=command_env)

        if result.returncode != 0:
            self.output.debug(f"acme.sh failed: {result.stderr}")
            # Also show stdout for debug mode
            if result.stdout:
                self.output.debug(f"acme.sh stdout: {result.stdout}")
        else:
            self.output.debug(f"acme.sh succeeded")
            # Show output in debug mode
            if result.stdout:
                self.output.debug(f"acme.sh output: {result.stdout}")

        return result

    def _clear_cached_dns_credentials(self):
        """
        Clear cached DNS credentials from acme.sh account.conf.

        When users update their Cloudflare API credentials in FM config, acme.sh may
        continue using old cached credentials from account.conf, causing authentication
        failures. This method removes cached DNS provider credentials while preserving
        all other acme.sh settings (ACME account configuration, CA preferences, etc.).

        Credentials cleared:
        - SAVED_CF_Token: Cloudflare API Token
        - SAVED_CF_Account_ID: Cloudflare Account ID
        - SAVED_CF_Key: Cloudflare Global API Key (legacy)
        - SAVED_CF_Email: Email for Global API Key auth
        - SAVED_CF_Zone_ID: Zone ID (optional optimization)

        This ensures acme.sh always uses fresh credentials from FM configuration
        instead of potentially stale/revoked cached values.

        Note: This does NOT affect the ACME account key stored in ca/ directory,
        which is required for certificate management and renewals.
        """
        account_conf = self.acmesh_home / "account.conf"
        if not account_conf.exists():
            self.output.debug("account.conf not found, skipping credential cache clear")
            return

        try:
            # Read current configuration
            content = account_conf.read_text()
            lines = content.split('\n')

            # Credential prefixes to remove (acme.sh uses SAVED_ prefix for mutable account config)
            stale_cred_prefixes = (
                "SAVED_CF_Token=",
                "SAVED_CF_Account_ID=",
                "SAVED_CF_Key=",
                "SAVED_CF_Email=",
                "SAVED_CF_Zone_ID=",
            )

            # Filter out credential lines, preserve everything else
            original_count = len(lines)
            filtered_lines = [line for line in lines if not line.startswith(stale_cred_prefixes)]
            removed_count = original_count - len(filtered_lines)

            # Write back filtered configuration
            account_conf.write_text('\n'.join(filtered_lines))

            if removed_count > 0:
                self.output.debug(f"Cleared {removed_count} cached DNS credential line(s) from account.conf")
            else:
                self.output.debug("No cached DNS credentials found in account.conf")

        except Exception as e:
            # Non-fatal: log warning but don't block certificate operations
            # Environment variables will still override cache if present
            self.output.warning(f"Failed to clear cached credentials: {e}")

    def _stream_acmesh_command(
        self, args: list, env: Optional[Dict[str, str]] = None, show_live_output: bool = True
    ) -> int:
        """
        Run acme.sh command with live output streaming from stdout/stderr.

        This method streams stdout/stderr in real-time to the user while the command runs,
        providing feedback during long-running operations (certificate generation, DNS propagation).
        Uses a rolling window display (shows last 5 lines) that updates as new output arrives.
        acme.sh outputs debug info to stderr when --debug flag is used.

        Args:
            args: Command arguments (without acme.sh binary path)
            env: Additional environment variables
            show_live_output: Whether to display live output (default True)

        Returns:
            Exit code of the command
        """
        cmd = [str(self.acmesh_bin)] + args
        self.output.debug(f"Running acme.sh with streaming: {' '.join(args)}")

        # Build environment
        command_env = os.environ.copy()
        command_env["LE_WORKING_DIR"] = str(self.acmesh_home)
        if env:
            command_env.update(env)

        if show_live_output:
            # Use closure to track exit code while streaming
            exit_code_holder = [0]

            def stream_with_exit_tracking():
                """Generator that tracks exit code while yielding output."""
                for source, line in stream_command_output(cmd, env=command_env, cwd=None):
                    if source == "exit_code":
                        exit_code_holder[0] = int(line.decode())
                    yield source, line

            # Display with rolling window (last 5 lines)
            self.output.live_lines(stream_with_exit_tracking(), padding=(0, 0, 0, 2), lines=5)
            return exit_code_holder[0]
        else:
            # No output display - just run and return exit code
            exit_code = 0
            for source, line in stream_command_output(cmd, env=command_env, cwd=None):
                if source == "exit_code":
                    exit_code = int(line.decode())
            return exit_code

    def generate_certificate(self, certificate: SSLCertificate, dry_run: bool = False) -> Tuple[Path, Path]:
        """
        Issue individual certificate using acme.sh.

        Each certificate is issued for a single domain only (no SANs).

        Args:
            certificate: Certificate configuration
            dry_run: If True, skips copying cert files to permanent location (staging only)

        Returns:
            Tuple of (privkey_path, fullchain_path)

        Raises:
            SSLCertificateGenerateFailed: If certificate generation fails
            SSLCertificateNotFoundError: If generated certificate files not found
        """
        self.output.change_head(f"Generating SSL certificate for {certificate.domain}")

        use_staging = os.getenv("FM_LETSENCRYPT_STAGING", "").lower() in ("1", "true", "yes")
        if use_staging:
            self.output.print(
                "[yellow]Using Let's Encrypt STAGING server (test certificates)[/yellow]", emoji_code=":warning:"
            )

        ca_server = LETSENCRYPT_STAGING_SERVER if use_staging else LETSENCRYPT_PRODUCTION_SERVER

        args = [
            "--home",
            str(self.acmesh_home),
            "--issue",
            "-d",
            certificate.domain,
            "--server",
            ca_server,
            "--force",
            "--debug",
            "2",
        ]

        env = {}

        if certificate.challenge_type.value == "http01":
            self.output.info(f"Using HTTP-01 challenge with webroot: {self.webroot_dir}")
            args.extend(["-w", str(self.webroot_dir)])

        elif certificate.challenge_type.value == "dns01":
            self.output.info("Using DNS-01 challenge with Cloudflare")

            self._clear_cached_dns_credentials()

            from frappe_manager.ssl_manager.ssl_utils import get_dns_credentials_for_certificate

            try:
                dns_creds = get_dns_credentials_for_certificate(certificate)
                if dns_creds:
                    env.update(dns_creds)
            except Exception as e:
                self.output.display_error(f"Failed to get DNS credentials: {e}")
                raise

            if isinstance(certificate, CustomDomainCertificate) and certificate.delegation_cname:
                self.output.info(f"Using challenge alias: {certificate.delegation_cname}")
                args.extend(["--dns", "dns_cf", "--challenge-alias", certificate.delegation_cname])
            else:
                args.extend(["--dns", "dns_cf"])

        exit_code = self._stream_acmesh_command(args, env=env, show_live_output=True)

        if exit_code != 0:
            error_msg = f"Certificate generation failed for {certificate.domain}"
            self.output.display_error(error_msg)
            raise SSLCertificateGenerateFailed(certificate.domain)

        cert_dir = self.acmesh_home / certificate.domain
        if not cert_dir.exists():
            cert_dir_ecc = self.acmesh_home / f"{certificate.domain}_ecc"
            if cert_dir_ecc.exists():
                cert_dir = cert_dir_ecc

        key_path = cert_dir / f"{certificate.domain}.key"
        fullchain_path = cert_dir / "fullchain.cer"

        if not fullchain_path.exists() or not key_path.exists():
            error_msg = f"Certificate files not found after generation in {cert_dir}"
            self.output.display_error(error_msg)
            raise SSLCertificateNotFoundError(certificate.domain)

        if dry_run:
            self.output.print(f"[green]Certificate generated successfully (staging) for {certificate.domain}[/green]")
            self.output.print(
                f"[yellow]Skipped: Copying certificate files (dry run)[/yellow]", emoji_code=":fast_forward:"
            )
            return (key_path, fullchain_path)

        dest_dir = self.root_dir / certificate.domain
        dest_dir.mkdir(parents=True, exist_ok=True)

        dest_key = dest_dir / "key.pem"
        dest_fullchain = dest_dir / "fullchain.pem"

        self.output.debug(f"Copying certificates to {dest_dir}")
        shutil.copy(fullchain_path, dest_fullchain)
        shutil.copy(key_path, dest_key)

        self.output.print(f"Certificate generated successfully for {certificate.domain}")
        return (dest_key, dest_fullchain)

    def renew_certificate(self, certificate: SSLCertificate, dry_run: bool = False) -> bool:
        """
        Renew certificate using acme.sh.

        Args:
            certificate: Certificate to renew
            dry_run: If True, skips copying cert files to permanent location (staging only)

        Returns:
            True if renewal succeeded, False otherwise
        """
        self.output.change_head(f"Renewing SSL certificate for {certificate.domain}")

        if certificate.challenge_type.value == "dns01":
            self._clear_cached_dns_credentials()

        use_staging = os.getenv("FM_LETSENCRYPT_STAGING", "").lower() in ("1", "true", "yes")
        if use_staging:
            self.output.print(
                "[yellow]Using Let's Encrypt STAGING server for renewal (test certificates)[/yellow]",
                emoji_code=":warning:",
            )

        ca_server = LETSENCRYPT_STAGING_SERVER if use_staging else LETSENCRYPT_PRODUCTION_SERVER

        args = [
            "--home",
            str(self.acmesh_home),
            "--renew",
            "-d",
            certificate.domain,
            "--server",
            ca_server,
            "--force",
            "--debug",
        ]

        exit_code = self._stream_acmesh_command(args, show_live_output=True)

        if exit_code != 0:
            self.output.display_error(f"Certificate renewal failed for {certificate.domain}")
            return False

        cert_dir = self.acmesh_home / certificate.domain
        if not cert_dir.exists():
            cert_dir_ecc = self.acmesh_home / f"{certificate.domain}_ecc"
            if cert_dir_ecc.exists():
                cert_dir = cert_dir_ecc

        fullchain_path = cert_dir / "fullchain.cer"
        key_path = cert_dir / f"{certificate.domain}.key"

        if fullchain_path.exists() and key_path.exists():
            if dry_run:
                self.output.print(f"[green]Certificate renewed successfully (staging) for {certificate.domain}[/green]")
                self.output.print(
                    f"[yellow]Skipped: Copying certificate files (dry run)[/yellow]", emoji_code=":fast_forward:"
                )
                return True

            dest_dir = self.root_dir / certificate.domain
            dest_dir.mkdir(parents=True, exist_ok=True)

            dest_key = dest_dir / "key.pem"
            dest_fullchain = dest_dir / "fullchain.pem"

            shutil.copy(fullchain_path, dest_fullchain)
            shutil.copy(key_path, dest_key)

            self.output.print(f"Certificate renewed successfully for {certificate.domain}")
            return True

        self.output.display_error(f"Renewed certificate files not found for {certificate.domain}")
        return False

    def remove_certificate(self, certificate: SSLCertificate) -> bool:
        """
        Remove certificate from acme.sh and service directory.

        Args:
            certificate: Certificate to remove

        Returns:
            True if removal succeeded, False otherwise
        """
        self.output.change_head(f"Removing SSL certificate for {certificate.domain}")

        args = ["--remove", "-d", certificate.domain]

        result = self._run_acmesh_command(args)

        # Remove from service directory
        cert_dir = self.root_dir / certificate.domain
        if cert_dir.exists():
            try:
                shutil.rmtree(cert_dir)
                self.output.debug(f"Removed certificate directory: {cert_dir}")
            except Exception as e:
                self.output.display_error(f"Failed to remove directory {cert_dir}: {e}")

        # Also remove acme.sh internal directory (.acme.sh/<domain>_ecc)
        # acme.sh --remove only renames the config to .conf.removed, it doesn't delete the directory
        acmesh_internal_dir = self.acmesh_home / f"{certificate.domain}_ecc"
        if acmesh_internal_dir.exists():
            try:
                shutil.rmtree(acmesh_internal_dir)
                self.output.debug(f"Removed acme.sh internal directory: {acmesh_internal_dir}")
            except Exception as e:
                self.output.warning(f"Failed to remove acme.sh directory {acmesh_internal_dir}: {e}")

        success = result.returncode == 0
        if success:
            self.output.print(f"Certificate removed successfully for {certificate.domain}")
        else:
            self.output.display_error(f"Failed to remove certificate for {certificate.domain}")

        return success
