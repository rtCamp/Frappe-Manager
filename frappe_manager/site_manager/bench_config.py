from enum import Enum
import tomlkit
from pathlib import Path

from typing import List, Optional, Union
from pydantic import BaseModel, validator
from frappe_manager.compose_manager.ComposeFile import ComposeFile
from frappe_manager.ssl_manager import SUPPORTED_SSL_TYPES
from frappe_manager.ssl_manager.certificate import RenewableSSLCertificate, SSLCertificate
from frappe_manager.ssl_manager.renewable_certificate import RenewableLetsencryptSSLCertificate
from frappe_manager.utils.helpers import get_container_name_prefix


class FMBenchEnvType(str, Enum):
    prod = 'prod'
    dev = 'dev'


def ssl_certificate_to_toml_doc(cert: Union[SSLCertificate, RenewableSSLCertificate]) -> Optional[tomlkit.TOMLDocument]:
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
    name: str
    userid: int
    usergroup: int
    apps_list: List[str]
    frappe_branch: str
    developer_mode: bool
    admin_tools: bool
    admin_pass: str
    mariadb_host: str
    mariadb_root_pass: str
    environment_type: FMBenchEnvType
    root_path: Path
    ssl: Union[SSLCertificate, RenewableSSLCertificate]

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

        exclude = {'root_path'}

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

        # Extract SSL data and remove it from the main data dictionary
        ssl_data = data.get('ssl', None)

        if ssl_data:
            ssl_data['domain'] = data.get('name', 'default')  # Set domain from main data if necessary
            if 'fullchain_path' in ssl_data:  # Assuming presence of 'privkey_path' indicates a RenewableSSLCertificate
                ssl_instance = RenewableLetsencryptSSLCertificate(**ssl_data)
            else:
                ssl_instance = SSLCertificate(**ssl_data)
        else:
            ssl_instance = SSLCertificate(domain=data.get('name', None), ssl_type=SUPPORTED_SSL_TYPES.none)

        input_data = {
            'name': data.get('name', None),
            'userid': data.get('userid', None),
            'usergroup': data.get('usergroup', None),
            'apps_list': data.get('apps_list', []),
            'frappe_branch': data.get('frappe_branch', None),
            'developer_mode': data.get('developer_mode', None),
            'admin_tools': data.get('admin_tools', False),
            'admin_pass': data.get('admin_pass', None),
            'mariadb_host': data.get('mariadb_host', None),
            'mariadb_root_pass': data.get('mariadb_root_pass', None),
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
