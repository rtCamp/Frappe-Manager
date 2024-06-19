from enum import Enum
import os
from frappe_manager.services_manager.database_service_manager import DatabaseServerServiceInfo
import tomlkit
from pathlib import Path
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field
from frappe_manager import STABLE_APP_BRANCH_MAPPING_LIST
from frappe_manager.metadata_manager import FMConfigManager
from frappe_manager.ssl_manager import LETSENCRYPT_PREFERRED_CHALLENGE, SUPPORTED_SSL_TYPES
from frappe_manager.ssl_manager.certificate import SSLCertificate
from frappe_manager.ssl_manager.letsencrypt_certificate import LetsencryptSSLCertificate
from frappe_manager.utils.helpers import get_container_name_prefix


class FMBenchEnvType(str, Enum):
    prod = 'prod'
    dev = 'dev'


def ssl_certificate_to_toml_doc(cert: SSLCertificate) -> Optional[tomlkit.TOMLDocument]:
    if cert.ssl_type == SUPPORTED_SSL_TYPES.none:
        return None

    model_dict = cert.model_dump(exclude=cert.toml_exclude, exclude_none=True)
    toml_doc = tomlkit.document()

    for key, value in model_dict.items():
        if isinstance(value, Path):
            toml_doc[key] = str(value.absolute())
        else:
            toml_doc[key] = value
    return toml_doc


class BenchConfig(BaseModel):
    name: str = Field(..., description="The name of the bench")
    developer_mode: bool = Field(..., description="Whether developer mode is enabled")
    admin_tools: bool = Field(..., description="Whether admin tools are enabled")
    environment_type: FMBenchEnvType = Field(..., description="The type of environment")
    ssl: SSLCertificate = Field(..., description="The SSL certificate")

    frappe_branch: str = Field(
        default=STABLE_APP_BRANCH_MAPPING_LIST['frappe'], description="The branch of Frappe to use"
    )
    admin_pass: str = Field('admin', description="The admin password")
    root_path: Path = Field(..., description="The root path")
    apps_list: List[Dict[str, Optional[str]]] = Field(default=[], description="List of apps")
    userid: int = Field(default_factory=os.getuid, description="The user ID of the current process")
    usergroup: int = Field(default_factory=os.getgid, description="The group ID of the current process")

    @property
    def db_name(self):
        return self.name.replace(".", "-")

    @property
    def container_name_prefix(self):
        return get_container_name_prefix(self.name)

    def export_to_toml(self, path: Path) -> bool:
        ssl_toml_doc: Optional[tomlkit.TOMLDocument] = None

        # check if it's SSLCertificate if not then its
        ssl_toml_doc = ssl_certificate_to_toml_doc(self.ssl)

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

        if ssl_toml_doc is None:
            exclude.add('ssl')

        # Convert the BenchConfig instance to a dictionary
        bench_dict = self.model_dump(exclude=exclude, exclude_none=True)

        if ssl_toml_doc:
            bench_dict['ssl'] = ssl_toml_doc

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
        data = tomlkit.parse(path.read_text())

        data['root_path'] = str(path)

        ssl_data = data.get('ssl', None)

        if ssl_data:
            domain: str = data.get('name', None)  # Set domain from main data if necessary
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

                ssl_instance = LetsencryptSSLCertificate(
                    domain=domain,
                    ssl_type=ssl_type,
                    email=email,
                    preferred_challenge=preferred_challenge,
                    api_key=api_key,
                    api_token=api_token,
                )
            else:
                ssl_instance = SSLCertificate(domain=domain, ssl_type=SUPPORTED_SSL_TYPES.none)
        else:
            ssl_instance = SSLCertificate(domain=data.get('name', None), ssl_type=SUPPORTED_SSL_TYPES.none)

        input_data = {
            'name': data.get('name', None),
            'developer_mode': data.get('developer_mode', None),
            'admin_tools': data.get('admin_tools', False),
            'environment_type': data.get('environment_type', None),
            'root_path': data.get('root_path', None),
            'ssl': ssl_instance,
        }

        bench_config_instance = cls(**input_data)

        return bench_config_instance

    def get_commmon_site_config_data(self, db_server_info: DatabaseServerServiceInfo) -> Dict[str, Any]:
        common_site_config_data = {
            "install_apps": [],
            "db_host": db_server_info.host,
            "db_port": db_server_info.port,
            "redis_cache": f"redis://{self.container_name_prefix}-redis-cache:6379",
            "redis_queue": f"redis://{self.container_name_prefix}-redis-queue:6379",
            "redis_socketio": f"redis://{self.container_name_prefix}-redis-socketio:6379",
            "webserver_port": 80,
            "socketio_port": 80,
            "restart_supervisor_on_update": 0,
            "developer_mode": self.developer_mode,
        }

        return common_site_config_data

    def export_to_compose_inputs(self):
        environment = {
            "frappe": {
                "USERID": self.userid,
                "USERGROUP": self.usergroup,
            },
            "nginx": {
                "SITENAME": self.name,
                "VIRTUAL_HOST": self.name,
                "VIRTUAL_PORT": 80,
                "HSTS": self.ssl.hsts,
            },
            "worker": {
                "USERID": self.userid,
                "USERGROUP": self.usergroup,
            },
            "schedule": {
                "USERID": self.userid,
                "USERGROUP": self.usergroup,
            },
            "socketio": {
                "USERID": self.userid,
                "USERGROUP": self.usergroup,
            },
        }

        users: dict = {"nginx": {"uid": self.userid, "gid": self.usergroup}}
        template_inputs: dict = {
            "environment": environment,
            "user": users,
        }
        return template_inputs
