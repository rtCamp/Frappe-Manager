"""Site Manager Module - Configuration and Constants"""

import json
from functools import cache
from importlib.resources import files

__all__ = [
    "get_vscode_launch_json",
    "get_vscode_tasks_json",
    "get_vscode_settings_json",
    "NON_BASH_SUPPORTED_SERVICES",
]


def _load_vscode_template(filename: str) -> dict:
    # importlib.resources + json parsing on every `fm` invocation was measurable; these are
    # only needed by the `fm code` devtools path, so loading is deferred to first use.
    templates = files("frappe_manager.templates.vscode")
    return json.loads((templates / filename).read_text())


@cache
def get_vscode_launch_json() -> dict:
    return _load_vscode_template("launch.json")


@cache
def get_vscode_tasks_json() -> dict:
    return _load_vscode_template("tasks.json")


@cache
def get_vscode_settings_json() -> dict:
    return _load_vscode_template("settings.json")


NON_BASH_SUPPORTED_SERVICES = ["redis-cache", "redis-queue", "adminer", "mailpit"]
