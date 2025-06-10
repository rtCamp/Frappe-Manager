from pathlib import Path
from typing import  List,  TYPE_CHECKING
from jinja2 import Environment, FileSystemLoader
from frappe_manager.display_manager.DisplayManager import richprint

if TYPE_CHECKING:
    from frappe_manager.site_manager.site import Site

class NginxConfigManager:
    def __init__(self, bench_path: Path, bench_name: str):
        self.bench_path = bench_path
        self.bench_name = bench_name
        self.nginx_dir = bench_path / "configs" / "nginx"
        self.nginx_conf_dir = self.nginx_dir / "conf"
        self.nginx_confd_dir = self.nginx_conf_dir / "conf.d"

        # Setup Jinja2 environment - templates are in the same directory as this file
        template_dir = Path(__file__).parent / "templates"
        self.jinja_env = Environment(loader=FileSystemLoader(template_dir))

    def setup_nginx_directories(self, compose_project):
        """Setup nginx directories by copying from nginx container"""
        from frappe_manager.utils.docker import host_run_cp
        
        richprint.change_head("Setting up nginx directories")
        
        # Create nginx directory structure
        self.nginx_dir.mkdir(parents=True, exist_ok=True)
        
        # Populate conf directory from nginx container
        nginx_image = compose_project.compose_file_manager.yml["services"]["nginx"]["image"]

        if not self.nginx_confd_dir.exists():
            host_run_cp(
                nginx_image,
                source="/etc/nginx",
                destination=str(self.nginx_conf_dir.absolute()),
                docker=compose_project.docker,
            )

        # Create additional nginx subdirectories
        nginx_subdirs = ["logs", "cache", "run", "html"]
        for directory in nginx_subdirs:
            new_dir = self.nginx_dir / directory
            new_dir.mkdir(parents=True, exist_ok=True)
        
        richprint.print("Setup nginx directories")

    def generate_site_config(self, site: 'Site') -> Path:
        """Generate nginx config for a specific site"""
        template = self.jinja_env.get_template("site.conf.j2")
        
        config_content = template.render(
            site_name=site.name,
            bench_name=self.bench_name
        )
        
        config_file = self.nginx_confd_dir / f"{site.name}.conf"
        config_file.write_text(config_content)
        return config_file

    def generate_main_config(self, sites: List['Site']) -> Path:
        """Generate main nginx config that includes all sites"""
        template = self.jinja_env.get_template("main.conf.j2")
        
        # Get default site from bench - sites[0] is fallback
        default_site = None
        if sites:
            # This will be called from bench context, so we can access bench
            # For now, use first site as default fallback
            default_site = sites[0]
        
        config_content = template.render(
            bench_name=self.bench_name,
            sites=sites,
            default_site=default_site
        )
        
        config_file = self.nginx_confd_dir / "default.conf"
        config_file.write_text(config_content)
        return config_file

    def remove_site_config(self, site_name: str):
        """Remove nginx config for a site"""
        config_file = self.nginx_confd_dir / f"{site_name}.conf"
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
