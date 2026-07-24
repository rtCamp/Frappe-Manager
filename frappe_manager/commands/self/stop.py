import typer
from typer_examples import example

from frappe_manager import CLI_BENCHES_DIRECTORY
from frappe_manager.output_manager import get_global_output_handler, spinner
from frappe_manager.services_manager import ServicesEnum
from frappe_manager.services_manager.services import ServicesManager
from frappe_manager.site_manager.bench_service import BenchService


@example(
    "Stop everything (all benches + global services)",
    "",
    detail="Stops all running benches and all global services (global-db, global-nginx-proxy).",
)
@example(
    "Stop only global services",
    "--global-only",
    detail="Stops only global services, leaves benches running.",
)
@example(
    "Stop only benches",
    "--benches-only",
    detail="Stops only benches, leaves global services running.",
)
def stop(
    ctx: typer.Context,
    global_only: bool = typer.Option(False, "--global-only", help="Stop only global services"),
    benches_only: bool = typer.Option(False, "--benches-only", help="Stop only benches"),
):
    """
    Stop everything managed by FM.

    Stops all running benches and global services (global-db, global-nginx-proxy).
    Use --global-only or --benches-only to stop only a subset.
    """
    services_manager: ServicesManager = ctx.obj["services"]
    verbose = ctx.obj["verbose"]
    output = get_global_output_handler()

    if global_only and benches_only:
        output.print("[fm.error]Error:[/fm.error] --global-only and --benches-only are mutually exclusive. Choose one.")
        raise typer.Exit(code=1)

    stop_global = not benches_only
    stop_benches = not global_only

    if stop_global:
        with spinner(output, "Stopping global services"):
            for service in ServicesEnum:
                if service == ServicesEnum.all:
                    continue

                if not services_manager.is_service_running(service.value):
                    output.print(f"Skipping already stopped service {service.value}")
                    continue

                services_manager.stop_service(services=[service.value])
                output.print(f"Stopped service {service.value}")

    if stop_benches:
        bench_service = BenchService(CLI_BENCHES_DIRECTORY, services_manager, verbose=verbose, output_handler=output)
        bench_names = bench_service.get_bench_names()

        if not bench_names:
            output.print("No benches found")
        else:
            for bench_name in bench_names:
                try:
                    bench = bench_service.get_bench(bench_name, workers_check=False, admin_tools_check=False)
                    if bench.running:
                        with spinner(output, f"Stopping {bench_name}"):
                            bench.stop()
                        output.print(f"Stopped bench {bench_name}")
                    else:
                        output.print(f"Skipping already stopped bench {bench_name}")
                except Exception as e:
                    output.warning(f"Failed to stop {bench_name}: {e}")
