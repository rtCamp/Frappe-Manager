from pathlib import Path
from enum import Enum
import frappe_manager.consts as consts

# TODO configure this using config
# sites_dir = Path().home() / __name__.split(".")[0]
CLI_DIR = Path.home() / consts.CONFIG_DIR_NAME
CLI_FM_CONFIG_PATH = CLI_DIR / consts.CONFIG_FM_FILE_NAME
CLI_SITES_ARCHIVE = CLI_DIR / consts.CONFIG_ARCHIEVE_DIR_NAME
CLI_LOG_DIRECTORY = CLI_DIR / consts.CONFIG_LOG_DIR_NAME
CLI_BENCHES_DIRECTORY = CLI_DIR / consts.CONFIG_SITES_DIR_NAME
CLI_SERVICES_DIRECTORY = CLI_DIR / consts.CONFIG_SERVICES_DIR_NAME

CLI_SERVICES_NGINX_PROXY_DIR = CLI_SERVICES_DIRECTORY / consts.CONFIG_SERVICE_NGINX_DIR_NAME
CLI_SERVICES_NGINX_PROXY_SSL_DIR = CLI_SERVICES_NGINX_PROXY_DIR / consts.CONFIG_SERVICE_NGINX_SSL_DIR_NAME

CLI_BENCH_CONFIG_FILE_NAME = consts.CONFIG_BENCH_CONFIG_FILE_NAME
SSL_RENEW_BEFORE_DAYS = consts.SSL_RENEW_BEFORE_DAYS

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
