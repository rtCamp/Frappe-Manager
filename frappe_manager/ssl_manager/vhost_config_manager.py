"""
Manages nginx-proxy vhost.d configuration files for per-domain HTTPS redirect control.

This module handles creation and removal of vhost.d configuration files that
enable HTTPS redirects only for domains that have SSL certificates.
"""

import re
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

    # The per-domain vhost.d file is SHARED: fm's maintenance block (see
    # frappe_manager/commands/maintenance.py), the proxy-level upload limit and any
    # hand-written directives live in the same jwilder file. So the redirect config owns a
    # MARKED BLOCK inside the file and must never truncate or unlink foreign content.
    BLOCK_BEGIN = "# fm:https-redirect BEGIN"
    BLOCK_END = "# fm:https-redirect END"
    _BLOCK_RE = re.compile(r"^# fm:https-redirect BEGIN.*?^# fm:https-redirect END\n?", re.DOTALL | re.MULTILINE)

    # Default HTTPS redirect configuration
    # Allows internal services (socketio) to make HTTP API calls without redirect,
    # since Node.js fetch() drops Cookie headers on cross-protocol redirects.
    HTTPS_REDIRECT_CONFIG = """# Enable HTTPS redirect for this domain only
# This domain has a valid SSL certificate
# Internal service API calls allowed over HTTP (Cookie header lost on redirect)
set $redirect_to_https 0;
if ($scheme = http) {
    set $redirect_to_https 1;
}
if ($uri ~ ^/api/method/frappe\\.realtime\\.) {
    set $redirect_to_https 0;
}
if ($redirect_to_https = 1) {
    return 301 https://$host$request_uri;
}
"""

    # Bare exact-text fallback for a legacy install with no markers, whose whole file body is just
    # this text (re.escape, since the body contains regex metacharacters like `\\.` and `$`). The
    # optional trailing `\n` is swallowed too, mirroring `_BLOCK_RE`'s own `\n?` after BLOCK_END:
    # without it, removing this text from a legacy file left exactly one "\n" behind, which is
    # truthy, so `disable_https_redirect` kept the file around (empty but present) instead of
    # deleting it -- a plain `.replace()` had no way to know that leftover newline was the
    # constant's own trailing formatting rather than real foreign content.
    _LEGACY_RE = re.compile(re.escape(HTTPS_REDIRECT_CONFIG.strip("\n")) + r"\n?")

    @classmethod
    def _redirect_block(cls) -> str:
        return f"{cls.BLOCK_BEGIN}\n{cls.HTTPS_REDIRECT_CONFIG}{cls.BLOCK_END}\n"

    @classmethod
    def _strip_redirect_block(cls, text: str) -> str:
        stripped = cls._BLOCK_RE.sub("", text)
        # Files written before the markers existed hold the bare config as the whole file body;
        # recognise that legacy shape too so an upgraded install can still turn the redirect off.
        return cls._LEGACY_RE.sub("", stripped)

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
                "Ensure nginx-proxy is running and volumes are mounted correctly.",
            )

    def enable_https_redirect(self, domain: str) -> Path:
        """
        Enable HTTPS redirect for a specific domain.

        Writes fm's own marked redirect block into the domain's vhost.d file, preserving any
        other content already there (maintenance block, upload limit, hand-written directives).
        This should be called after a certificate is successfully generated for the domain.

        The remainder is written back byte-for-byte, with no newline normalisation: whatever
        `disable_https_redirect` strips back out later must be identical to what was here before
        this call, so that add-then-remove is a true inverse, not merely a semantic one.

        Args:
            domain: Domain name to enable HTTPS redirect for

        Returns:
            Path to the created vhost.d config file

        Example:
            >>> manager.enable_https_redirect("example.com")
            Path("/path/to/vhostd/example.com")
        """
        vhost_file = self.vhostd_dir / domain

        # Replace only our own block; everything else in this shared file survives verbatim,
        # byte-for-byte -- no .strip("\n") here, which used to normalise away a foreign file's own
        # leading/trailing newlines and made remove restore something merely equivalent, not
        # identical, to what add found.
        remainder = self._strip_redirect_block(vhost_file.read_text()) if vhost_file.exists() else ""
        vhost_file.write_text(self._redirect_block() + remainder)

        return vhost_file

    def disable_https_redirect(self, domain: str) -> bool:
        """
        Disable HTTPS redirect for a specific domain.

        Strips fm's redirect block from the domain's vhost.d file, leaving foreign content in
        place; the file itself is removed only when nothing else remains. This should be called
        when a certificate is removed.

        The remainder is written back exactly as `_strip_redirect_block` returns it -- no
        `.strip("\\n")` -- so that a file `enable_https_redirect` last touched returns to its
        pre-add bytes exactly, including any leading/trailing blank lines it already had.

        Args:
            domain: Domain name to disable HTTPS redirect for

        Returns:
            True if a redirect config was present and removed, False if there was none

        Example:
            >>> manager.disable_https_redirect("example.com")
            True
        """
        vhost_file = self.vhostd_dir / domain

        if not vhost_file.exists():
            return False

        text = vhost_file.read_text()
        if self.BLOCK_BEGIN not in text and self.HTTPS_REDIRECT_CONFIG.strip("\n") not in text:
            return False

        remainder = self._strip_redirect_block(text)
        if remainder:
            vhost_file.write_text(remainder)
        else:
            vhost_file.unlink()
        return True

    def has_redirect_config(self, domain: str) -> bool:
        """
        Check if a domain has fm's HTTPS redirect configuration.

        The file may exist while holding only foreign content (upload limits, maintenance
        block), so presence of the file alone does not mean the redirect is enabled.

        Args:
            domain: Domain name to check

        Returns:
            True if the redirect config is present for this domain, False otherwise
        """
        vhost_file = self.vhostd_dir / domain
        if not vhost_file.exists():
            return False
        text = vhost_file.read_text()
        return self.BLOCK_BEGIN in text or self.HTTPS_REDIRECT_CONFIG.strip("\n") in text

    def get_config_path(self, domain: str) -> Path:
        """
        Get the path to the vhost.d config file for a domain.

        Args:
            domain: Domain name

        Returns:
            Path to the vhost.d config file (may not exist)
        """
        return self.vhostd_dir / domain
