#!/usr/bin/env python3
import inspect
import json
import sys
from pathlib import Path
from typing import Any, get_args, get_origin

import typer
from rich.console import Console

project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

console = Console()


def load_examples() -> dict:
    examples_path = project_root / "frappe_manager" / "utils" / "examples.json"
    with open(examples_path) as f:
        return json.load(f)


def extract_param_info(param_name: str, param: inspect.Parameter) -> dict[str, Any]:
    annotation = param.annotation
    default_val = param.default

    info = {
        "name": param_name,
        "required": default_val == inspect.Parameter.empty,
        "default": None,
        "type": "str",
        "help": "",
        "is_option": False,
        "is_argument": False,
        "option_names": [],
    }

    typer_info = None
    actual_type = annotation

    if get_origin(annotation) is not None:
        args = get_args(annotation)
        if args:
            if len(args) >= 2:
                actual_type = args[0]
                typer_info = args[1]
            elif len(args) == 1:
                actual_type = args[0]

            if hasattr(actual_type, "__name__"):
                info["type"] = actual_type.__name__

    if typer_info is not None:
        if hasattr(typer_info, "__class__"):
            class_name = typer_info.__class__.__name__

            if "OptionInfo" in class_name or "Option" in class_name:
                info["is_option"] = True
                if hasattr(typer_info, "help"):
                    info["help"] = typer_info.help or ""

                option_names = []
                if hasattr(typer_info, "param_decls") and typer_info.param_decls:
                    option_names.extend(typer_info.param_decls)

                if (
                    hasattr(typer_info, "default")
                    and isinstance(typer_info.default, str)
                    and typer_info.default.startswith("--")
                ):
                    if typer_info.default not in option_names:
                        option_names.append(typer_info.default)

                if not option_names:
                    option_names = [f"--{param_name.replace('_', '-')}"]

                info["option_names"] = option_names

                if hasattr(typer_info, "default") and typer_info.default is not None:
                    default_value = typer_info.default
                    if not isinstance(default_value, str) or not default_value.startswith("--"):
                        if default_value is not ... and str(default_value) != "Ellipsis":
                            info["default"] = str(default_value)

            elif "ArgumentInfo" in class_name or "Argument" in class_name:
                info["is_argument"] = True
                if hasattr(typer_info, "help"):
                    info["help"] = typer_info.help or ""

    if not info["is_option"] and not info["is_argument"]:
        if default_val != inspect.Parameter.empty:
            if default_val is not ... and str(default_val) != "Ellipsis":
                info["default"] = str(default_val)

    return info


def extract_command_info(command_info: typer.models.CommandInfo) -> dict[str, Any]:
    callback = command_info.callback

    if callback is None:
        return {
            "name": command_info.name or "unknown",
            "description": "",
            "params": [],
        }

    doc = inspect.getdoc(callback) or ""
    sig = inspect.signature(callback)

    params = []
    for param_name, param in sig.parameters.items():
        if param_name in ("ctx", "self"):
            continue

        param_info = extract_param_info(param_name, param)
        params.append(param_info)

    return {
        "name": command_info.name or callback.__name__,
        "description": doc,
        "params": params,
        "callback": callback,
    }


def extract_typer_structure(app: typer.Typer, path: list[str] | None = None) -> dict:
    if path is None:
        path = []

    # Explicit typing helps static analyzers understand the shape
    structure: dict[str, Any] = {
        "path": path,
        "commands": [],
        "groups": [],
    }

    for cmd in app.registered_commands:
        cmd_info = extract_command_info(cmd)
        cmd_info["path"] = path + [cmd_info["name"]]
        structure["commands"].append(cmd_info)

    for group in app.registered_groups:
        group_name = group.name
        if not group_name:
            continue

        group_typer = group.typer_instance
        if group_typer is None:
            continue

        group_path = path + [group_name]

        sub_structure = extract_typer_structure(group_typer, group_path)
        structure["groups"].append(
            {
                "name": group_name,
                "path": group_path,
                "structure": sub_structure,
            }
        )

    return structure


def get_examples_for_command(examples_data: dict, command_path: list[str]) -> list[dict] | None:
    current = examples_data

    for segment in command_path:
        if segment not in current:
            return None
        current = current[segment]

    if "examples" in current:
        return current["examples"]

    return None


