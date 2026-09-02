from typing import Annotated

import typer
from typer_examples import example

from frappe_manager import CLI_BENCHES_DIRECTORY
from frappe_manager.commands.arguments import BenchSiteArgument
from frappe_manager.output_manager import get_global_output_handler
from frappe_manager.site_manager.bench_service import BenchService


def _plural(count: int, noun: str) -> str:
    """`1 site` / `2 sites`. The counts are read out loud in a blast radius, so they must agree."""
    return f"{count} {noun}" if count == 1 else f"{count} {noun}s"


def _blast_radius(schemas) -> list[str]:
    """The rows shown before a bench serving several sites is destroyed.

    Built from `Bench.site_schemas()`, which reads each site's `site_config.json` off disk rather
    than the `[sites]` table: the bench config is missing or stale exactly when a delete is needed,
    and a blast radius that under-reports is worse than none.

    `SiteSchema.droppable` and `SiteSchema.unreadable` partition the sites exactly, so every site
    is reported once. A droppable schema is in the global-db container fm owns and is dropped. An
    unreadable one is neither dropped nor deliberately left: fm cannot drop a name it does not know
    and cannot promise it is gone, and that is the case that orphans a schema, so it is reported as
    itself. Everything else has a schema on a server fm does not own, which is named and left alone.
    """
    rows: list[tuple[str, str]] = [(_plural(len(schemas), "site"), ", ".join(s.site for s in schemas))]

    dropped = [s.schema for s in schemas if s.droppable]
    if dropped:
        rows.append((f"{_plural(len(dropped), 'schema')} dropped", f"{', '.join(dropped)}  (global-db)"))

    kept = [f"{s.schema} on {s.external_host}" for s in schemas if not s.droppable and not s.unreadable]
    if kept:
        rows.append((f"{_plural(len(kept), 'schema')} kept", f"{', '.join(kept)}  (external, not fm's)"))

    unreadable = [s.site for s in schemas if s.unreadable]
    if unreadable:
        rows.append(
            (
                f"{_plural(len(unreadable), 'schema')} unreadable",
                f"{', '.join(unreadable)}  (no readable site_config.json, a schema may be left behind)",
            )
        )

    width = max(len(label) for label, _ in rows)
    lines = [f"  {label.ljust(width)} {value}" for label, value in rows]
    lines.append("  containers, workspace, certificates")
    return lines


def _confirm_bench_name(output, benchname: str, schemas) -> None:
    """Show what dies, then require the bench name typed back. Anything else removes nothing.

    The guard for a bench serving several sites, and only for that: one typed word would otherwise
    destroy several separately named things, so the address stops being the acknowledgement. A
    single-site bench and a `BENCH/SITE` address both destroy exactly what was typed, and keep the
    yes/no question they have always asked.
    """
    output.warning(f"This will permanently delete bench '{benchname}':")

    for line in _blast_radius(schemas):
        output.print(line, emoji_code="")

    typed = output.prompt_ask(prompt="Type the bench name to confirm", required_flag="--yes or -y")

    if typed.strip() != benchname:
        # The typed value is deliberately not echoed: it goes through rich markup, where a stray
        # bracket in a typo would render as nothing and make the refusal look like it lost the input.
        output.print("Cancelled: that is not the bench name. Nothing was removed.", emoji_code=":x:")
        raise typer.Exit(1)


def _site_schemas(bench_service: BenchService, benchname: str) -> list:
    """Every site the bench has on disk, or nothing when the bench itself cannot be loaded.

    An unloadable config is the cleanup case `BenchService.delete_bench` exists to serve, and it has
    to stay deletable. Enumerating nothing means the multi-site guards below do not fire and a broken
    bench deletes exactly as it did before a bench could hold several sites.
    """
    try:
        bench = bench_service.get_bench(benchname, workers_check=False, admin_tools_check=False)
    except FileNotFoundError:
        return []
    return bench.site_schemas()


