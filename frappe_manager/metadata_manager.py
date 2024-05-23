from typing import Optional
from pathlib import Path
from pydantic import BaseModel, EmailStr, Field
import tomlkit
from frappe_manager.migration_manager.version import Version
from frappe_manager import CLI_FM_CONFIG_PATH
from frappe_manager.utils.helpers import get_current_fm_version


class FMLetsencryptConfig(BaseModel):
    email: Optional[EmailStr] = Field(None, description="Email used by certbot.")
    api_token: Optional[str] = Field(None, description="Cloudflare API token used by Certbot.")
    api_key: Optional[str] = Field(None, description="Cloudflare Global API Key used by Certbot.")

    @property
    def exists(self):
        if self.api_token or self.api_key:
            return True

        return False

    def get_toml_doc(self):
        model_dict = self.model_dump(exclude_none=True)
        toml_doc = tomlkit.document()

        for key, value in model_dict.items():
            if isinstance(value, Path):
                toml_doc[key] = str(value.absolute())
            else:
                toml_doc[key] = value
        return toml_doc

    @classmethod
    def import_from_toml_doc(cls, toml_doc):
        config_object = cls(**toml_doc)
        return config_object


class FMConfigManager(BaseModel):
    root_path: Path
    version: Version
    letsencrypt: FMLetsencryptConfig = Field(default=FMLetsencryptConfig())

    def export_to_toml(self, path: Path = CLI_FM_CONFIG_PATH) -> bool:
        exclude = {'root_path'}

        if not self.letsencrypt.email and not self.letsencrypt.api_key and not self.letsencrypt.api_token:
            exclude.add('letsencrypt')
        else:
            if self.letsencrypt.email == 'dummy@fm.fm':
                exclude.add('letsencrypt')

        if self.version < Version('0.13.0'):
            path = CLI_FM_CONFIG_PATH.parent / '.fm.toml'

        # Convert the BenchConfig instance to a dictionary
        fm_config_dict = self.model_dump(exclude=exclude, exclude_none=True)

        # transform structure if needed
        fm_config_dict['version'] = self.version.version

        toml_doc = tomlkit.document()

        for key, value in fm_config_dict.items():
            toml_doc[key] = value

        try:
            with open(path, 'w') as f:
                f.write(tomlkit.dumps(toml_doc))
            return True
        except Exception as e:
            return False

    @classmethod
    def import_from_toml(cls, path: Path = CLI_FM_CONFIG_PATH) -> "FMConfigManager":
        # previously the name of the fm config was .fm.toml now changed to fm_cofig.toml
        input_data = {}

        old_config_path = path.parent / '.fm.toml'

        input_data['version'] = Version('0.8.3')
        input_data['letsencrypt'] = FMLetsencryptConfig(email=None, api_key=None, api_token=None)
        input_data['root_path'] = str(path)

        if old_config_path.exists():
            old_data = tomlkit.parse(old_config_path.read_text())
            input_data['version'] = Version(old_data.get('version', '0.8.3'))
        elif path.exists():
            data = tomlkit.parse(path.read_text())
            input_data['version'] = Version(data.get('version', get_current_fm_version()))
            input_data['letsencrypt'] = FMLetsencryptConfig(
                **data.get('letsencrypt', {'email': None, 'api_key': None, 'api_token': None})
            )

        fm_config_instance = cls(**input_data)
        return fm_config_instance
