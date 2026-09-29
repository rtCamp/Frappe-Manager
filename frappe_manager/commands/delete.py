from typing import Annotated

import typer
from typer_examples import example

from frappe_manager import CLI_BENCHES_DIRECTORY
from frappe_manager.commands.arguments import BenchSiteArgument
from frappe_manager.output_manager import get_global_output_handler
from frappe_manager.site_manager.bench_service import BenchService
from frappe_manager.utils.process_lock import bench_lock


def _plural(count: int, noun: str) -> str:
    """`1 site` / `2 sites`. The counts are read out loud in a blast radius, so they must agree."""
    return f"{count} {noun}" if count == 1 else f"{count} {noun}s"


def _blast_radius(schemas, delete_fm_managed_db: bool | None = True) -> list[str]:
    """The rows shown before a bench serving several sites is destroyed.

    Built from `Bench.site_schemas()`, which reads each site's `site_config.json` off disk rather
    than the `[sites]` table: the bench config is missing or stale exactly when a delete is needed,
    and a blast radius that under-reports is worse than none.

    `SiteSchema.droppable` and `SiteSchema.unreadable` partition the sites exactly, so every site
    is reported once. A droppable schema is on the server fm owns for its engine. An unreadable one
    is neither dropped nor deliberately left: fm cannot drop a name it does not know and cannot
    promise it is gone, and that is the case that orphans a schema, so it is reported as itself.
    Everything else has a schema on a server fm does not own, which is named and left alone.

    `droppable` says fm MAY drop it; `delete_fm_managed_db` says whether it will. Reading only the
    first made the plan promise "1 schema dropped" for a run that had not been told to drop
    anything, and which then stopped to ask -- or, non-interactively, refused after the containers
    were already gone.
    """
    rows: list[tuple[str, str]] = [(_plural(len(schemas), "site"), ", ".join(s.site for s in schemas))]

    fm_owned = [s for s in schemas if s.droppable]
    if fm_owned:
        engines = {s.engine.value for s in fm_owned}
        if len(engines) == 1:
            label = f"{', '.join(s.schema for s in fm_owned)}  ({engines.pop()})"
        else:
            # A bench spanning both engines: a single trailing label would misattribute a schema
            # to the wrong server, so each schema names its own.
            label = ", ".join(f"{s.schema} ({s.engine.value})" for s in fm_owned)
        if delete_fm_managed_db is True:
            rows.append((f"{_plural(len(fm_owned), 'schema')} dropped", label))
        elif delete_fm_managed_db is False:
            rows.append((f"{_plural(len(fm_owned), 'schema')} kept", f"{label}  (fm's own, --delete-fm-managed-db drops)"))
        else:
            rows.append((f"{_plural(len(fm_owned), 'schema')} undecided", f"{label}  (you will be asked)"))

    # `schema` is None for a site whose config records an external host but no database name --
    # what a create that failed in preflight leaves behind. Interpolating it printed the literal
    # "1 schema kept None on 172.17.0.1".
    kept = [
        f"{s.schema or 'no schema recorded'} on {s.external_host}"
        for s in schemas
        if not s.droppable and not s.unreadable
    ]
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


def _print_deletion_plan(output, benchname: str, schemas, delete_fm_managed_db: bool | None = True) -> None:
    """The plan shown before any whole-bench deletion (and by --dry-run): what dies, where
    it lives and how big it is. Specificity belongs in the prompt, not the flag: this
    listing is what the typed-name ceremony below asks the operator to acknowledge."""
    from frappe_manager.utils.prune import dir_size, format_size

    output.warning(f"This will permanently delete bench '{benchname}':")
    bench_dir = CLI_BENCHES_DIRECTORY / benchname
    if bench_dir.exists():
        output.print(f"  dir    {bench_dir}  ({format_size(dir_size(bench_dir))})", emoji_code="")
    for line in _blast_radius(schemas, delete_fm_managed_db):
        output.print(line, emoji_code="")


def _confirm_bench_name(output, benchname: str) -> None:
    """Require the bench name typed back; anything else (including a bare Enter) removes
    nothing. The ceremony for EVERY whole-bench deletion: delete is the one command that
    destroys user data with no undo, and the typed name catches the wrong-bench /
    wrong-terminal accident a y/N cannot. The plan was printed just above by
    `_print_deletion_plan`."""
    typed = output.prompt_ask(
        prompt="Type the bench name to confirm deletion (anything else aborts)", required_flag="--yes or -y"
    )

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
        bench = bench_service.get_bench(benchname, start_workers_if_stopped=False, start_admin_tools_if_stopped=False)
    except FileNotFoundError:
        return []
    return bench.site_schemas()


