from copy import deepcopy
from typing import Dict, List
import json
from pathlib import Path
import importlib.resources as pkg_resources


def get_frappe_manager_own_files(file_path: str):
    return Path(str(pkg_resources.files("frappe_manager").joinpath(file_path)))


def get_examples_from_toml(
    commands_stack: List[str],
    frappe_version: str,
    toml_path: Path = get_frappe_manager_own_files('./utils/examples.json'),
    benchname: str = 'mybench',
):
    file_data = toml_path.read_bytes()
    data: Dict[str, List[Dict[str, str]]] = json.loads(file_data)

    example_data = {'benchname': benchname, 'default_version': frappe_version}

    examples_data = deepcopy(data)

    for command in commands_stack:
        if not command in examples_data:
            return None

        examples_data = examples_data[command]

    if not 'examples' in examples_data:
        return None

    examples_data = examples_data['examples']

    from rich.table import Table

    examples_table = Table(padding=(0, 0), title=None, show_header=False, show_lines=False, box=None)

    COMMANDS_WITHOUT_BENCHNAME = ['list', 'services']

    if examples_data:
        element_example_data = deepcopy(example_data)

        for element in examples_data:
            desc = element.get('desc', 'None')
            code = element.get('code', 'None')

            if 'benchname' in element:
                element_example_data['benchname'] = element['benchname']

            element_table = Table(box=None, show_lines=False)

            element_table.add_row(f"[bold cyan]{desc.format(**element_example_data)}[/bold cyan]")

            cmd = f"fm {' '.join(commands_stack)}"

            command_requires_benchname = commands_stack[0] not in COMMANDS_WITHOUT_BENCHNAME
            element_has_benchname_key = 'benchname' in element
            benchname_is_nonempty = element_example_data['benchname']

            if command_requires_benchname:
                if element_has_benchname_key:
                    if benchname_is_nonempty:
                        cmd += f" {element_example_data['benchname']}"
                else:
                    cmd += f" {benchname}"

            if code and code.strip():
                cmd += code.format(**element_example_data)

            element_table.add_row(f"[blue]:play_button:[/blue] {cmd}")
            examples_table.add_row(element_table)
        return examples_table
