from enum import Enum
import os
import tomlkit
from pathlib import Path
from typing import Any, List, Optional
from pydantic import BaseModel, Field, model_validator, validator
from frappe_manager import STABLE_APP_BRANCH_MAPPING_LIST
from frappe_manager.metadata_manager import FMConfigManager, FMLetsencryptConfig
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
    mariadb_host: str = Field('global-db', description="The host for MariaDB")
    mariadb_root_pass: str = Field(default='/run/secrets/db_root_password', description="The root password for MariaDB")
    apps_list: List[str] = Field(default=[], description="List of apps")
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

    def export_to_compose_inputs(self):
        environment = {
            "frappe": {
                "USERID": self.userid,
                "USERGROUP": self.usergroup,
                "APPS_LIST": ",".join(self.apps_list) if self.apps_list else None,
                "FRAPPE_BRANCH": self.frappe_branch,
                "DEVELOPER_MODE": self.developer_mode,
                "ADMIN_PASS": self.admin_pass,
                "DB_NAME": self.db_name,
                "SITENAME": self.name,
                "MARIADB_HOST": self.mariadb_host,
                "MARIADB_ROOT_PASS": self.mariadb_root_pass,
                "CONTAINER_NAME_PREFIX": self.container_name_prefix,
                "ENVIRONMENT": self.environment_type.value,
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
