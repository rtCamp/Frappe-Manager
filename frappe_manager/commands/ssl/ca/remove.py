"""`fm ssl ca remove`: stop this host trusting fm's dev CA."""

from typing import Annotated

import typer
from typer_examples import example

from frappe_manager.commands.ssl.ca.helpers import ca_paths
from frappe_manager.output_manager import get_global_output_handler
from frappe_manager.ssl_manager.trust_store_manager import TrustStoreManager


@example(
    "Stop trusting the dev CA",
    "",
)
@example(
    "See which stores would be touched, change nothing",
    "--dry-run",
)
def ca_remove(
    yes: Annotated[
        bool,
        typer.Option("--yes", "-y", help="Remove without asking for confirmation."),
    ] = False,
    dry_run: Annotated[
        bool,
        typer.Option("--dry-run", help="Print the stores that would be touched and exit; never prompts."),
    ] = False,
    delete_ca: Annotated[
        bool,
        typer.Option("--delete-ca", help="Also delete the CA key and certificate from disk."),
    ] = False,
):
    """
    Remove fm's dev CA from every trust store on this host that has it.

    Certificates signed by this CA stop being trusted the moment it is removed, so every dev bench served over https will warn until the CA is installed again. Nothing else is affected: the certificates themselves, the benches and their data are untouched.

    The CA key and certificate stay on disk unless --delete-ca is passed, which is what lets `fm ssl ca install` put the same CA back. Deleting them means the next dev certificate is signed by a NEW CA that every client must be told to trust again.
    """
    output = get_global_output_handler()
    paths = ca_paths()
    manager = TrustStoreManager(output)

    entries = manager.find()

    if not entries:
        output.print("No trust store on this host has fm's dev CA.", emoji_code="")
    else:
        output.warning(f"This will remove fm's dev CA from {len(entries)} trust store(s):")
        for entry in entries:
            suffix = "  (needs sudo)" if entry.privileged else ""
            output.print(f"{entry.store:<28} {entry.location}{suffix}", emoji_code="", prefix="  ")

    if delete_ca:
        for path in (paths.cert, paths.key, paths.sentinel):
            if path.exists():
                output.print(f"delete      {path}", emoji_code="", prefix="  ")

    if dry_run:
        output.print("Nothing was changed.", emoji_code="")
        return

    if not entries and not delete_ca:
        return

    if not yes:
        choice = output.prompt_ask(
            prompt="Proceed? (default: no)",
            choices=["yes", "no"],
            default="no",
            required_flag="--yes",
        )
        if choice != "yes":
            output.print("Aborted; nothing touched.", emoji_code="")
            raise typer.Exit(1)

    removed, failures = manager.uninstall()

    for entry in removed:
        output.print(f"Removed from {entry.store}", emoji_code="")

    # Always clear the sentinel, even when nothing was found: it is the flag that suppresses the
    # automatic install on the next dev certificate, and leaving it set after a removal is what
    # would make fm believe a CA it just deleted is still trusted.
    paths.sentinel.unlink(missing_ok=True)

    if delete_ca:
        paths.cert.unlink(missing_ok=True)
        paths.key.unlink(missing_ok=True)
        output.print("Deleted the CA key and certificate", emoji_code="")

    if failures:
        for failure in failures:
            output.display_error(failure)
        output.display_error("This host still trusts fm's dev CA in the stores listed above.")
        raise typer.Exit(1)
