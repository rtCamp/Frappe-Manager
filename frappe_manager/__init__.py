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

# Container-side bench layout: the CONTRACT with the frappe Docker image
# (Docker/frappe/Dockerfile pins /workspace/frappe-bench; runtime.Dockerfile COPYs to it).
# Versioned migrations (migration_manager/migrations/migrate_*.py) deliberately do NOT
# use these: a migration is a time capsule and must keep writing the paths of its era.
CONTAINER_BENCH_DIR = "/workspace/frappe-bench"
CONTAINER_SITES_DIR = f"{CONTAINER_BENCH_DIR}/sites"
BENCH_PYTHON = f"{CONTAINER_BENCH_DIR}/env/bin/python"
COMMON_SITE_CONFIG_FILE = "common_site_config.json"
CLI_BENCH_CONFIG_FILE_NAME = "bench_config.toml"

# Container-side bench layout: the CONTRACT with the frappe Docker image
# (Docker/frappe/Dockerfile pins /workspace/frappe-bench; runtime.Dockerfile COPYs to it).
# Versioned migrations (migration_manager/migrations/migrate_*.py) deliberately do NOT
# use these: a migration is a time capsule and must keep writing the paths of its era.
CONTAINER_BENCH_DIR = "/workspace/frappe-bench"
CONTAINER_SITES_DIR = f"{CONTAINER_BENCH_DIR}/sites"
BENCH_PYTHON = f"{CONTAINER_BENCH_DIR}/env/bin/python"
COMMON_SITE_CONFIG_FILE = "common_site_config.json"
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

# Commands that must not trigger the first-install prefetch of the stock image set.
# The prefetch exists so a first `fm create` does not stall halfway through pulling
# frappe, nginx, two redis, mariadb, nginx-proxy, mailpit and adminer. `fm bake` runs
# none of those: it builds an image, pulling only the base image it is told to build
# FROM. On a CI runner that prefetch is a per-job tax for images the job never runs.
STOCK_IMAGE_PREFETCH_SKIP_COMMANDS: frozenset[str] = frozenset({"bake"})


class EnableDisableOptionsEnum(str, Enum):
    enable = "enable"
    disable = "disable"
