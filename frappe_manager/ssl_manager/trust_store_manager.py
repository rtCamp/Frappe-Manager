"""
Trust store manager for installing local CA certificates into the host OS trust store.

Handles macOS (login keychain), Linux (system CA store), and Firefox/Chrome NSS databases.
"""

import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

from frappe_manager.output_manager import OutputHandler
from frappe_manager.output_manager.rich_output import RichOutputHandler

# Store labels. Shared by the probes, the removal dispatch and the rendered plan, so a label can
# never drift out of the branch that acts on it.
MACOS_STORE = "macOS login keychain"
ARCH_STORE = "Arch trust anchors"
NSS_STORE = "NSS database (Firefox/Chrome)"


# Where each Linux flavour's CA store keeps fm's anchor. install() writes ONE of these (first
# tool found wins); find/remove check ALL of them, because a host that changed distro tooling,
# or was installed to by an older fm, can hold the anchor in a store the current install() would
# never pick. Removal must be wider than installation or it leaves a trusted CA behind.
LINUX_CA_FILENAME = "fm-dev-ca.crt"
LINUX_CA_STORES: tuple[tuple[Path, str, list[str]], ...] = (
    (Path("/usr/local/share/ca-certificates") / LINUX_CA_FILENAME, "Debian/Ubuntu CA store", ["update-ca-certificates"]),
    (
        Path("/etc/pki/ca-trust/source/anchors") / LINUX_CA_FILENAME,
        "RHEL/Fedora CA store",
        ["update-ca-trust", "extract"],
    ),
)


def display_path(path: Path) -> str:
    """`location` is shown in a terminal next to a padded store label; an absolute home path
    pushes the line past 80 columns and rich then breaks it mid-path."""
    home = str(Path.home())
    text = str(path)
    return f"~{text[len(home) :]}" if text.startswith(home) else text


@dataclass(frozen=True)
class TrustStoreEntry:
    """One place on this host that currently trusts fm's dev CA.

    `location` is for humans; `key` is what removal acts on (a certificate hash, an anchor file,
    an NSS database path). They are separate so the displayed string can be made readable without
    removal having to parse it back.
    """

    store: str
    location: str
    key: str = ""
    privileged: bool = False


