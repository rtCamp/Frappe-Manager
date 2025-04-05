import typer
from frappe_manager.logger import log
import json
from typing import List
from pathlib import Path
from rich.table import Table
from frappe_manager.services_manager.services import ServicesManager
from frappe_manager.site_manager.bench import Bench
from frappe_manager.display_manager.DisplayManager import richprint


class BenchesManager:
    def __init__(self, sitesdir: Path, services: ServicesManager, verbose: bool = False):
        self.root_path = sitesdir
        self.benches: List[Bench] = []

        self.verbose = False
        self.services: ServicesManager = services
        self.logger = log.get_logger()

    def set_typer_context(self, ctx: typer.Context):
        self.ctx: typer.Context = ctx

    def get_all_bench(self, exclude: List[str] = []):
        sites = {}
        for dir in self.root_path.iterdir():
            if dir.is_dir() and dir.parts[-1] not in exclude:
                name = dir.parts[-1]
                dir = dir / "docker-compose.yml"
                if dir.exists():
                    sites[name] = dir
        return sites

    def add_bench(self, bench: Bench):
        self.benches.append(bench)

    def create_benches(self, is_template_bench: bool = False):
        for bench in self.benches:
            bench.create(is_template_bench=is_template_bench)

    def start_benches(self):
        for bench in self.benches:
            bench.start()

    def stop_benches(self):
        for bench in self.benches:
            bench.stop()

    def remove_benches(self):
        for bench in self.benches:
            bench.remove_bench()

    def list_benches(self, output_format: str = "table"):
        """
        Lists all the sites and their status.
        
        Args:
            output_format: Format to output the list in ('table' or 'json')
        """
        richprint.change_head("Generating bench list")
        bench_list = self.get_all_bench()

        if not bench_list:
            self._handle_empty_bench_list()
            return

        bench_data = self._collect_bench_data(bench_list)
        
        if output_format == "json":
            self._output_json(bench_data)
        else:
            self._output_table(bench_data)

    def _handle_empty_bench_list(self):
        """Handle case when no benches are found"""
        richprint.exit(
            "Seems like you haven't created any sites yet. To create a bench, use the command: 'fm create <benchname>'.",
            emoji_code=":white_check_mark:",
        )

    def _collect_bench_data(self, bench_list):
        """
        Collect data about each bench and sort by status (Active first)
        
        Args:
            bench_list: Dictionary of bench names and their paths
        
        Returns:
            list: List of dictionaries containing bench data, sorted with Active benches first
        """
        bench_data = []
        for bench_name in bench_list.keys():
            try:
                bench = Bench.get_object(bench_name, self.services, workers_check=False, admin_tools_check=False)
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

        # Sort bench_data: Active first, then Inactive, then Error
        return sorted(
            bench_data,
            key=lambda x: (
                # Create a tuple for sorting priority:
                # First element: 0 for Active, 1 for Inactive, 2 for Error
                0 if x["status"] == "Active" else (1 if x["status"] == "Inactive" else 2),
                # Second element: bench name for consistent ordering within same status
                x["name"]
            )
        )

    def _output_json(self, bench_data):
        """
        Output bench data in JSON format
        
        Args:
            bench_data: List of dictionaries containing bench data
        """
        richprint.stdout.print(json.dumps(bench_data, indent=2))

    def _output_table(self, bench_data):
        """
        Output bench data in table format
        
        Args:
            bench_data: List of dictionaries containing bench data
        """
        list_table = self._create_table()
        
        for bench in bench_data:
            if bench["error"]:
                self._handle_error_row(bench)
                continue
                
            self._add_table_row(list_table, bench)
            richprint.update_live(list_table, padding=(0, 0, 0, 0))

        richprint.stop()

        if list_table.row_count:
            richprint.stdout.print(list_table)

    def _create_table(self):
        """Create and return a formatted Rich table"""
        table = Table(show_lines=True, show_header=True, highlight=True)
        table.add_column("Site")
        table.add_column("Status", vertical="middle")
        table.add_column("Path")
        return table

    def _handle_error_row(self, bench):
        """Handle displaying error rows"""
        richprint.warning(
            f'[red][bold]{bench["name"]}[/bold][/red] : Bench config not found at {bench["path"]}'
        )

    def _add_table_row(self, table, bench):
        """
        Add a row to the table for a bench
        
        Args:
            table: Rich Table object
            bench: Dictionary containing bench data
        """
        row_data = f"[link=http://{bench['name']}]{bench['name']}[/link]"
        path_data = f"[link=file://{bench['path']}]{bench['path']}[/link]"
        
        status_color = "green" if bench["status"] == "Active" else "white"
        status_data = f"[{status_color}]{bench['status']}[/{status_color}]"
        
        table.add_row(row_data, status_data, path_data, style=status_color)
