from pathlib import Path
from enum import Enum

# TODO configure this using config
#sites_dir = Path().home() / __name__.split(".")[0]
CLI_DIR = Path.home() / 'frappe'
CLI_METADATA_PATH = CLI_DIR / '.fm.toml'
CLI_SITES_ARCHIVE = CLI_DIR / 'archived'


default_extension = [
    "dbaeumer.vscode-eslint",
    "esbenp.prettier-vscode",
    "ms-python.python",
    "ms-python.black-formatter",
    "ms-python.flake8",
    "visualstudioexptteam.vscodeintellicode",
    "VisualStudioExptTeam.intellicode-api-usage-examples"
]

class SiteServicesEnum(str, Enum):
    frappe= "frappe"
    nginx = "nginx"
    mailhog = "mailhog"
    adminer = "adminer"
    mariadb = "mariadb"
    redis_queue = "redis-queue"
    redis_cache = "redis-cache"
    redis_socketio = "redis-socketio"
    schedule = "schedule"
    socketio = "socketio"