class TrustStoreManager:
    """Installs and removes a local CA certificate in the host OS and browser trust stores."""

    CA_NAME = "Frappe Manager Dev CA"

    def __init__(self, output_handler: OutputHandler | None = None):
        self.output = output_handler or RichOutputHandler()

    def install(self, ca_cert_path: Path) -> bool:
        """
        Install the CA into the host OS and browser trust stores. BEST-EFFORT.

        Returns True when the host OS store was updated, False when it could not be.
        Never raises for a missing privilege or tool: issuing a dev certificate must not
        depend on trusting it HERE. A headless server has no browser -- the operator
        trusts the CA on their own machine -- and on Linux the OS store needs sudo, which
        a non-interactive run cannot supply. On failure the exact manual steps are printed.

        Args:
            ca_cert_path: Path to the CA certificate PEM file

        Returns:
            True if the host OS trust store was updated, else False.
        """
        if sys.platform == "darwin":
            installer = self._install_macos
        elif sys.platform.startswith("linux"):
            installer = self._install_linux
        else:
            self.output.warning(f"Unsupported platform '{sys.platform}' for automatic trust store installation.")
            self.output.print(f"Manually trust the CA certificate at: {ca_cert_path}")
            return False

        installed = False
        try:
            installer(ca_cert_path)
            installed = True
        except RuntimeError as e:
            # No sudo on Linux, locked/denied keychain on macOS: the cert is still issued;
            # only the host trust step is skipped, with instructions to finish it by hand.
            self.output.warning(f"Could not install the dev CA into this host's trust store: {e}")
            self._print_manual_instructions(ca_cert_path)

        # Best-effort NSS (Firefox/Chrome on Linux, Firefox on macOS)
        self._install_nss(ca_cert_path)
        return installed

    def _print_manual_instructions(self, ca_cert_path: Path) -> None:
        """Print how to trust the CA by hand -- on this host, and on any other machine
        (a laptop/browser) that will talk to these dev certificates.

        Emitted as ONE soft-wrapped block: a single leading marker, indented command
        lines, and soft_wrap so the terminal reflows long commands instead of Rich
        inserting hard breaks mid-command (which would corrupt a copy-paste).
        """
        if sys.platform == "darwin":
            host_lines = [
                "  this host:",
                f"    security add-trusted-cert -r trustRoot -k ~/Library/Keychains/login.keychain-db {ca_cert_path}",
            ]
        elif sys.platform.startswith("linux"):
            host_lines = [
                "  this host (Debian/Ubuntu):",
                f"    sudo cp {ca_cert_path} /usr/local/share/ca-certificates/fm-dev-ca.crt && sudo update-ca-certificates",
                "  this host (Fedora/RHEL):",
                f"    sudo cp {ca_cert_path} /etc/pki/ca-trust/source/anchors/fm-dev-ca.crt && sudo update-ca-trust extract",
            ]
        else:
            host_lines = [f"  this host: add {ca_cert_path} to the OS trust store"]

        block = "\n".join(
            [
                "The dev certificate was still issued. To make clients trust it, install the CA:",
                *host_lines,
                "  another machine (your browser's): copy the CA file there and add it to that trust store:",
                f"    {ca_cert_path}",
            ]
        )
        self.output.print(block, emoji_code=":page_facing_up:", soft_wrap=True, highlight=False)

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

    def _nss_databases(self) -> list[Path]:
        """Every NSS database on this host fm may have installed into.

        Shared by install, find and remove so a database that install() could reach can never be
        one that remove() silently skips.
        """
        nss_paths: list[Path] = []

        ff_mac = Path.home() / "Library" / "Application Support" / "Firefox" / "Profiles"
        if ff_mac.exists():
            nss_paths.extend(ff_mac.glob("*.default*"))

        ff_linux = Path.home() / ".mozilla" / "firefox"
        if ff_linux.exists():
            nss_paths.extend(ff_linux.glob("*.default*"))

        chrome_nss = Path.home() / ".pki" / "nssdb"
        if chrome_nss.exists():
            nss_paths.append(chrome_nss)

        return nss_paths

    def _install_nss(self, ca_cert_path: Path) -> None:
        """Best-effort installation into NSS databases (Firefox, Chrome on Linux)."""
        certutil = shutil.which("certutil")
        if not certutil:
            self.output.debug("certutil not found, skipping NSS trust store installation")
            return

        nss_paths = self._nss_databases()

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

    # ---- removal -----------------------------------------------------------------------
    #
    # None of this can key off the `.installed` sentinel next to the CA: that file records only
    # that ONE install once succeeded, never where, and it is gone the moment the services dir
    # is deleted -- which is exactly the state a user is in when they want the CA gone. Every
    # probe below asks the store itself.

    def _macos_hashes(self) -> list[str]:
        """SHA-256 hashes of every copy of fm's CA in the login keychain.

        `-a` because a regenerated CA installs a SECOND certificate under the same name, and
        deleting one would leave the other trusted. Delete by hash, not by `-c`: `-c` matches any
        substring of a common name, so it could take a certificate that merely contains ours.
        """
        keychain = Path.home() / "Library" / "Keychains" / "login.keychain-db"
        if not keychain.exists():
            return []

        result = subprocess.run(  # noqa: S603
            ["security", "find-certificate", "-a", "-c", self.CA_NAME, "-Z", str(keychain)],  # noqa: S607
            capture_output=True,
            text=True,
            check=False,
        )
        if result.returncode != 0:
            return []

        return [
            line.split(":", 1)[1].strip()
            for line in result.stdout.splitlines()
            if line.startswith("SHA-256 hash:")
        ]

    def _nss_databases_with_ca(self) -> list[Path]:
        certutil = shutil.which("certutil")
        if not certutil:
            return []

        found = []
        for nss_db in self._nss_databases():
            result = subprocess.run(  # noqa: S603
                [certutil, "-L", "-d", f"sql:{nss_db}", "-n", self.CA_NAME],
                capture_output=True,
                text=True,
                check=False,
            )
            if result.returncode == 0:
                found.append(nss_db)
        return found

    def find(self) -> list[TrustStoreEntry]:
        """Every store on this host that trusts fm's dev CA right now, asked of the stores."""
        entries: list[TrustStoreEntry] = []

        if sys.platform == "darwin":
            keychain = Path.home() / "Library" / "Keychains" / "login.keychain-db"
            for digest in self._macos_hashes():
                entries.append(TrustStoreEntry(store=MACOS_STORE, location=display_path(keychain), key=digest))

        if sys.platform.startswith("linux"):
            for dest, label, _ in LINUX_CA_STORES:
                if dest.exists():
                    entries.append(TrustStoreEntry(store=label, location=str(dest), key=str(dest), privileged=True))
            if self._arch_anchor_present():
                entries.append(
                    TrustStoreEntry(
                        store=ARCH_STORE,
                        location=f"trust anchor '{self.CA_NAME}'",
                        privileged=True,
                    )
                )

        for nss_db in self._nss_databases_with_ca():
            entries.append(TrustStoreEntry(store=NSS_STORE, location=display_path(nss_db), key=str(nss_db)))

        return entries

    def _arch_anchor_present(self) -> bool:
        if not shutil.which("trust"):
            return False
        result = subprocess.run(  # noqa: S603
            ["trust", "list", "--filter=ca-anchors"],  # noqa: S607
            capture_output=True,
            text=True,
            check=False,
        )
        return result.returncode == 0 and self.CA_NAME in result.stdout

    def uninstall(self) -> tuple[list[TrustStoreEntry], list[str]]:
        """Remove fm's dev CA from every store that has it. Returns (removed, failures).

        Best-effort per store, like install: one store refusing (no sudo, a locked keychain)
        must not leave the others trusted. Failures are returned, never raised, so the caller
        can name exactly what is still trusted and exit non-zero.
        """
        removed: list[TrustStoreEntry] = []
        failures: list[str] = []

        for entry in self.find():
            try:
                self._remove_entry(entry)
                removed.append(entry)
            except RuntimeError as e:
                failures.append(f"{entry.store}: {e}")

        return removed, failures

    def _remove_entry(self, entry: TrustStoreEntry) -> None:
        if entry.store == MACOS_STORE:
            self._remove_macos(entry.key)
        elif entry.store == ARCH_STORE:
            self._remove_arch()
        elif entry.store == NSS_STORE:
            self._remove_nss(Path(entry.key))
        else:
            self._remove_linux_file(Path(entry.key))

    def _remove_macos(self, digest: str) -> None:
        keychain = Path.home() / "Library" / "Keychains" / "login.keychain-db"
        result = subprocess.run(  # noqa: S603
            # -t also drops the user trust setting: deleting the certificate alone would leave a
            # dangling "always trust" entry that re-applies if the same CA is ever re-imported.
            ["security", "delete-certificate", "-Z", digest, "-t", str(keychain)],  # noqa: S607
            capture_output=True,
            text=True,
            check=False,
        )
        if result.returncode != 0:
            raise RuntimeError(result.stderr.strip() or f"security delete-certificate exited {result.returncode}")

    def _remove_linux_file(self, dest: Path) -> None:
        refresh = next((cmd for path, _, cmd in LINUX_CA_STORES if path == dest), None)
        if refresh is None:
            raise RuntimeError(f"unknown CA store location {dest}")

        result = subprocess.run(  # noqa: S603
            ["sudo", "rm", "-f", str(dest)],  # noqa: S607
            capture_output=True,
            text=True,
            check=False,
        )
        if result.returncode != 0:
            raise RuntimeError(result.stderr.strip() or "could not remove the anchor file")

        # The file is already gone; a failing refresh leaves the OS bundle still containing the
        # CA, so it is a real failure and must be reported as one.
        result = subprocess.run(["sudo", *refresh], capture_output=True, text=True, check=False)  # noqa: S603
        if result.returncode != 0:
            raise RuntimeError(f"{' '.join(refresh)} failed: {result.stderr.strip()}")

    def _remove_arch(self) -> None:
        result = subprocess.run(  # noqa: S603
            ["sudo", "trust", "anchor", "--remove", self.CA_NAME],  # noqa: S607
            capture_output=True,
            text=True,
            check=False,
        )
        if result.returncode != 0:
            raise RuntimeError(result.stderr.strip() or "trust anchor --remove failed")

    def _remove_nss(self, nss_db: Path) -> None:
        certutil = shutil.which("certutil")
        if not certutil:
            raise RuntimeError("certutil is not installed")

        result = subprocess.run(  # noqa: S603
            [certutil, "-D", "-d", f"sql:{nss_db}", "-n", self.CA_NAME],
            capture_output=True,
            text=True,
            check=False,
        )
        if result.returncode != 0:
            raise RuntimeError(result.stderr.strip() or f"certutil -D exited {result.returncode}")
