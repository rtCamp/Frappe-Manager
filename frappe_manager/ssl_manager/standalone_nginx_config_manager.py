"""
Manages standalone nginx server blocks for external domains without backends.

This module creates static nginx configurations for domains that use FM's SSL
management but don't have Frappe benches as backends. It handles:
- Creating server blocks for HTTP-01 ACME challenges
- Serving .well-known/acme-challenge directory
- Providing placeholder responses until backend is connected
"""

from pathlib import Path


class StandaloneNginxConfigManager:
    """
    Manages nginx server block configurations for external (standalone) domains.

    External domains are those that use FM's SSL infrastructure but don't have
    Frappe benches. Since nginx-proxy only generates configs for containers with
    VIRTUAL_HOST, we need to manually create server blocks for these domains.

    This enables:
    - HTTP-01 ACME challenge support (serves .well-known/acme-challenge)
    - SSL certificate installation without requiring a backend container
    - Graceful handling when backend isn't connected yet

    Attributes:
        conf_dir: Directory where standalone nginx configs are stored
        webroot_dir: Path to webroot for ACME challenges (container path)
        certs_dir: Path to SSL certificates directory (container path)
    """

    # Template for standalone domain server block (HTTP only, for ACME challenges)
    HTTP_SERVER_TEMPLATE = """# Standalone domain: {domain}
# Managed by Frappe Manager
# This configuration allows HTTP-01 ACME challenge for SSL certificate generation

server {{
    server_name {domain};
    listen 80;
    access_log /var/log/nginx/access.log;
    
    # Serve ACME challenge files for Let's Encrypt validation
    location ^~ /.well-known/acme-challenge/ {{
        default_type "text/plain";
        root {webroot_dir};
    }}
    
    # Default response for all other requests
    location / {{
        return 503 '<html><head><title>503 Backend Not Connected</title></head><body><h1>503 Backend Not Connected</h1><p>This domain is managed by Frappe Manager but no backend service is configured.</p><p>To connect your Docker service:</p><ol><li>Add to your docker-compose.yml:</li><pre>services:\n  your-app:\n    environment:\n      VIRTUAL_HOST: {domain}\n      VIRTUAL_PORT: 80\n    networks:\n      - fm-global-frontend-network\n\nnetworks:\n  fm-global-frontend-network:\n    external: true</pre><li>Start your service: <code>docker compose up -d</code></li></ol></body></html>';
        add_header Content-Type text/html;
    }}
}}
"""

    # Template for standalone domain with SSL
    HTTPS_SERVER_TEMPLATE = """# Standalone domain: {domain}
# Managed by Frappe Manager
# This configuration provides SSL termination without requiring a backend

server {{
    server_name {domain};
    listen 80;
    access_log /var/log/nginx/access.log;
    
    # Serve ACME challenge files for Let's Encrypt validation
    location ^~ /.well-known/acme-challenge/ {{
        default_type "text/plain";
        root {webroot_dir};
    }}
    
    # Redirect all other HTTP traffic to HTTPS
    location / {{
        return 301 https://$host$request_uri;
    }}
}}

server {{
    server_name {domain};
    listen 443 ssl;
    http2 on;
    access_log /var/log/nginx/access.log;
    
    ssl_session_timeout 5m;
    ssl_session_cache shared:SSL:50m;
    ssl_session_tickets off;
    
    ssl_certificate {certs_dir}/{domain}.crt;
    ssl_certificate_key {certs_dir}/{domain}.key;
    
    # Serve ACME challenge files (for renewals)
    location ^~ /.well-known/acme-challenge/ {{
        default_type "text/plain";
        root {webroot_dir};
    }}
    
    # Default response for all other requests
    location / {{
        return 503 '<html><head><title>503 Backend Not Connected</title></head><body><h1>503 Backend Not Connected</h1><p>This domain has a valid SSL certificate but no backend service is configured.</p><p>To connect your Docker service:</p><ol><li>Add to your docker-compose.yml:</li><pre>services:\n  your-app:\n    environment:\n      VIRTUAL_HOST: {domain}\n      VIRTUAL_PORT: 80\n    networks:\n      - fm-global-frontend-network\n\nnetworks:\n  fm-global-frontend-network:\n    external: true</pre><li>Start your service: <code>docker compose up -d</code></li><li>Access at: https://{domain}</li></ol></body></html>';
        add_header Content-Type text/html;
    }}
}}
"""

    def __init__(self, conf_dir: Path, webroot_dir_container: str, certs_dir_container: str):
        """
        Initialize the standalone nginx config manager.

        Args:
            conf_dir: Directory to store standalone nginx configs (host filesystem)
            webroot_dir_container: Path to webroot in container (/usr/share/nginx/html)
            certs_dir_container: Path to certs in container (/etc/nginx/certs)
        """
        self.conf_dir = conf_dir
        self.webroot_dir = webroot_dir_container
        self.certs_dir = certs_dir_container

        # Ensure config directory exists
        self.conf_dir.mkdir(parents=True, exist_ok=True)

    def create_http_config(self, domain: str) -> Path:
        """
        Create HTTP-only nginx config for a standalone domain.

        This is used during initial certificate generation to serve ACME challenges.
        After certificate is generated, call create_https_config() to enable SSL.

        Args:
            domain: Domain name

        Returns:
            Path to created config file
        """
        config_file = self.conf_dir / f"{domain}.conf"

        config_content = self.HTTP_SERVER_TEMPLATE.format(
            domain=domain,
            webroot_dir=self.webroot_dir,
        )

        config_file.write_text(config_content)
        return config_file

    def create_https_config(self, domain: str) -> Path:
        """
        Create HTTPS nginx config for a standalone domain.

        This should be called after the SSL certificate is successfully generated.
        It creates a full server block with SSL termination and HTTP→HTTPS redirect.

        Args:
            domain: Domain name

        Returns:
            Path to created config file
        """
        config_file = self.conf_dir / f"{domain}.conf"

        config_content = self.HTTPS_SERVER_TEMPLATE.format(
            domain=domain,
            webroot_dir=self.webroot_dir,
            certs_dir=self.certs_dir,
        )

        config_file.write_text(config_content)
        return config_file

    def remove_config(self, domain: str) -> bool:
        """
        Remove nginx config for a standalone domain.

        Args:
            domain: Domain name

        Returns:
            True if config was removed, False if it didn't exist
        """
        config_file = self.conf_dir / f"{domain}.conf"

        if config_file.exists():
            config_file.unlink()
            return True
        return False

    def config_exists(self, domain: str) -> bool:
        """
        Check if a config file exists for a domain.

        Args:
            domain: Domain name

        Returns:
            True if config exists, False otherwise
        """
        config_file = self.conf_dir / f"{domain}.conf"
        return config_file.exists()

    def get_config_path(self, domain: str) -> Path:
        """
        Get the path to the config file for a domain.

        Args:
            domain: Domain name

        Returns:
            Path to the config file (may not exist)
        """
        return self.conf_dir / f"{domain}.conf"
