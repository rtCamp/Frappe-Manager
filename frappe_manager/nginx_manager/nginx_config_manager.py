from pathlib import Path
from typing import Dict, List, Optional, TYPE_CHECKING
from jinja2 import Environment, FileSystemLoader
from frappe_manager.display_manager.DisplayManager import richprint

if TYPE_CHECKING:
    from frappe_manager.site_manager.site import Site

class NginxConfigManager:
    def __init__(self, bench_path: Path, bench_name: str):
        self.bench_path = bench_path
        self.bench_name = bench_name
        self.nginx_config_dir = bench_path / "configs" / "nginx" / "conf"
        self.nginx_config_dir.mkdir(parents=True, exist_ok=True)
        
        # Setup Jinja2 environment - templates are in the same directory as this file
        template_dir = Path(__file__).parent / "templates"
        self.jinja_env = Environment(loader=FileSystemLoader(template_dir))

    def generate_site_config(self, site: 'Site') -> Path:
        """Generate nginx config for a specific site"""
        template = self.jinja_env.get_template("site.conf.j2")
        
        config_content = template.render(
            site_name=site.name,
            bench_name=self.bench_name
        )
        
        config_file = self.nginx_config_dir / f"{site.name}.conf"
        config_file.write_text(config_content)
        return config_file

    def generate_main_config(self, sites: List['Site']) -> Path:
        """Generate main nginx config that includes all sites"""
        template = self.jinja_env.get_template("main.conf.j2")
        
        config_content = template.render(
            bench_name=self.bench_name,
            sites=sites,
            default_site=sites[0] if sites else None
        )
        
        config_file = self.nginx_config_dir / "default.conf"
        config_file.write_text(config_content)
        return config_file

    def remove_site_config(self, site_name: str):
        """Remove nginx config for a site"""
        config_file = self.nginx_config_dir / f"{site_name}.conf"
        if config_file.exists():
            config_file.unlink()
            richprint.print(f"Removed nginx config for {site_name}")


    def reload_nginx(self, compose_project):
        """Reload nginx configuration"""
        try:
            compose_project.docker.compose.exec(
                service="nginx",
                command="nginx -s reload",
                stream=False
            )
            richprint.print("Reloaded nginx configuration")
        except Exception as e:
            richprint.warning(f"Failed to reload nginx: {str(e)}")
