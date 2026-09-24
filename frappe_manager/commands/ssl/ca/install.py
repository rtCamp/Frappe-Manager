"""`fm ssl ca install`: put fm's dev CA back into this host's trust stores."""

import typer
from typer_examples import example

from frappe_manager.commands.ssl.ca.helpers import ca_paths, read_ca
from frappe_manager.output_manager import get_global_output_handler
from frappe_manager.ssl_manager.trust_store_manager import TrustStoreManager


@example(
    "Trust the dev CA on this host",
    "",
)
def ca_install():
    """
    Install fm's dev CA into this host's OS and browser trust stores.

    fm installs the CA by itself the first time it issues a dev certificate, and only once. This command is the way back when that one attempt did not stick or no longer covers everything: a sudo prompt that was declined, a browser profile created afterwards, a restored machine. It is safe to run repeatedly.

    A CA that does not exist yet is not created here: it is minted the first time a bench asks for a dev certificate.
    """
    output = get_global_output_handler()
    paths = ca_paths()

    if read_ca(paths.cert) is None:
        state = "is unreadable" if paths.cert.exists() else "does not exist yet"
        output.display_error(
            f"The dev CA {state} ({paths.cert}). It is created the first time a bench is given a dev certificate."
        )
        raise typer.Exit(1)

    installed = TrustStoreManager(output).install(paths.cert)

    if installed:
        # The sentinel is what stops every later dev certificate from re-running the install. It
        # is written here for the same reason the issuing path writes it: only on success.
        paths.sentinel.touch()
        output.print("Dev CA installed into this host's trust store", emoji_code=":lock:")
    else:
        # The manual steps were already printed by the trust store manager.
        raise typer.Exit(1)
