"""
Trust store manager for installing local CA certificates into the host OS trust store.

Handles macOS (login keychain), Linux (system CA store), and Firefox/Chrome NSS databases.
"""

import shutil
import subprocess
import sys
from pathlib import Path

from frappe_manager.output_manager import OutputHandler
from frappe_manager.output_manager.rich_output import RichOutputHandler


class TrustStoreManager:
    """Installs a local CA certificate into the host OS and browser trust stores."""

    CA_NAME = "Frappe Manager Dev CA"

    def __init__(self, output_handler: OutputHandler | None = None):
        self.output = output_handler or RichOutputHandler()

    def install(self, ca_cert_path: Path) -> None:
        """
        Install CA certificate into all available trust stores.

        Args:
            ca_cert_path: Path to the CA certificate PEM file

        Raises:
            RuntimeError: If primary trust store installation fails
        """
        if sys.platform == "darwin":
            self._install_macos(ca_cert_path)
        elif sys.platform.startswith("linux"):
            self._install_linux(ca_cert_path)
        else:
            self.output.warning(f"Unsupported platform '{sys.platform}' for automatic trust store installation.")
            self.output.print(f"Manually trust the CA certificate at: {ca_cert_path}")
            return

        # Best-effort NSS (Firefox/Chrome on Linux, Firefox on macOS)
        self._install_nss(ca_cert_path)

    def _install_macos(self, ca_cert_path: Path) -> None:
        """Install into macOS login keychain (current user, no sudo required)."""
        login_keychain = Path.home() / "Library" / "Keychains" / "login.keychain-db"
        self.output.debug(f"Installing CA into macOS login keychain: {login_keychain}")

        result = subprocess.run(  # noqa: S603
            [  # noqa: S607
                "security",
                "add-trusted-cert",
                "-r",
                "trustRoot",
                "-k",
                str(login_keychain),
                str(ca_cert_path),
            ],
            capture_output=True,
            text=True,
            check=False,
        )

        if result.returncode != 0:
            # Exit code 36 = user denied / keychain locked
            if result.returncode == 36:
                raise RuntimeError(
                    "macOS denied trust store access. Unlock your login keychain in Keychain Access and retry."
                )
            raise RuntimeError(f"Failed to install CA into macOS keychain (exit {result.returncode}): {result.stderr}")

        self.output.debug("CA installed into macOS login keychain")

    def _install_linux(self, ca_cert_path: Path) -> None:
        """Install into Linux system CA store."""
        if shutil.which("update-ca-certificates"):
            # Debian / Ubuntu
            dest = Path("/usr/local/share/ca-certificates/fm-dev-ca.crt")
            self.output.debug(f"Installing CA to {dest} (Debian/Ubuntu)")
            result = subprocess.run(  # noqa: S603
                ["sudo", "cp", str(ca_cert_path), str(dest)],  # noqa: S607
                capture_output=True,
                text=True,
                check=False,
            )
            if result.returncode != 0:
                raise RuntimeError(f"Failed to copy CA cert: {result.stderr}")
            result = subprocess.run(
                ["sudo", "update-ca-certificates"],  # noqa: S607
                capture_output=True,
                text=True,
                check=False,
            )
            if result.returncode != 0:
                raise RuntimeError(f"update-ca-certificates failed: {result.stderr}")

        elif shutil.which("update-ca-trust"):
            # RHEL / Fedora / CentOS
            dest = Path("/etc/pki/ca-trust/source/anchors/fm-dev-ca.crt")
            self.output.debug(f"Installing CA to {dest} (RHEL/Fedora)")
            result = subprocess.run(  # noqa: S603
                ["sudo", "cp", str(ca_cert_path), str(dest)],  # noqa: S607
                capture_output=True,
                text=True,
                check=False,
            )
            if result.returncode != 0:
                raise RuntimeError(f"Failed to copy CA cert: {result.stderr}")
            result = subprocess.run(
                ["sudo", "update-ca-trust", "extract"],  # noqa: S607
                capture_output=True,
                text=True,
                check=False,
            )
            if result.returncode != 0:
                raise RuntimeError(f"update-ca-trust failed: {result.stderr}")

        elif shutil.which("trust"):
            # Arch Linux
            self.output.debug("Installing CA via trust anchor (Arch)")
            result = subprocess.run(  # noqa: S603
                ["sudo", "trust", "anchor", "--store", str(ca_cert_path)],  # noqa: S607
                capture_output=True,
                text=True,
                check=False,
            )
            if result.returncode != 0:
                raise RuntimeError(f"trust anchor failed: {result.stderr}")

        else:
            raise RuntimeError(
                "No supported CA trust update tool found. Install libnss3-tools (Debian/Ubuntu) or nss-tools (Fedora)."
            )

        self.output.debug("CA installed into Linux system trust store")

    def _install_nss(self, ca_cert_path: Path) -> None:
        """Best-effort installation into NSS databases (Firefox, Chrome on Linux)."""
        certutil = shutil.which("certutil")
        if not certutil:
            self.output.debug("certutil not found, skipping NSS trust store installation")
            return

        nss_paths: list[Path] = []

        # Firefox profiles — macOS
        ff_mac = Path.home() / "Library" / "Application Support" / "Firefox" / "Profiles"
        if ff_mac.exists():
            nss_paths.extend(ff_mac.glob("*.default*"))

        # Firefox profiles — Linux
        ff_linux = Path.home() / ".mozilla" / "firefox"
        if ff_linux.exists():
            nss_paths.extend(ff_linux.glob("*.default*"))

        # Chrome/Chromium NSS DB — Linux
        chrome_nss = Path.home() / ".pki" / "nssdb"
        if chrome_nss.exists():
            nss_paths.append(chrome_nss)

        for nss_db in nss_paths:
            result = subprocess.run(  # noqa: S603
                [
                    certutil,
                    "-A",
                    "-d",
                    f"sql:{nss_db}",
                    "-t",
                    "C,,",
                    "-n",
                    self.CA_NAME,
                    "-i",
                    str(ca_cert_path),
                ],
                capture_output=True,
                text=True,
                check=False,
            )
            if result.returncode == 0:
                self.output.debug(f"CA installed into NSS database: {nss_db}")
            else:
                self.output.debug(f"NSS install skipped for {nss_db}: {result.stderr.strip()}")
