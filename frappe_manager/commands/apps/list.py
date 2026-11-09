import typer
from typer_examples import example

from frappe_manager.commands import check_bench_migration_required
from frappe_manager.commands.arguments import BenchNameArgument, JsonFieldsOption, JsonResultOption, _apply_fields
from frappe_manager.output_manager import get_global_output_handler
from frappe_manager.site_manager.site import Bench
from frappe_manager.utils.site import host_bench_dir


@example(
    "List a bench's apps",
    "{benchname}",
    benchname="mybench",
)
def list_apps(
    ctx: typer.Context,
    benchname: BenchNameArgument = None,
    json_result: JsonResultOption = False,
    fields: JsonFieldsOption = None,
):
    """
    List the apps a bench has recorded, and what is actually on disk.

    Recorded apps come from bench_config.toml, with their pinned refs. Installed apps come from sites/apps.txt, which is bind-mounted into every bench -- mount OR image -- so this reads it the same way on both; "installed" is left out, not guessed, only when that file genuinely isn't there.
    """

    services_manager = ctx.obj["services"]
    output = get_global_output_handler()
    check_bench_migration_required(benchname)

    bench = Bench.get_object(benchname, services_manager, output_handler=output)

    apps = bench.bench_config.apps_list
    apps_txt = host_bench_dir(bench.path) / "sites" / "apps.txt"
    installed = (
        [line.strip() for line in apps_txt.read_text().splitlines() if line.strip()] if apps_txt.exists() else None
    )

    if json_result:
        get_global_output_handler().set_json_results()
        _apply_fields(fields)
        # `installed` is null, not [], when apps.txt genuinely isn't on disk: fm does not know
        # the installed apps, which is a different fact from knowing there are none.
        output.print_data(
            {
                "bench": bench.name,
                "recorded": [{"name": a.name, "repo": a.repo, "ref": a.ref} for a in apps],
                "installed": installed,
            }
        )
        return

    if not apps:
        # `apps_list` is in NOT_WRITTEN_TO_DISK, so `[[apps]]` exists only in a file a user wrote
        # for `fm bake --config`. Empty is the designed steady state, not drift, and "(none)" read
        # as a missing record on every normally created bench.
        output.data_raw(f"{bench.name} has no [[apps]] recorded in bench_config.toml (only a bake config writes one)")
    else:
        output.data_raw(f"{bench.name} recorded apps:")
        width = max(len(app.name) for app in apps)
        for app in apps:
            output.data_raw(f"  {app.name:<{width}}  {app.repo}:{app.ref or 'default'}")

    if installed is not None:
        output.data_raw(f"{bench.name} installed on disk:")
        for name in installed:
            output.data_raw(f"  {name}")
    else:
        output.data_raw(f"{bench.name} has no sites/apps.txt on disk -- installed apps unknown")
