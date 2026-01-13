from enum import Enum
import os
from frappe_manager.services_manager.database_service_manager import DatabaseServerServiceInfo
import tomlkit
from tomlkit.items import Array as TOMLArray
from pathlib import Path
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field
from frappe_manager import CLI_DEFAULT_DELIMETER, STABLE_APP_BRANCH_MAPPING_LIST
from frappe_manager.metadata_manager import FMConfigManager
from frappe_manager.ssl_manager import LETSENCRYPT_PREFERRED_CHALLENGE, SUPPORTED_SSL_TYPES
from frappe_manager.ssl_manager.certificate import SSLCertificate
from frappe_manager.ssl_manager.letsencrypt_certificate import LetsencryptSSLCertificate
from frappe_manager.utils.helpers import get_container_name_prefix


class FMBenchEnvType(str, Enum):
    prod = 'prod'
    dev = 'dev'


def ssl_certificate_from_toml_data(ssl_data: Dict, domain: str) -> SSLCertificate:
    """Parse a single certificate from TOML data."""
    ssl_type = ssl_data.get('ssl_type', SUPPORTED_SSL_TYPES.none)

    if ssl_type == SUPPORTED_SSL_TYPES.le:
        email = ssl_data.get('email', None)

        fm_config_manager = FMConfigManager.import_from_toml()

        pref_challenge_data = ssl_data.get("preferred_challenge", None)

        api_token = ssl_data.get('api_token', None)
        if not api_token:
            api_token = fm_config_manager.letsencrypt.api_token

        api_key = ssl_data.get('api_key', None)
        if not api_key:
            api_key = fm_config_manager.letsencrypt.api_key

        if not pref_challenge_data:
            if fm_config_manager.letsencrypt.exists:
                preferred_challenge = LETSENCRYPT_PREFERRED_CHALLENGE.dns01
            else:
                preferred_challenge = LETSENCRYPT_PREFERRED_CHALLENGE.http01
        else:
            preferred_challenge = pref_challenge_data

        # Read acme_client field (defaults to "certbot" if not specified)
        acme_client = ssl_data.get('acme_client', 'certbot')

        return LetsencryptSSLCertificate(
            domain=domain,
            ssl_type=ssl_type,
            email=email,
            preferred_challenge=preferred_challenge,
            api_key=api_key,
            api_token=api_token,
            acme_client=acme_client,
        )
    else:
        return SSLCertificate(domain=domain, ssl_type=SUPPORTED_SSL_TYPES.none)


def ssl_certificate_to_toml_doc(cert: SSLCertificate) -> Optional[tomlkit.TOMLDocument]:
    """Convert a single certificate to TOML document."""
    if cert.ssl_type == SUPPORTED_SSL_TYPES.none:
        return None

    # Explicitly exclude only the computed field, but INCLUDE domain
    model_dict = cert.model_dump(exclude={'toml_exclude'}, exclude_none=True)
    toml_doc = tomlkit.document()

    for key, value in model_dict.items():
        if isinstance(value, Path):
            toml_doc[key] = str(value.absolute())
        else:
            toml_doc[key] = value
    return toml_doc


def ssl_certificates_to_toml_array(certs: List[SSLCertificate]) -> TOMLArray:
    """Convert a list of certificates to TOML array-of-tables."""
    # Use aot() for array-of-tables format [[ssl_certificates]]
    toml_aot = tomlkit.aot()
    for cert in certs:
        if cert.ssl_type != SUPPORTED_SSL_TYPES.none:
            cert_doc = ssl_certificate_to_toml_doc(cert)
            if cert_doc:
                toml_aot.append(cert_doc)
    return toml_aot


