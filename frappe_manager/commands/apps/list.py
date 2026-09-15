"""List a bench's apps command."""

import typer
from typer_examples import example

from frappe_manager.commands import check_bench_migration_required
from frappe_manager.commands.arguments import BenchNameArgument
from frappe_manager.output_manager import get_global_output_handler
from frappe_manager.site_manager.site import Bench


@example(
    "List a bench's apps",
    "{benchname}",
    benchname="mybench",
)
def list_apps(
    ctx: typer.Context,
    benchname: BenchNameArgument = None,
):
    """
    List the apps a bench has recorded, and what is actually on disk.

    Recorded apps come from bench_config.toml, with their pinned refs. Installed apps come from the workspace's sites/apps.txt, which an image-runtime bench has no workspace to read -- that section is left out instead of guessed at.
    """

    services_manager = ctx.obj["services"]
    output = get_global_output_handler()
    check_bench_migration_required(benchname)

    bench = Bench.get_object(benchname, services_manager, output_handler=output)

    # Copy target, not a table: plain lines survive copying and piping, the same rule
    # `commands/list.py`'s --paths form follows.
    output.stop()

    apps = bench.bench_config.apps_list
    typer.echo(f"{bench.name} recorded apps:")
    if not apps:
        typer.echo("  (none)")
    else:
        width = max(len(app.name) for app in apps)
        for app in apps:
            typer.echo(f"  {app.name:<{width}}  {app.repo}:{app.ref or 'default'}")

    apps_txt = bench.path / "workspace" / "frappe-bench" / "sites" / "apps.txt"
    if apps_txt.exists():
        installed = [line.strip() for line in apps_txt.read_text().splitlines() if line.strip()]
        typer.echo(f"{bench.name} installed on disk:")
        for name in installed:
            typer.echo(f"  {name}")
    else:
        typer.echo(f"{bench.name} has no workspace on disk (image runtime) -- installed apps unknown")
