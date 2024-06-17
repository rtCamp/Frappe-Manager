from pathlib import Path
from typing import Final

# TODO configure this using config
# sites_dir = Path().home() / __name__.split(".")[0]
CONFIG_DIR_NAME: Final[str] = "frappe"
CONFIG_FM_FILE_NAME: Final[str] = "fm_config.toml"
CONFIG_ARCHIEVE_DIR_NAME: Final[str] = "archived"
CONFIG_LOG_DIR_NAME: Final[str] = 'logs'
CONFIG_SITES_DIR_NAME: Final[str] = 'sites'

CONFIG_SERVICES_DIR_NAME: Final[str] = 'services'
CONFIG_SERVICE_NGINX_DIR_NAME: Final[str] = 'nginx-proxy'
CONFIG_SERVICE_NGINX_SSL_DIR_NAME: Final[str] = 'ssl'
CONFIG_BENCH_CONFIG_FILE_NAME: Final[str] = 'bench_config.toml'
SSL_RENEW_BEFORE_DAYS: Final[int] = 30
