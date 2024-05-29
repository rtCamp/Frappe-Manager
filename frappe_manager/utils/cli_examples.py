from typing import Dict, List
import json
from pathlib import Path
import importlib.resources as pkg_resources

import rich

def get_frappe_manager_own_files(file_path: str):
    return Path(str(pkg_resources.files("frappe_manager").joinpath(file_path)))

def get_examples_from_toml(command_name: str, frappe_version: str, toml_path: Path = get_frappe_manager_own_files('./utils/examples.json')):
    file_data = toml_path.read_bytes()
    data: Dict[str,List[Dict[str,str]]] = json.loads(file_data)

    example_data ={
        'current_command' : command_name,
        'benchname' : 'example.com',
        'default_version' : frappe_version
    }

    from rich.table import Table

    examples_table = Table(padding=(0,0),title=None,show_header=False,show_lines=False, box=None)

    if command_name in data:

        for element in data[command_name]:
            desc = element.get('desc', 'None')
            code = element.get('code', 'None')

            element_table = Table(box=None,show_lines=False)

            element_table.add_row(f"[bold cyan]{desc.format(**example_data)}[/bold cyan]")
            element_table.add_row(f"[blue]:play_button:[/blue] {code.format(**example_data)}")
            examples_table.add_row(element_table)

        return examples_table
