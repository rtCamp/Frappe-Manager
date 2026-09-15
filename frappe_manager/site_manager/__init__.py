"""Site Manager Module - Configuration and Constants"""

import json
from importlib.resources import files

__all__ = [
    "VSCODE_LAUNCH_JSON",
    "VSCODE_TASKS_JSON",
    "VSCODE_SETTINGS_JSON",
    "NON_BASH_SUPPORTED_SERVICES",
]

_vscode_templates = files("frappe_manager.templates.vscode")

VSCODE_LAUNCH_JSON: dict = json.loads((_vscode_templates / "launch.json").read_text())
VSCODE_TASKS_JSON: dict = json.loads((_vscode_templates / "tasks.json").read_text())
VSCODE_SETTINGS_JSON: dict = json.loads((_vscode_templates / "settings.json").read_text())

NON_BASH_SUPPORTED_SERVICES = ["redis-cache", "redis-queue", "adminer", "mailpit"]
