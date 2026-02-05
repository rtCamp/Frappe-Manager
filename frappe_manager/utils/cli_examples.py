import importlib.resources as pkg_resources
import json
from copy import deepcopy
from pathlib import Path


def get_frappe_manager_own_files(file_path: str):
    return Path(str(pkg_resources.files("frappe_manager").joinpath(file_path)))


def get_examples_from_toml(
    commands_stack: list[str],
    frappe_version: str,
    toml_path: Path = get_frappe_manager_own_files("./utils/examples.json"),
    example_data: dict[str, str] | None = None,
):
    file_data = toml_path.read_bytes()
    data: dict[str, list[dict[str, str]]] = json.loads(file_data)

    if example_data is None:
        example_data = {"benchname": "mybench", "domain": "example.com"}

    example_data["default_version"] = frappe_version

    examples_data = deepcopy(data)

    for command in commands_stack:
        if command not in examples_data:
            return None

        examples_data = examples_data[command]

    if "examples" not in examples_data:
        return None

    examples_data = examples_data["examples"]

    from rich.table import Table

    examples_table = Table(padding=(0, 0), title=None, show_header=False, show_lines=False, box=None)

    COMMANDS_WITHOUT_BENCHNAME = ["list", "services"]

    if examples_data:
        for element in examples_data:
            element_example_data = deepcopy(example_data)

            desc = element.get("desc", "None")
            code = element.get("code", "None")

            if "benchname" in element:
                element_example_data["benchname"] = element["benchname"]

            element_table = Table(box=None, show_lines=False)

            element_table.add_row(f"[bold cyan]{desc.format(**element_example_data)}[/bold cyan]")

            cmd = f"fm {' '.join(commands_stack)}"

            command_requires_benchname = commands_stack[0] not in COMMANDS_WITHOUT_BENCHNAME
            element_has_benchname_key = "benchname" in element
            benchname_is_nonempty = element_example_data["benchname"]

            if command_requires_benchname:
                if element_has_benchname_key:
                    if benchname_is_nonempty:
                        cmd += f" {element_example_data['benchname']}"
                else:
                    cmd += f" {element_example_data.get('benchname', 'mybench')}"

            if code and code.strip():
                cmd += code.format(**element_example_data)

            element_table.add_row(f"[blue]:play_button:[/blue] {cmd}")
            examples_table.add_row(element_table)
        return examples_table
