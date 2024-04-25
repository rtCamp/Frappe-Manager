from pathlib import Path
from enum import Enum

# TODO configure this using config
# sites_dir = Path().home() / __name__.split(".")[0]
CLI_DIR = Path.home() / "frappe"
CLI_FM_CONFIG_PATH = CLI_DIR / "fm_config.toml"
CLI_SITES_ARCHIVE = CLI_DIR / "archived"
CLI_LOG_DIRECTORY = CLI_DIR / 'logs'
CLI_BENCHES_DIRECTORY = CLI_DIR / 'sites'
CLI_SERVICES_DIRECTORY = CLI_DIR / 'services'

CLI_SERVICES_NGINX_PROXY_DIR = CLI_SERVICES_DIRECTORY / 'nginx-proxy'
CLI_SERVICES_NGINX_PROXY_SSL_DIR = CLI_SERVICES_NGINX_PROXY_DIR / 'ssl'

CLI_BENCH_CONFIG_FILE_NAME = 'bench_config.toml'
SSL_RENEW_BEFORE_DAYS = 30


DEFAULT_EXTENSIONS = [
    "dbaeumer.vscode-eslint",
    "esbenp.prettier-vscode",
    "ms-python.python",
    "ms-python.debugpy",
    "ms-python.flake8",
    "ms-python.black-formatter",
    "visualstudioexptteam.vscodeintellicode",
    "VisualStudioExptTeam.intellicode-api-usage-examples",
]


class SiteServicesEnum(str, Enum):
    frappe = "frappe"
    nginx = "nginx"
    mariadb = "mariadb"
    redis_queue = "redis-queue"
    redis_cache = "redis-cache"
    redis_socketio = "redis-socketio"
    schedule = "schedule"
    socketio = "socketio"


STABLE_APP_BRANCH_MAPPING_LIST = {
    "frappe": 'version-15',
    "erpnext": 'version-15',
    "hrms": 'version-15',
}


class EnableDisableOptionsEnum(str, Enum):
    enable = 'enable'
    disable = 'disable'
