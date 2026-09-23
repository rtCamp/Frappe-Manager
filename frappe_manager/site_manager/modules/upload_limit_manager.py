"""
Manages client_max_body_size configuration for nginx-proxy vhost.d files.
"""

import re
from pathlib import Path


class UploadLimitManager:
    """Manages upload limit directives in nginx-proxy vhost.d files."""

    def __init__(self, vhostd_dir: Path):
        """
        Initialize the upload limit manager.

        Args:
            vhostd_dir: Path to nginx-proxy vhost.d directory
        """
        self.vhostd_dir = vhostd_dir

    def set_upload_limit(self, domain: str, size: str):
        """
        Create or update vhost.d file with upload limit directive.

        Args:
            domain: Domain name (e.g., "example.com")
            size: Size in nginx format (e.g., "50m", "1g")
        """
        vhost_file = self.vhostd_dir / domain

        size = size.lower()

        existing_content = ""
        if vhost_file.exists():
            existing_content = vhost_file.read_text()

        if "client_max_body_size" in existing_content:
            updated = re.sub(r"client_max_body_size\s+[^;]+;", f"client_max_body_size {size};", existing_content)
            vhost_file.write_text(updated)
        else:
            new_directive = f"\nclient_max_body_size {size};\n"
            vhost_file.write_text(existing_content + new_directive)

    def set_upload_limit_for_domains(self, domains: list[str], size: str):
        """
        Set upload limit for multiple domains.

        Handles wildcard domains intelligently: if a wildcard exists (e.g., *.example.com),
        subdomains matching that wildcard will NOT get individual files (to avoid nginx duplicates).

        Args:
            domains: List of domain names
            size: Size in nginx format (e.g., "50m", "1g")
        """
        wildcards = [d for d in domains if d.startswith("*.")]
        non_wildcards = [d for d in domains if not d.startswith("*.")]

        for domain in wildcards:
            self.set_upload_limit(domain, size)

        for domain in non_wildcards:
            matches_wildcard = False
            for wildcard in wildcards:
                wildcard_base = wildcard[2:]  # Remove "*."
                if domain.endswith(wildcard_base) and domain != wildcard_base:
                    matches_wildcard = True
                    break

            if not matches_wildcard:
                self.set_upload_limit(domain, size)

    def remove_upload_limit(self, domain: str):
        """
        Remove upload limit directive from vhost.d file.

        Note: Does not delete the file if other directives exist.

        Args:
            domain: Domain name
        """
        vhost_file = self.vhostd_dir / domain

        if not vhost_file.exists():
            return

        content = vhost_file.read_text()

        updated = re.sub(r"client_max_body_size\s+[^;]+;\n?", "", content)

        if not updated.strip():
            vhost_file.unlink()
        else:
            vhost_file.write_text(updated)