class BenchConfig(BaseModel):
    name: str = Field(..., description="The name of the bench")
    developer_mode: bool = Field(..., description="Whether developer mode is enabled")
    admin_tools: bool = Field(..., description="Whether admin tools are enabled")
    environment_type: FMBenchEnvType = Field(..., description="The type of environment")

    # Multi-certificate support
    ssl_certificates: List[SSLCertificate] = Field(default=[], description="List of SSL certificates for this bench")

    alias_domains: List[str] = Field(default=[], description="List of alias domains for the bench")

    frappe_branch: str = Field(
        default=STABLE_APP_BRANCH_MAPPING_LIST['frappe'], description="The branch of Frappe to use"
    )
    admin_pass: str = Field('admin', description="The admin password")
    root_path: Path = Field(..., description="The root path")
    apps_list: List[Dict[str, Optional[str]]] = Field(default=[], description="List of apps")
    userid: int = Field(default_factory=os.getuid, description="The user ID of the current process")
    usergroup: int = Field(default_factory=os.getgid, description="The group ID of the current process")
    admin_tools_username: Optional[str] = Field(None, description="Username for admin tools basic auth")
    admin_tools_password: Optional[str] = Field(None, description="Password for admin tools basic auth")

    def get_primary_certificate(self) -> SSLCertificate:
        """
        Get the primary SSL certificate.

        Returns the first certificate in ssl_certificates list, or creates a default
        disabled certificate if the list is empty.
        """
        if self.ssl_certificates:
            return self.ssl_certificates[0]

        # Return default disabled certificate
        return SSLCertificate(domain=self.name, ssl_type=SUPPORTED_SSL_TYPES.none)

    def set_primary_certificate(self, certificate: SSLCertificate):
        """
        Set the primary SSL certificate.

        Updates the first certificate in the list, or creates a new list with this certificate.
        """
        if self.ssl_certificates:
            self.ssl_certificates[0] = certificate
        else:
            self.ssl_certificates = [certificate]

    def create_individual_certificates(self, template_certificate: SSLCertificate) -> None:
        """
        Create individual certificate entries for primary domain and all alias domains.

        This replaces any existing certificates with individual certificate entries
        for each domain (primary + aliases), using the template certificate as a base.

        Args:
            template_certificate: Certificate configuration to use as template
                                 (email, acme_client, ssl_type, etc.)
        """
        from copy import deepcopy

        new_certificates = []

        # Create certificate for primary domain
        primary_cert = deepcopy(template_certificate)
        primary_cert.domain = self.name
        new_certificates.append(primary_cert)

        # Create individual certificates for each alias domain
        for alias_domain in self.alias_domains:
            alias_cert = deepcopy(template_certificate)
            alias_cert.domain = alias_domain
            new_certificates.append(alias_cert)

        # Replace all certificates with individual ones
        self.ssl_certificates = new_certificates

    @property
    def db_name(self):
        return self.name.replace(".", "-")

    @property
    def container_name_prefix(self):
        return get_container_name_prefix(self.name)

    def export_to_toml(self, path: Path) -> bool:
        """
        Export bench configuration to TOML file.
        """
        exclude = {
            'root_path',
            'mariadb_root_pass',
            'userid',
            'mariadb_host',
            'usergroup',
            'apps_list',
            'frappe_branch',
            'admin_pass',
        }

        # Convert the BenchConfig instance to a dictionary
        bench_dict = self.model_dump(exclude=exclude, exclude_none=True)

        # Handle SSL certificates
        if self.ssl_certificates:
            # Export as array
            certs_array = ssl_certificates_to_toml_array(self.ssl_certificates)
            if len(certs_array) > 0:
                bench_dict['ssl_certificates'] = certs_array
            else:
                # No active certificates, remove the key
                bench_dict.pop('ssl_certificates', None)
        else:
            # No certificates at all
            bench_dict.pop('ssl_certificates', None)

        # Serialize the dictionary to a TOML string
        # Create a TOML document from the dictionary
        toml_doc = tomlkit.document()

        for key, value in bench_dict.items():
            if isinstance(value, Path):
                toml_doc[key] = str(value.absolute())
            else:
                toml_doc[key] = value
        try:
            with open(path, 'w') as f:
                f.write(tomlkit.dumps(toml_doc))
            return True
        except Exception as e:
            return False

    @classmethod
    def import_from_toml(cls, path: Path) -> "BenchConfig":
        """
        Import bench configuration from TOML file.

        Automatically migrates legacy single-certificate format (ssl key) to new
        multi-certificate format (ssl_certificates array).
        """
        data = tomlkit.parse(path.read_text())
        data['root_path'] = str(path)

        domain: str = data.get('name', '')
        ssl_certificates_list: List[SSLCertificate] = []

        # Check for new multi-cert format
        ssl_certificates_data = data.get('ssl_certificates', None)
        if ssl_certificates_data and isinstance(ssl_certificates_data, list):
            # Parse multi-certificate format
            for cert_data in ssl_certificates_data:
                cert_domain = cert_data.get('domain', domain)
                ssl_cert = ssl_certificate_from_toml_data(cert_data, cert_domain)
                ssl_certificates_list.append(ssl_cert)

        # Check for legacy single-cert format and migrate it
        ssl_data = data.get('ssl', None)
        if ssl_data and not ssl_certificates_list:
            # Migrate from legacy format
            ssl_cert = ssl_certificate_from_toml_data(ssl_data, domain)
            ssl_certificates_list.append(ssl_cert)

        # If no certificates found at all, start with empty list
        # (default cert will be created via get_primary_certificate() when needed)

        # Read alias_domains from root level only
        alias_domains_list = data.get('alias_domains', [])

        input_data = {
            'name': data.get('name', None),
            'developer_mode': data.get('developer_mode', None),
            'admin_tools': data.get('admin_tools', False),
            'environment_type': data.get('environment_type', None),
            'root_path': data.get('root_path', None),
            'ssl_certificates': ssl_certificates_list,
            'alias_domains': alias_domains_list,
            'admin_tools_username': data.get('admin_tools_username', None),
            'admin_tools_password': data.get('admin_tools_password', None),
        }

        bench_config_instance = cls(**input_data)
        return bench_config_instance

    def get_commmon_site_config_data(self, db_server_info: DatabaseServerServiceInfo) -> Dict[str, Any]:
        common_site_config_data = {
            "install_apps": [],
            "db_host": db_server_info.host,
            "db_port": db_server_info.port,
            "redis_cache": f"redis://{self.container_name_prefix}{CLI_DEFAULT_DELIMETER}redis-cache:6379",
            "redis_queue": f"redis://{self.container_name_prefix}{CLI_DEFAULT_DELIMETER}redis-queue:6379",
            "redis_socketio": f"redis://{self.container_name_prefix}{CLI_DEFAULT_DELIMETER}redis-cache:6379",
            "webserver_port": 80,
            "socketio_port": 80,
            "restart_supervisor_on_update": 0,
            "developer_mode": self.developer_mode,
        }

        return common_site_config_data

    def export_to_compose_inputs(self):
        # Build domains list: primary domain + alias domains
        all_domains = [self.name]
        if self.alias_domains:
            all_domains.extend(self.alias_domains)
        domains_string = ','.join(all_domains)

        environment = {
            "frappe": {
                "USERID": self.userid,
                "USERGROUP": self.usergroup,
                "SERVICE_NAME": "frappe",
            },
            "nginx": {
                "SITENAME": domains_string,
                "VIRTUAL_HOST": domains_string,
                "VIRTUAL_PORT": 80,
                "HSTS": self.get_primary_certificate().hsts,
            },
            "worker": {
                "USERID": self.userid,
                "USERGROUP": self.usergroup,
            },
            "schedule": {
                "USERID": self.userid,
                "USERGROUP": self.usergroup,
                "SERVICE_NAME": "schedule",
            },
            "socketio": {
                "USERID": self.userid,
                "USERGROUP": self.usergroup,
                "SERVICE_NAME": "socketio",
            },
        }

        users: dict = {"nginx": {"uid": self.userid, "gid": self.usergroup}}
        template_inputs: dict = {
            "environment": environment,
            "user": users,
        }
        return template_inputs
