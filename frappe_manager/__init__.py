import os
from enum import Enum
from pathlib import Path
from typing import Optional

import typer.rich_utils as ut
from rich.panel import Panel
from typer.core import TyperCommand

from frappe_manager.utils.cli_examples import get_examples_from_toml

# patch rich_format_help to display examples Panel
# save the function so that recurssion doesn't occur
rich_format_help_original = ut.rich_format_help


def print_fm_examples(*, obj, ctx, markup_mode):
    import inspect
    import sys

    rich_format_help_original(obj=obj, ctx=ctx, markup_mode=markup_mode)

    commands_stack = ctx.command_path.split(" ")[1:]

    example_data = {"benchname": "mybench", "domain": "example.com"}

    command_param_names = []
    if hasattr(obj, "callback") and obj.callback:
        sig = inspect.signature(obj.callback)
        command_param_names = [p for p in sig.parameters.keys() if p not in ["ctx", "return"]]

    if ctx.params:
        for param_name in command_param_names:
            if ctx.params.get(param_name):
                example_data[param_name] = ctx.params[param_name]

    if "--help" in sys.argv:
        help_index = sys.argv.index("--help")
        args_before_help = []

        for i in range(1, help_index):
            arg = sys.argv[i]
            if not arg.startswith("-") and arg not in commands_stack:
                args_before_help.append(arg)

        for i, param_name in enumerate(command_param_names):
            if i < len(args_before_help):
                example_data[param_name] = args_before_help[i]

    new_doc = get_examples_from_toml(
        commands_stack=commands_stack,
        frappe_version=STABLE_APP_BRANCH_MAPPING_LIST["frappe"],
        example_data=example_data,
    )

    if new_doc:
        import rich

        rich.print(
            Panel(
                new_doc,
                padding=ut.STYLE_OPTIONS_TABLE_PADDING,
                border_style=ut.STYLE_OPTIONS_PANEL_BORDER,
                title="Examples",
                title_align=ut.ALIGN_OPTIONS_PANEL,
            ),
        )


ut.rich_format_help = print_fm_examples

CLI_DIR = Path(os.environ.get("FRAPPE_MANAGER_HOME", Path.home() / "frappe"))
CLI_FM_CONFIG_PATH = CLI_DIR / "fm_config.toml"
CLI_SITES_ARCHIVE = CLI_DIR / "archived"
CLI_LOG_DIRECTORY = CLI_DIR / "logs"
CLI_BENCHES_DIRECTORY = CLI_DIR / "sites"
CLI_SERVICES_DIRECTORY = CLI_DIR / "services"
CLI_CACHE_PATH = Path.home() / ".cache" / "fm"
CLI_RECENT_USED_SITES_CACHE_PATH = CLI_CACHE_PATH / "recent_sites.json"

CLI_SERVICES_NGINX_PROXY_DIR = CLI_SERVICES_DIRECTORY / "nginx-proxy"
CLI_SERVICES_NGINX_PROXY_SSL_DIR = CLI_SERVICES_NGINX_PROXY_DIR / "ssl"

CLI_BENCH_CONFIG_FILE_NAME = "bench_config.toml"
SSL_RENEW_BEFORE_DAYS = 30
CLI_DEFAULT_DELIMETER = "__"
CLI_SITE_NAME_DELIMETER = "_"


DEFAULT_EXTENSIONS = [
    # Debugger
    "ms-python.debugpy",
    "rioj7.command-variable",
    # Python
    "ms-python.python",
    "charliermarsh.ruff",
    # JavaScript/Web
    "dbaeumer.vscode-eslint",
    "esbenp.prettier-vscode",
]


class SiteServicesEnum(str, Enum):
    frappe = "frappe"
    nginx = "nginx"
    mariadb = "mariadb"
    redis_queue = "redis-queue"
    redis_cache = "redis-cache"
    schedule = "schedule"
    socketio = "socketio"
    default_worker = "default-worker"
    short_worker = "short-worker"
    long_worker = "long-worker"
    adminer = "adminer"
    mailpit = "mailpit"


STABLE_APP_BRANCH_MAPPING_LIST = {
    "frappe": "version-16",
    "erpnext": "version-16",
    "hrms": "version-16",
}


class EnableDisableOptionsEnum(str, Enum):
    enable = "enable"
    disable = "disable"
