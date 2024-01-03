from pathlib import Path
from enum import Enum

# TODO configure this using config
#sites_dir = Path().home() / __name__.split(".")[0]
CLI_DIR = Path.home() / 'frappe'

default_extension = [
    "dbaeumer.vscode-eslint",
    "esbenp.prettier-vscode",
    "ms-python.python",
    "ms-python.black-formatter",
    "ms-python.flake8",
    "visualstudioexptteam.vscodeintellicode",
    "VisualStudioExptTeam.intellicode-api-usage-examples"
]

# TODO Make it dynamic using compose file template
class SiteServicesEnum(str, Enum):
    frappe= "frappe"
    nginx = "nginx"
    mailhog = "mailhog"
    adminer = "adminer"
    mariadb = "mariadb"
    schedule = "schedule"
    socketio = "socketio"
    redis_queue = "redis-queue"
    redis_cache = "redis-cache"
    redis_socketio = "redis-socketio"