def format_examples(examples: list[dict], command_path: list[str], benchname: str = "mybench") -> str:
    if not examples:
        return ""

    md = "\n**Examples**:\n\n"

    COMMANDS_WITHOUT_BENCHNAME = ["list", "services", "migrate"]

    for example in examples:
        desc = example.get("desc", "")
        code = example.get("code", "")
        custom_benchname = example.get("benchname", benchname)

        desc_formatted = desc.format(benchname=custom_benchname, domain="example.com", default_version="version-15")
        md += f"_{desc_formatted}_\n"

        cmd = f"fm {' '.join(command_path)}"

        command_requires_benchname = command_path[0] not in COMMANDS_WITHOUT_BENCHNAME
        element_has_benchname_key = "benchname" in example

        if command_requires_benchname:
            if element_has_benchname_key:
                if custom_benchname:
                    cmd += f" {custom_benchname}"
            else:
                cmd += f" {benchname}"

        if code and code.strip():
            cmd += code.format(benchname=custom_benchname, domain="example.com", default_version="version-15")

        md += f"```bash\n{cmd}\n```\n\n"

    return md


def generate_command_markdown(cmd_info: dict, examples_data: dict, level: int = 2) -> str:
    heading = "#" * level
    command_path = cmd_info["path"]
    full_command = "fm " + " ".join(command_path)

    md = f"{heading} `{full_command}`\n\n"

    description = cmd_info["description"].strip() if cmd_info["description"] else ""
    if description:
        md += f"{description}\n\n"

    md += "**Usage**:\n\n```console\n"
    md += f"$ {full_command}"

    arguments = [p for p in cmd_info["params"] if p["is_argument"]]
    for arg in arguments:
        md += f" {arg['name'].upper()}"

    if any(p["is_option"] for p in cmd_info["params"]):
        md += " [OPTIONS]"

    md += "\n```\n\n"

    if arguments:
        md += "**Arguments**:\n\n"
        for arg in arguments:
            arg_text = f"* `{arg['name'].upper()}`"
            if arg["help"]:
                arg_text += f": {arg['help']}"
            if arg["required"]:
                arg_text += "  [required]"
            md += f"{arg_text}\n"
        md += "\n"

    options = [p for p in cmd_info["params"] if p["is_option"]]
    if options:
        md += "**Options**:\n\n"
        for opt in options:
            opt_names = opt.get("option_names", [f"--{opt['name'].replace('_', '-')}"])
            opt_text = f"* `{', '.join(opt_names)}`"
            if opt["help"]:
                opt_text += f": {opt['help']}"
            if opt["default"] and opt["default"] != "None":
                opt_text += f"  [default: {opt['default']}]"
            md += f"{opt_text}\n"
        md += "\n"

    examples = get_examples_for_command(examples_data, command_path)
    if examples:
        md += format_examples(examples, command_path)

    return md


def generate_group_markdown(group_info: dict, examples_data: dict, level: int = 2) -> str:
    heading = "#" * level
    group_path = group_info["path"]
    full_command = "fm " + " ".join(group_path)

    md = f"{heading} `{full_command}`\n\n"
    md += f"{group_info['name'].title()} commands.\n\n"
    md += "**Usage**:\n\n```console\n"
    md += f"$ {full_command} [OPTIONS] COMMAND [ARGS]...\n```\n\n"
    md += "**Options**:\n\n* `--help`: Show this message and exit.\n\n"

    structure = group_info["structure"]

    if structure["commands"]:
        md += "**Commands**:\n\n"
        for cmd in structure["commands"]:
            cmd_name = cmd["name"]
            desc = cmd["description"].split("\n")[0] if cmd["description"] else f"{cmd_name.title()} command"
            md += f"* `{cmd_name}`: {desc}\n"
        md += "\n"

    for cmd in structure["commands"]:
        md += "\n" + generate_command_markdown(cmd, examples_data, level=level + 1)

    for sub_group in structure["groups"]:
        md += "\n" + generate_group_markdown(sub_group, examples_data, level=level + 1)

    return md


