from copy import deepcopy
from typing import Dict, List, Optional
import json
from pathlib import Path
import importlib.resources as pkg_resources

def get_frappe_manager_own_files(file_path: str):
    return Path(str(pkg_resources.files("frappe_manager").joinpath(file_path)))

def get_examples_from_toml(command: str, frappe_version: str, toml_path: Path = get_frappe_manager_own_files('./utils/examples.json'), sub_command: Optional[str] = None ):
    file_data = toml_path.read_bytes()
    data: Dict[str,List[Dict[str,str]]] = json.loads(file_data)

    bench_name = 'example.com'

    example_data ={
        'command' : command,
        'sub_command' : sub_command,
        'benchname' : bench_name,
        'default_version' : frappe_version
    }

    from rich.table import Table

    examples_table = Table(padding=(0,0),title=None,show_header=False,show_lines=False, box=None)

    if command in data:
        examples_data = data[command]

        if isinstance(examples_data, dict):
            if not sub_command:
                if not 'examples'in examples_data:
                    return None
            else:
                examples_data = examples_data[sub_command]
                sub_command = f" {sub_command} "
        else:
            sub_command = ' '


        if examples_data:
            element_example_data = deepcopy(example_data)

            for element in examples_data:
                desc = element.get('desc', 'None')
                code = element.get('code', 'None')

                if 'benchname' in element:
                    element_example_data['benchname'] = element['benchname']

                element_table = Table(box=None,show_lines=False)

                element_table.add_row(f"[bold cyan]{desc.format(**example_data)}[/bold cyan]")
                element_table.add_row(f"[blue]:play_button:[/blue] fm {command}{sub_command}{element_example_data['benchname']}{code.format(**element_example_data)}")
                examples_table.add_row(element_table)
            return examples_table
