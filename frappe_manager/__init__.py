import os
from enum import Enum
from pathlib import Path

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

# The shared mariadb engine. Pinned to what Frappe's own CI tests against on the
# branch above (frappe/.github/workflows/_base-server-tests.yml), which is the real
# statement of support: the soft bounds in frappe/database/mariadb/setup_db.py warn
# below 10.6 and above 11.8. Kept in sync with the `image:` line in both
# docker-compose.services templates by tests/unit/services_manager/test_mariadb_image.py.
MARIADB_IMAGE = "mariadb:11.8"

# Commands that must not trigger the first-install prefetch of the stock image set.
# The prefetch exists so a first `fm create` does not stall halfway through pulling
# frappe, nginx, two redis, mariadb, nginx-proxy, mailpit and adminer. `fm bake` runs
# none of those: it builds an image, pulling only the base image it is told to build
# FROM. On a CI runner that prefetch is a per-job tax for images the job never runs.
STOCK_IMAGE_PREFETCH_SKIP_COMMANDS: frozenset[str] = frozenset({"bake"})

# Commands that must keep working on a BROKEN host, keyed by full command path. A teardown is
# the one job whose preconditions are the very things it removes, so these three gates all read
# this one set: no docker daemon, a pending migration, and an unparseable fm_config.toml. The
# rule is narrow on purpose -- a command earns a place here only by being how you get fm off a
# machine, never merely by being read-only.
BROKEN_HOST_COMMANDS: frozenset[str] = frozenset({"ssl ca", "self uninstall"})

# Commands that only OBSERVE fm's state: they read configs and container states and
# mutate nothing. Two behaviors key off this set, and they must stay a pair:
# - they hold no lock, so they keep working DURING a migration (mid-cutover is exactly
#   when an operator wants to peek at what is happening);
# - they never auto-start the global stack (an observer that boots infrastructure is
#   not an observer -- and lock-free, it would boot the half-renamed stack mid-cutover).
# Keyed by full command path ("ssl list"), matching app_callback's get_full_command_path.
OBSERVE_ONLY_COMMANDS: frozenset[str] = frozenset(
    {"list", "info", "logs", "services info", "ssl list", "apps list", "domain list", "tools status"}
)

# The two commands that RUN migrations. Everything gating on "is this a migration?"
# reads this one set: the host lock (they take it EXCLUSIVE in the executor instead of
# SHARED in the callback) and the pre-rename escape hatch in the services manager.
MIGRATION_COMMANDS: frozenset[str] = frozenset({"migrate", "services migrate"})

# Command families (matched on the FIRST token of the full command path) that never
# auto-start a stopped global stack: `services`/`self` act ON the stack and must be able
# to run against one that was deliberately stopped, and `compose` is a diagnostic
# passthrough that must report a stopped stack as stopped. Observers are the other
# exemption, via OBSERVE_ONLY_COMMANDS above.
STACK_AUTOSTART_EXEMPT_PREFIXES: frozenset[str] = frozenset({"services", "self", "compose"})


class EnableDisableOptionsEnum(str, Enum):
    enable = "enable"
    disable = "disable"


class TelemetryProviderEnum(str, Enum):
    """APM backends `fm telemetry` can act on.

    One value today, but it is the command's positional argument rather than a flag so a
    second backend is a new member here and nothing else: `fm telemetry enable BENCH otel`.
    Encoding the provider in the flag name instead (the retired `fm update --newrelic`) meant
    every backend added its own pair of booleans to a command that already carried twelve.
    """

    newrelic = "newrelic"
