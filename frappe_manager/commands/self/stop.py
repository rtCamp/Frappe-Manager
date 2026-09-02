import typer
from typer_examples import example

from frappe_manager import CLI_BENCHES_DIRECTORY
from frappe_manager.output_manager import get_global_output_handler, spinner
from frappe_manager.services_manager import ServicesEnum
from frappe_manager.services_manager.services import ServicesManager
from frappe_manager.site_manager.bench_service import BenchService


@example(
    "Stop everything",
    "",
)
@example(
    "Stop the global services, leave the benches up",
    "--global-only",
)
@example(
    "Stop the benches, leave the global services up",
    "--benches-only",
)
def stop(
    ctx: typer.Context,
    global_only: bool = typer.Option(
        False, "--global-only", help="Stop the global services only, leaving every bench running."
    ),
    benches_only: bool = typer.Option(
        False, "--benches-only", help="Stop every bench only, leaving the global services running."
    ),
):
    """
    Stop every bench on this host, then the global services (global-nginx-proxy, global-db).

    Nothing fm manages is left running unless you narrow the blast radius with --benches-only or --global-only.

    A bench that fails to stop does not abort the run: the remaining benches and the global services are still stopped, and fm ends by naming what is still up and exiting non-zero.
    """
    services_manager: ServicesManager = ctx.obj["services"]
    verbose = ctx.obj["verbose"]
    output = get_global_output_handler()

    if global_only and benches_only:
        output.print("[fm.error]Error:[/fm.error] --global-only and --benches-only are mutually exclusive. Choose one.")
        raise typer.Exit(code=1)

    stop_global = not benches_only
    stop_benches = not global_only

    # Best effort: every bench is attempted, and the global services after them, before the
    # command reports failure. Reporting has to happen, otherwise a caller checking $? is told
    # the host is quiet while containers are still up.
    benches_failed: list[str] = []

    if stop_benches:
        bench_service = BenchService(CLI_BENCHES_DIRECTORY, services_manager, verbose=verbose, output_handler=output)
        bench_names = bench_service.get_bench_names()

        if not bench_names:
            output.print("No benches found")
        else:
            for bench_name in bench_names:
                try:
                    bench = bench_service.get_bench(bench_name, workers_check=False, admin_tools_check=False)
                    # No `if bench.running` guard: that predicate is all-or-nothing over the MAIN
                    # compose file only, so a partially running bench (crashed frappe, or only the
                    # worker/admin-tools containers left) reads as stopped and would keep its
                    # containers. bench.stop() also stops workers and admin tools, so it is
                    # strictly wider than the predicate -- always run it, like `fm stop` does.
                    with spinner(output, f"Stopping {bench_name}"):
                        bench.stop()
                    output.print(f"Stopped bench {bench_name}")
                except Exception as e:
                    output.warning(f"Failed to stop {bench_name}: {e}")
                    benches_failed.append(bench_name)

    if stop_global:
        with spinner(output, "Stopping global services"):
            # Benches first (above), then the globals in reverse dependency order: the proxy goes
            # down before the database it fronts, so nothing is ever reachable-but-databaseless.
            for service in reversed([s for s in ServicesEnum if s != ServicesEnum.all]):
                if not services_manager.is_service_running(service.value):
                    output.print(f"Skipping already stopped service {service.value}")
                    continue

                services_manager.stop_service(services=[service.value])
                output.print(f"Stopped service {service.value}")

    if benches_failed:
        output.display_error(f"Still running: {', '.join(benches_failed)}")
        raise typer.Exit(1)
