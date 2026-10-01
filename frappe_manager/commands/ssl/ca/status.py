import shutil
import sys

"""`fm ssl ca status`: which stores trust fm's dev CA, asked of the stores themselves."""

from typer_examples import example

from frappe_manager.commands.arguments import JsonResultOption
from frappe_manager.commands.ssl.ca.helpers import ca_paths, expiry_note, fingerprint, read_ca
from frappe_manager.output_manager import get_global_output_handler, railcard
from frappe_manager.ssl_manager.trust_store_manager import TrustStoreManager


@example(
    "Is fm's dev CA trusted on this host?",
    "",
)
def ca_status(json_results: JsonResultOption = False):
    """
    Show fm's dev CA and every trust store on this host that currently trusts it.

    Each store is asked directly (the keychain, the system CA directories, each browser's NSS database), never the .installed marker fm writes next to the CA: that marker records only that one install once succeeded, not where, and not whether it is still there. A CA fm believes it installed but no store actually holds is the case this command exists to surface, and `fm ssl ca install` is the fix.
    """
    output = get_global_output_handler()
    if json_results:
        output.set_json_results()
    paths = ca_paths()
    cert = read_ca(paths.cert)
    entries = TrustStoreManager(output).find()

    if output.wants_structured_data:
        output.print_data(
            {
                "path": str(paths.cert),
                "exists": cert is not None,
                "subject": cert.subject.rfc4514_string() if cert else None,
                "sha256": fingerprint(cert) if cert else None,
                "private_key_present": paths.key.exists(),
                "trusted_stores": [
                    {"store": e.store, "location": e.location, "fingerprint": e.fingerprint or None} for e in entries
                ],
            }
        )
        return

    # The answer this command exists to settle: a CA that exists but nothing trusts is the
    # failure mode, so `active`/`meta` follow TRUST, not whether the cert file is present.
    trusted = bool(entries)
    meta = f"trusted by {len(entries)} store(s)" if trusted else "not trusted on this host"
    card = railcard.Card("dev ca", meta, active=trusted)

    card.section("certificate")
    if cert is None:
        state = "unreadable" if paths.cert.exists() else "not created yet"
        card.fact("path", str(paths.cert))
        card.fact("status", state)
    else:
        card.fact("path", str(paths.cert))
        card.fact("subject", cert.subject.rfc4514_string())
        card.fact("sha256", fingerprint(cert))
        card.fact("expires", expiry_note(cert))
        card.fact("private key", "present" if paths.key.exists() else "[fm.error]MISSING[/fm.error]")

    card.section("trust")
    local = fingerprint(cert) if cert else None
    if entries:
        for i, entry in enumerate(entries):
            # A store holding a hash that is not the CA on disk means the CA was regenerated and
            # the old one is STILL trusted: a signing key nobody tracks any more, which is the
            # one state worth shouting about.
            stale = (
                "  [fm.warning](a different CA, not the one on disk)[/fm.warning]"
                if local and entry.fingerprint and entry.fingerprint != local
                else ""
            )
            card.fact("stores" if i == 0 else "", f"{entry.store}  [fm.muted]{entry.location}[/fm.muted]{stale}")
    else:
        card.fact("stores", "[fm.muted]none[/fm.muted]")

    output.print_data(card.render())

    if paths.sentinel.exists() and not entries:
        output.warning(
            "fm recorded a successful trust install, but no store has the CA now. "
            "Run 'fm ssl ca install' to put it back."
        )
    if entries and not paths.cert.exists():
        output.warning(
            "The CA is trusted by this host but its certificate file is gone. "
            "Run 'fm ssl ca remove' to stop trusting a CA you no longer hold."
        )

    # Firefox never reads the host trust store, so "trusted" above can be true while Firefox shows
    # an untrusted-certificate page. Reported here because that gap is invisible otherwise: the
    # NSS install skips silently when `certutil` is absent, which is every stock macOS.
    manager = TrustStoreManager(output)
    if manager.nss_profiles_present() and not shutil.which("certutil"):
        hint = "brew install nss" if sys.platform == "darwin" else "install the 'libnss3-tools' package"
        output.warning(
            f"Firefox is installed and keeps its own certificate store, which fm cannot write to "
            f"without 'certutil'. Run '{hint}', then 'fm ssl ca install'."
        )