def update_wiki_home(home_path: Path, structure: dict) -> bool:
    if not home_path.exists():
        console.print(f"[yellow]Warning: Home.md not found at {home_path}[/yellow]")
        return False

    content = home_path.read_text()

    commands_start = content.find("#### **Commands**:")
    if commands_start == -1:
        console.print("[yellow]Warning: Commands section not found in Home.md[/yellow]")
        return False

    commands_lines = ["#### **Commands**:\n\n"]

    all_items = []

    for cmd in sorted(structure["commands"], key=lambda x: x["name"]):
        desc = cmd["description"].split("\n")[0] if cmd["description"] else f"{cmd['name'].title()} command"
        all_items.append((cmd["name"], desc))

    for group in sorted(structure["groups"], key=lambda x: x["name"]):
        desc = f"{group['name'].title()} commands."
        all_items.append((group["name"], desc))

    for cmd_name, desc in all_items:
        commands_lines.append(f"* [`{cmd_name}`](https://github.com/rtCamp/Frappe-Manager/wiki/{cmd_name}): {desc}\n")

    commands_lines.append("\n")
    new_commands = "".join(commands_lines)

    section_end = content.find("\n\n### `fmx`", commands_start)
    if section_end == -1:
        section_end = content.find("\n###", commands_start + 10)
    if section_end == -1:
        section_end = len(content)

    new_content = content[:commands_start] + new_commands + content[section_end:]

    home_path.write_text(new_content)
    return True


def update_wiki_sidebar(sidebar_path: Path, structure: dict) -> bool:
    if not sidebar_path.exists():
        console.print(f"[yellow]Warning: _Sidebar.md not found at {sidebar_path}[/yellow]")
        return False

    content = sidebar_path.read_text()

    commands_start = content.find("### [:arrow_forward: Commands]")
    if commands_start == -1:
        console.print("[yellow]Warning: Commands section not found in _Sidebar.md[/yellow]")
        return False

    commands_lines = [" ### [:arrow_forward: Commands](https://github.com/rtCamp/frappe-manager/wiki#commands)\n"]

    all_items = []

    for cmd in sorted(structure["commands"], key=lambda x: x["name"]):
        all_items.append(cmd["name"])

    for group in sorted(structure["groups"], key=lambda x: x["name"]):
        all_items.append(group["name"])

    for cmd_name in all_items:
        commands_lines.append(f"> - [{cmd_name}](https://github.com/rtCamp/Frappe-Manager/wiki/{cmd_name})\n")

    commands_lines.append("\n")
    new_commands = "".join(commands_lines)

    section_end = content.find("\n ### [:question: FAQ Topics", commands_start)
    if section_end == -1:
        section_end = content.find("\n ###", commands_start + 10)
    if section_end == -1:
        section_end = len(content)

    new_content = content[:commands_start] + new_commands + content[section_end:]

    sidebar_path.write_text(new_content)
    return True


def update_readme_command_reference(readme_path: Path, structure: dict) -> bool:
    if not readme_path.exists():
        console.print(f"[yellow]Warning: README.md not found at {readme_path}[/yellow]")
        return False

    content = readme_path.read_text()

    table_start = content.find("## 📋 Command Reference")
    if table_start == -1:
        console.print("[yellow]Warning: Command Reference section not found in README.md[/yellow]")
        return False

    table_lines = [
        "## 📋 Command Reference\n",
        "\n",
        "| Command | Description | Documentation |\n",
        "|---------|-------------|---------------|\n",
    ]

    all_items = []

    for cmd in sorted(structure["commands"], key=lambda x: x["name"]):
        desc = cmd["description"].split("\n")[0] if cmd["description"] else f"{cmd['name'].title()} command"
        desc = desc[0].upper() + desc[1:] if desc else desc
        all_items.append((cmd["name"], desc, f"https://github.com/rtCamp/Frappe-Manager/wiki/{cmd['name'].title()}"))

    for group in sorted(structure["groups"], key=lambda x: x["name"]):
        desc = f"Manage {group['name']}"
        desc = desc[0].upper() + desc[1:] if desc else desc
        all_items.append(
            (group["name"], desc, f"https://github.com/rtCamp/Frappe-Manager/wiki/{group['name'].title()}")
        )

    all_items.sort(key=lambda x: x[0])

    for cmd_name, desc, link in all_items:
        table_lines.append(f"| `fm {cmd_name}` | {desc} | [Wiki: {cmd_name.title()}]({link}) |\n")

    table_lines.append("\n")
    table_lines.append(
        "> 💡 **Tip**: Use `fm <command> --help` to see detailed options and examples for any command.\n"
    )

    new_table = "".join(table_lines)

    section_end = content.find("\n## ", table_start + 10)
    if section_end == -1:
        section_end = len(content)

    new_content = content[:table_start] + new_table + content[section_end:]

    readme_path.write_text(new_content)
    return True


