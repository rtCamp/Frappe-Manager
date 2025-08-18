from pathlib import Path
import json
from typing import TYPE_CHECKING, Optional, Dict, Any

from pydantic import config

if TYPE_CHECKING:
    from frappe_manager.site_manager.bench_ssl_manager import BenchSSLManager

class Site:
    def __init__(self, name: str, bench_path: Path , ssl_manager: 'BenchSSLManager'):
        """Initialize a site within a bench
        
        Args:
            name: Site name
            bench: Parent Bench instance that owns this site
            ssl_manager: Optional BenchSSLManager instance for SSL/cert management
        """
        self.name = name
        self.ssl_manager = ssl_manager
        self.site_dir =  bench_path / "workspace/frappe-bench/sites" / name
        self.config_path = self.site_dir / "site_config.json"

    @property
    def exists(self) -> bool:
        return self.site_dir.exists() and self.config_path.exists()

    def get_config(self) -> Dict[str, Any]:
        """Get site-specific configuration"""
        if not self.config_path.exists():
            return {}

        return json.loads(self.config_path.read_text())

    def set_config(self, config: Dict[str, Any]) -> None:
        """Update site-specific configuration"""
        current_config = self.get_config()
        current_config.update(config)
        self.config_path.write_text(json.dumps(current_config, indent=4))

    def get_db_name(self) -> Optional[str]:
        """Get database name from site config"""
        config = self.get_config()
        db_name = config.get("db_name", None)
        return db_name or self.name.replace('.','-')
