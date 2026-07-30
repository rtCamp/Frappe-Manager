import os
from enum import Enum
from pathlib import Path
from typing import Optional

# Examples are now provided using typer-examples decorators and installed per Typer app.

_home_env = os.environ.get("FRAPPE_MANAGER_HOME", "")
CLI_DIR = Path(_home_env) if _home_env else Path.home() / "frappe"
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

# The shared global-db engine. Pinned to what Frappe's own CI tests against on the
# branch above (frappe/.github/workflows/_base-server-tests.yml), which is the real
# statement of support: the soft bounds in frappe/database/mariadb/setup_db.py warn
# below 10.6 and above 11.8. Kept in sync with the `image:` line in both
# docker-compose.services templates by tests/unit/services_manager/test_global_db_image.py.
GLOBAL_DB_IMAGE = "mariadb:11.8"


class EnableDisableOptionsEnum(str, Enum):
    enable = "enable"
    disable = "disable"
