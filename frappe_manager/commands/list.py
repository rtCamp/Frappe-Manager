from enum import Enum
from typing import Annotated
import typer
from frappe_manager.utils.bench import get_all_benches
from frappe_manager.commands import app
import json
from rich.table import Table
from frappe_manager import CLI_BENCHES_DIRECTORY
from frappe_manager.display_manager.DisplayManager import richprint
from frappe_manager.services_manager.services import ServicesManager
from frappe_manager.site_manager.site import Bench

class OutputFormat(str, Enum):
    table = "table"
    json = "json"

@app.command()
def list(
    ctx: typer.Context,
    output: Annotated[
        OutputFormat, 
        typer.Option(
            "--output", 
            "-o", 
            help="Output format for the bench list",
            case_sensitive=False
        )
    ] = OutputFormat.table
):
    """Lists all of the available benches."""
    services_manager = ctx.obj["services"]
    list_benches(services_manager, output_format=output)

def _create_table():
    """Create and return a formatted Rich table"""
    table = Table(show_lines=True, show_header=True, highlight=True)
    table.add_column("Site")
    table.add_column("Status", vertical="middle")
    table.add_column("Path")
    return table

def _handle_error_row(bench):
    """Handle displaying error rows"""
    richprint.warning(
        f'[red][bold]{bench["name"]}[/bold][/red] : Bench config not found at {bench["path"]}'
    )

def _add_table_row(table, bench):
    """Add a row to the table for a bench"""
    row_data = f"[link=http://{bench['name']}]{bench['name']}[/link]"
    path_data = f"[link=file://{bench['path']}]{bench['path']}[/link]"

    status_color = "green" if bench["status"] == "Active" else "white"
    status_data = f"[{status_color}]{bench['status']}[/{status_color}]"

    table.add_row(row_data, status_data, path_data, style=status_color)

def _output_bench_table(bench_data):
    """Output bench data in table format"""
    list_table = _create_table()

    for bench in bench_data:
        if bench["error"]:
            _handle_error_row(bench)
            continue

        _add_table_row(list_table, bench)
        richprint.update_live(list_table, padding=(0, 0, 0, 0))

    richprint.stop()

    if list_table.row_count:
        richprint.stdout.print(list_table)

def list_benches(services_manager: ServicesManager, output_format: str = "table"):
    """List all benches in table or JSON format"""
    richprint.change_head("Generating bench list")
    bench_list = get_all_benches(CLI_BENCHES_DIRECTORY)

    if not bench_list:
        richprint.exit(
            "Seems like you haven't created any sites yet. To create a bench, use the command: 'fm create <benchname>'.",
            emoji_code=":white_check_mark:",
        )
        return

    bench_data = []
    for bench_name in bench_list.keys():
        try:
            bench = Bench.get_object(
                bench_name,
                services_manager,
                workers_check=False,
                admin_tools_check=False
            )
            bench_data.append({
                "name": bench.name,
                "path": str(bench.path),
                "status": "Active" if bench.compose_project.running else "Inactive",
                "error": None
            })
        except FileNotFoundError as e:
            bench_data.append({
                "name": bench_name,
                "path": str(e.filename),
                "status": "Error",
                "error": "Config not found"
            })

    if output_format == "json":
        richprint.stdout.print(json.dumps(bench_data, indent=2))
    else:
        _output_bench_table(bench_data)
