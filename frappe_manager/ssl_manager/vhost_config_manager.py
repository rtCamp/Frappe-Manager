"""
Manages nginx-proxy vhost.d configuration files for per-domain HTTPS redirect control.

This module handles creation and removal of vhost.d configuration files that
enable HTTPS redirects only for domains that have SSL certificates.
"""

from pathlib import Path


class VhostConfigManager:
    """
    Manages per-domain nginx-proxy vhost.d configuration files.

    This class creates and manages vhost.d files that control HTTPS redirect
    behavior on a per-domain basis. When a domain has a certificate, we create
    a vhost.d file to enable HTTP→HTTPS redirects for that domain only.

    This solves the nginx-proxy behavior where ALL domains in VIRTUAL_HOST get
    redirected to HTTPS when ANY certificate exists.

    Attributes:
        vhostd_dir: Path to the nginx-proxy vhost.d directory (host filesystem)
    """

    # Default HTTPS redirect configuration
    HTTPS_REDIRECT_CONFIG = """# Enable HTTPS redirect for this domain only
# This domain has a valid SSL certificate
if ($scheme = http) {
    return 301 https://$host$request_uri;
}
"""

    def __init__(self, vhostd_dir: Path):
        """
        Initialize the vhost config manager.

        Args:
            vhostd_dir: Path to the nginx-proxy vhost.d directory

        Raises:
            ValueError: If vhostd_dir doesn't exist
        """
        self.vhostd_dir = vhostd_dir

        if not self.vhostd_dir.exists():
            raise ValueError(
                f"vhost.d directory does not exist: {self.vhostd_dir}. "
                "Ensure nginx-proxy is running and volumes are mounted correctly."
            )

    def enable_https_redirect(self, domain: str) -> Path:
        """
        Enable HTTPS redirect for a specific domain.

        Creates a vhost.d configuration file that redirects HTTP traffic to HTTPS
        for this domain only. This should be called after a certificate is successfully
        generated for the domain.

        Args:
            domain: Domain name to enable HTTPS redirect for

        Returns:
            Path to the created vhost.d config file

        Example:
            >>> manager.enable_https_redirect("example.com")
            Path("/path/to/vhostd/example.com")
        """
        vhost_file = self.vhostd_dir / domain

        # Write the redirect configuration
        vhost_file.write_text(self.HTTPS_REDIRECT_CONFIG)

        return vhost_file

    def disable_https_redirect(self, domain: str) -> bool:
        """
        Disable HTTPS redirect for a specific domain.

        Removes the vhost.d configuration file for this domain, allowing it to
        serve HTTP traffic without redirecting to HTTPS. This should be called
        when a certificate is removed.

        Args:
            domain: Domain name to disable HTTPS redirect for

        Returns:
            True if the file was removed, False if it didn't exist

        Example:
            >>> manager.disable_https_redirect("example.com")
            True
        """
        vhost_file = self.vhostd_dir / domain

        if vhost_file.exists():
            vhost_file.unlink()
            return True
        return False

    def has_redirect_config(self, domain: str) -> bool:
        """
        Check if a domain has a redirect configuration file.

        Args:
            domain: Domain name to check

        Returns:
            True if vhost.d file exists for this domain, False otherwise
        """
        vhost_file = self.vhostd_dir / domain
        return vhost_file.exists()

    def get_config_path(self, domain: str) -> Path:
        """
        Get the path to the vhost.d config file for a domain.

        Args:
            domain: Domain name

        Returns:
            Path to the vhost.d config file (may not exist)
        """
        return self.vhostd_dir / domain