@example(
    "Delete a bench and its database",
    "{benchname} --delete-fm-managed-db",
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
    "{benchname} --no-delete-fm-managed-db",
    detail="The bench is gone; the schema stays on its server.",
    benchname="mybench",
)
@example(
    "Delete unattended",
    "{benchname} --yes --delete-fm-managed-db",
    benchname="mybench",
)
@example(
    "Delete a multi-site bench unattended",
    "{benchname} --all-sites --yes --delete-fm-managed-db",
    detail="--yes skips the confirmation; --all-sites is still required, so no script deletes more than it named.",
    benchname="mybench",
)
@bench_lock(param="address", operation="delete")
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
    delete_fm_managed_db: Annotated[
        bool | None,
        typer.Option(
            "--delete-fm-managed-db/--no-delete-fm-managed-db",
            help="Drop the schema and user from the database server fm manages, or keep them. A schema on a server fm does not own is never dropped, with or without this flag. fm asks when neither is passed.",
        ),
    ] = None,
    delete_backups: Annotated[
        bool,
        typer.Option(
            "--delete-backups",
            help="Also delete the removed site's recorded database dumps. Off by default: a dump is the last copy of something, and once its history row is gone fm can no longer offer to prune it, so the paths are printed instead.",
        ),
    ] = False,
    dry_run: Annotated[
        bool,
        typer.Option("--dry-run", help="Print the deletion plan and exit without deleting anything; never prompts."),
    ] = False,
):
    """
    Delete a whole bench, or one site out of one.

    BENCH deletes the bench: every site in it, its containers and volumes, its whole directory, and its TLS certificates. A bench serving more than one site also needs --all-sites and asks for its name typed back, because one word would otherwise destroy several separately named sites.

    BENCH/SITE deletes just that site: its schema, its certificate, its proxy entries and its files. The bench and its other sites keep running.

    The database is decided separately. fm can drop a site's schema and user from the database server it manages, but a schema on a server fm does not own is always left in place, --delete-fm-managed-db or not. A schema fm cannot account for, one whose name is unreadable or whose drop failed, stops the deletion with the bench directory intact, because that directory holds the only record of the schema.
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
        bench = bench_service.get_bench(address, start_workers_if_stopped=False, start_admin_tools_if_stopped=False)

        # The bench is loaded first, so a name that resolves to nothing fails as "not found"
        # rather than offering to destroy whatever it did find.
        output.warning(
            f"Removing the site '{site}' from bench '{address}' drops its schema when the schema is fm's to drop, removes its certificate and deletes its files. The bench and its other sites keep running."
        )
        if dry_run:
            output.print("Dry run: nothing deleted.", emoji_code="")
            return
        if not yes:
            choice = output.prompt_ask(
                prompt=f"🤔 Do you want to remove the site [bold][fm.ok]'{site}'[/bold][/fm.ok] from '{address}' (default: no)",
                choices=["yes", "no"],
                default="no",
                required_flag="--yes or -y",
            )
            if choice != "yes":
                output.print("Cancelled.", emoji_code=":x:")
                raise typer.Exit(1)

        bench.remove_site(
            site, delete_fm_managed_db=delete_fm_managed_db, delete_backups=delete_backups
        )
        # The last site on an engine can leave by this path too, not only by deleting the bench.
        bench_service.services.reconcile_database_services()
        return

    schemas = _site_schemas(bench_service, address)

    _print_deletion_plan(output, address, schemas, delete_fm_managed_db)
    if dry_run:
        output.print("Dry run: nothing deleted.", emoji_code="")
        return

    # Asked here, before a single container is removed. The question used to surface per site
    # inside the removal, so a non-interactive run destroyed the containers and the network and
    # THEN refused, leaving a half-deleted bench to be re-run with the flag. `--yes` deliberately
    # does not answer it: dropping a schema is a decision about what happens, not permission to
    # proceed, and it gets its own flag (docs/commands/index.md).
    if delete_fm_managed_db is None and any(s.droppable for s in schemas) and not output.is_interactive():
        output.display_error(
            f"Bench '{address}' has {_plural(len([s for s in schemas if s.droppable]), 'schema')} on a database "
            "server fm manages, and nothing has said what to do with them. Pass --delete-fm-managed-db to drop "
            "them, or --no-delete-fm-managed-db to keep them."
        )
        raise typer.Exit(1)

    if len(schemas) > 1 and not all_sites:
        names = ", ".join(s.site for s in schemas)
        output.display_error(
            f"Bench '{address}' serves {_plural(len(schemas), 'site')}: {names}. Deleting the bench destroys every one of them. Pass --all-sites to say that is what you mean, or delete one site at a time with 'fm delete {address}/{schemas[0].site}'."
        )
        raise typer.Exit(1)

    confirmed = False
    if not yes:
        _confirm_bench_name(output, address)
        # The name has just been typed. `delete_bench`'s own yes/no would be a second question
        # about the same decision, so it is skipped exactly as --yes skips it.
        confirmed = True

    bench_service.delete_bench(address, yes=yes or confirmed, delete_fm_managed_db=delete_fm_managed_db)
