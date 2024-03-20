from pathlib import Path
from enum import Enum

# TODO configure this using config
# sites_dir = Path().home() / __name__.split(".")[0]
CLI_DIR = Path.home() / "frappe"
CLI_METADATA_PATH = CLI_DIR / ".fm.toml"
CLI_SITES_ARCHIVE = CLI_DIR / "archived"
CLI_LOG_DIRECTORY = CLI_DIR / 'logs'
CLI_SITES_DIRECTORY = CLI_DIR / 'sites'


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
    mailhog = "mailhog"
    adminer = "adminer"
    mariadb = "mariadb"
    redis_queue = "redis-queue"
    redis_cache = "redis-cache"
    redis_socketio = "redis-socketio"
    schedule = "schedule"
    socketio = "socketio"


STABLE_APP_BRANCH_MAPPING_LIST = {
    "erpnext" :'version-15',
    "hrms" :'version-15',
}