def generate_all_docs(output_dir: Path, update_readme: bool = False) -> dict:
    console.print("[bold blue]Generating CLI documentation...[/bold blue]")

    examples_data = load_examples()
    console.print("[green]✓[/green] Loaded examples from examples.json")

    from frappe_manager.commands import app

    structure = extract_typer_structure(app)
    console.print(
        f"[green]✓[/green] Extracted {len(structure['commands'])} commands and {len(structure['groups'])} groups"
    )

    output_dir.mkdir(parents=True, exist_ok=True)
    # If the provided output_dir already points to a `commands` folder, use it directly.
    if output_dir.name == "commands":
        commands_dir = output_dir
    else:
        commands_dir = output_dir / "commands"
    commands_dir.mkdir(exist_ok=True)

    generated_files = []

    for cmd_info in structure["commands"]:
        cmd_name = cmd_info["name"]
        md_content = generate_command_markdown(cmd_info, examples_data, level=2)

        output_file = commands_dir / f"{cmd_name}.md"
        output_file.write_text(md_content)
        generated_files.append(output_file)
        console.print(f"[green]✓[/green] Generated {output_file.relative_to(output_dir)}")

    for group_info in structure["groups"]:
        group_name = group_info["name"]
        md_content = generate_group_markdown(group_info, examples_data, level=2)

        output_file = commands_dir / f"{group_name}.md"
        output_file.write_text(md_content)
        generated_files.append(output_file)
        console.print(f"[green]✓[/green] Generated {output_file.relative_to(output_dir)}")

    home_path = output_dir / "Home.md"
    if home_path.exists():
        if update_wiki_home(home_path, structure):
            console.print(f"[green]✓[/green] Updated Home.md commands section")
        else:
            console.print(f"[yellow]![/yellow] Could not update Home.md")

    sidebar_path = output_dir / "_Sidebar.md"
    if sidebar_path.exists():
        if update_wiki_sidebar(sidebar_path, structure):
            console.print(f"[green]✓[/green] Updated _Sidebar.md commands section")
        else:
            console.print(f"[yellow]![/yellow] Could not update _Sidebar.md")

    if update_readme:
        readme_path = project_root / "README.md"
        if update_readme_command_reference(readme_path, structure):
            console.print(f"[green]✓[/green] Updated README.md command reference")
        else:
            console.print(f"[yellow]![/yellow] Could not update README.md")

    console.print(f"\n[bold green]✓ Generated {len(generated_files)} documentation files[/bold green]")
    console.print(f"Output directory: {output_dir}")

    return {"structure": structure, "examples_data": examples_data, "generated_files": generated_files}


def main():
    import argparse
    import os

    parser = argparse.ArgumentParser(description="Generate CLI documentation for Frappe Manager")
    parser.add_argument(
        "--update-readme",
        action="store_true",
        help="Update command reference table in README.md",
    )
    parser.add_argument(
        "--output-dir",
        type=str,
        help="Output directory for generated docs (overrides WIKI_DIR env var)",
    )
    args = parser.parse_args()

    if args.output_dir:
        output_dir = Path(args.output_dir)
        console.print(f"[blue]Using --output-dir argument: {output_dir}[/blue]")
    else:
        # Prefer DOCS_COMMANDS_DIR for new behaviour, fall back to WIKI_DIR for backward compatibility
        docs_commands_env = os.getenv("DOCS_COMMANDS_DIR")
        if docs_commands_env:
            output_dir = Path(docs_commands_env)
            console.print(f"[blue]Using DOCS_COMMANDS_DIR environment variable: {output_dir}[/blue]")
        else:
            wiki_dir_env = os.getenv("WIKI_DIR")
            if wiki_dir_env:
                output_dir = Path(wiki_dir_env)
                console.print(f"[blue]Using WIKI_DIR environment variable: {output_dir}[/blue]")
            else:
                # Default to repository docs/commands path
                output_dir = project_root / "docs" / "commands"
                console.print(f"[yellow]No WIKI_DIR/DOCS_COMMANDS_DIR found, using: {output_dir}[/yellow]")

    try:
        generate_all_docs(output_dir, update_readme=args.update_readme)
    except Exception as e:
        console.print(f"[bold red]Error: {e}[/bold red]")
        import traceback

        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    main()
