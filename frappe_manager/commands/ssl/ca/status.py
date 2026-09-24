"""`fm ssl ca status`: which stores trust fm's dev CA, asked of the stores themselves."""

from typer_examples import example

from frappe_manager.commands.ssl.ca.helpers import ca_paths, expiry_note, fingerprint, read_ca
from frappe_manager.output_manager import get_global_output_handler
from frappe_manager.ssl_manager.trust_store_manager import TrustStoreManager


@example(
    "Is fm's dev CA trusted on this host?",
    "",
)
def ca_status():
    """
    Show fm's dev CA and every trust store on this host that currently trusts it.

    Each store is asked directly (the keychain, the system CA directories, each browser's NSS database), never the .installed marker fm writes next to the CA: that marker records only that one install once succeeded, not where, and not whether it is still there. A CA fm believes it installed but no store actually holds is the case this command exists to surface, and `fm ssl ca install` is the fix.
    """
    output = get_global_output_handler()
    paths = ca_paths()
    cert = read_ca(paths.cert)

    if cert is None:
        state = "unreadable" if paths.cert.exists() else "not created yet"
        output.print(f"CA       : {state}  ({paths.cert})", emoji_code="")
    else:
        output.print(f"CA       : {paths.cert}", emoji_code="")
        output.print(f"subject     {cert.subject.rfc4514_string()}", emoji_code="", prefix="  ")
        output.print(f"sha256      {fingerprint(cert)}", emoji_code="", prefix="  ")
        output.print(f"expires     {expiry_note(cert)}", emoji_code="", prefix="  ")
        output.print(f"private key {'present' if paths.key.exists() else 'MISSING'}", emoji_code="", prefix="  ")


    entries = TrustStoreManager(output).find()
    local = fingerprint(cert) if cert else None
    if entries:
        output.print(f"Trusted  : {len(entries)} store(s)", emoji_code="")
        for entry in entries:
            # A store holding a hash that is not the CA on disk means the CA was regenerated and
            # the old one is STILL trusted: a signing key nobody tracks any more, which is the
            # one state worth shouting about.
            stale = "  (a different CA, not the one on disk)" if local and entry.key and entry.key != local else ""
            output.print(f"{entry.store:<28} {entry.location}{stale}", emoji_code="", prefix="  ")
    else:
        output.print("Trusted  : no store on this host trusts it", emoji_code="")

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
