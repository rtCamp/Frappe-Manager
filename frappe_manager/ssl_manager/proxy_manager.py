from typing import Protocol

from click import Path
from frappe_manager.services_manager.services import ServicesManager


class ProxyManager(Protocol):
    services: ServicesManager
    root_dir: Path
    sub_dirs: 