@example(
    "Delete a bench and its database",
    "{benchname} --delete-db-from-global-db",
    benchname="mybench",
)
@example(
    "Delete one site out of a bench",
    "{benchname}/a.example.com",
    detail="Only that site is removed. The bench and its other sites keep running, so no --all-sites is needed: the address already names exactly one site.",
    benchname="mybench",
)
@example(
    "Delete a bench that serves several sites",
    "{benchname} --all-sites",
    detail="fm lists every site it is about to destroy, then asks for the bench name typed back.",
    benchname="mybench",
)
@example(
    "Delete the bench but keep the database",
    "{benchname} --no-delete-db-from-global-db",
    detail="The bench is gone; the schema stays in global-db.",
    benchname="mybench",
)
@example(
    "Delete unattended",
    "{benchname} --yes --delete-db-from-global-db",
    benchname="mybench",
)
@example(
    "Delete a multi-site bench unattended",
    "{benchname} --all-sites --yes --delete-db-from-global-db",
    detail="--yes skips the confirmation; --all-sites is still required, so no script deletes more than it named.",
    benchname="mybench",
)
def delete(
    ctx: typer.Context,
    address: BenchSiteArgument = None,
    all_sites: Annotated[
        bool,
        typer.Option(
            "--all-sites",
            help="Required to delete a bench that serves more than one site, and it means every one of them. A single-site bench does not need it, and a bench/site address refuses it because that address already names exactly one site.",
        ),
    ] = False,
    yes: Annotated[
        bool,
        typer.Option(
            "--yes",
            "-y",
            help="Delete without the removal confirmation, including the typed-name confirmation a multi-site bench asks for. The database question is asked anyway, and --all-sites is still required.",
        ),
    ] = False,
    delete_db_from_global_db: Annotated[
        bool | None,
        typer.Option(
            "--delete-db-from-global-db/--no-delete-db-from-global-db",
            help="Drop the schema and user from the global-db container, or keep them. Applies to every site being deleted that is on the global-db container, and never touches a database on an external server. fm asks when neither is passed.",
        ),
    ] = None,
):
    """
    Delete a whole bench, or one site out of one.

    BENCH deletes the bench: every site in it, its containers and volumes, its whole directory, and its TLS certificates. A bench serving more than one site also needs --all-sites and asks for its name typed back, because one word would otherwise destroy several separately named sites.

    BENCH/SITE deletes just that site: its schema, its certificate, its proxy entries and its files. The bench and its other sites keep running.

    The database is decided separately. fm can drop a site's schema and user from the global-db container it owns, but a schema on a server fm does not own is always left in place, --delete-db-from-global-db or not.
    """

    if not address:
        return

    output = get_global_output_handler()

    # The site half of a `BENCH/SITE` address arrives on the context, put there by
    # `bench_site_callback`, so the body keeps receiving a plain bench-directory name.
    site = (ctx.obj or {}).get("site")

    if site and all_sites:
        output.display_error(
            f"--all-sites cannot be combined with the address '{address}/{site}', which already names exactly one site. Drop --all-sites to delete that site, or drop the '/{site}' to delete the whole bench."
        )
        raise typer.Exit(1)

    services_manager = ctx.obj["services"]
    verbose = ctx.obj["verbose"]

    bench_service = BenchService(CLI_BENCHES_DIRECTORY, services_manager, verbose=verbose, output_handler=output)

    if site:
        bench = bench_service.get_bench(address, workers_check=False, admin_tools_check=False)

        if not yes:
            # The bench is loaded first, so a name that resolves to nothing fails as "not found"
            # rather than offering to destroy whatever it did find.
            output.warning(
                f"Removing the site '{site}' from bench '{address}' drops its schema when the schema is fm's to drop, removes its certificate and deletes its files. The bench and its other sites keep running."
            )
            choice = output.prompt_ask(
                prompt=f"🤔 Do you want to remove the site [bold][fm.ok]'{site}'[/bold][/fm.ok] from '{address}'",
                choices=["yes", "no"],
                default="no",
                required_flag="--yes or -y",
            )
            if choice != "yes":
                output.print("Cancelled.", emoji_code=":x:")
                raise typer.Exit(0)

        bench.remove_site(site, delete_db_from_global_db=delete_db_from_global_db)
        return

    schemas = _site_schemas(bench_service, address)
    confirmed = False

    if len(schemas) > 1:
        if not all_sites:
            names = ", ".join(s.site for s in schemas)
            output.display_error(
                f"Bench '{address}' serves {_plural(len(schemas), 'site')}: {names}. Deleting the bench destroys every one of them. Pass --all-sites to say that is what you mean, or delete one site at a time with 'fm delete {address}/{schemas[0].site}'."
            )
            raise typer.Exit(1)

        if not yes:
            _confirm_bench_name(output, address, schemas)
            # The name has just been typed. `remove_bench`'s own yes/no would be a second question
            # about the same decision, so it is skipped exactly as --yes skips it.
            confirmed = True

    bench_service.delete_bench(address, yes=yes or confirmed, delete_db_from_global_db=delete_db_from_global_db)
