import typer
from frappe_manager.logger import log
from typing import List, Optional
from pathlib import Path
from rich.table import Table
from frappe_manager.services_manager.services import ServicesManager
from frappe_manager.site_manager.site import Bench
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

    def list_benches(self):
        """
        Lists all the sites and their status.
        """

        # TODO entrypoint check can be changed
        richprint.change_head("Generating bench list")

        bench_list = self.get_all_bench()

        if not bench_list:
            richprint.exit(
                "Seems like you haven't created any sites yet. To create a bench, use the command: 'fm create <benchname>'.",
                emoji_code=":white_check_mark:",
            )

        list_table = Table(show_lines=True, show_header=True, highlight=True)
        list_table.add_column("Site")
        list_table.add_column("Status", vertical="middle")
        list_table.add_column("Path")

        for bench_name in bench_list.keys():
            try:
                bench = Bench.get_object(bench_name, self.services, workers_check=False, admin_tools_check=False)

                row_data = f"[link=http://{bench.name}]{bench.name}[/link]"
                path_data = f"[link=file://{bench.path}]{bench.path}[/link]"

                status_color = "white"
                status_msg = "Inactive"

                if bench.compose_project.running:
                    status_color = "green"
                    status_msg = "Active"

                status_data = f"[{status_color}]{status_msg}[/{status_color}]"

                list_table.add_row(row_data, status_data, path_data, style=f"{status_color}")
                richprint.update_live(list_table, padding=(0, 0, 0, 0))
            except FileNotFoundError as e:
                richprint.warning(f'[red][bold]{bench_name}[/bold][/red] : Bench config not found at {e.filename}')

        richprint.stop()

        if list_table.row_count:
            richprint.stdout.print(list_table)
