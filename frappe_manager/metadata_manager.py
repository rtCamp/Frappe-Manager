from typing import Optional
from pathlib import Path
from pydantic import BaseModel, EmailStr, Field
import tomlkit
from frappe_manager.migration_manager.version import Version
from frappe_manager import CLI_FM_CONFIG_PATH
from frappe_manager.utils.helpers import get_current_fm_version


class FMCloudflareConfig(BaseModel):
    """Cloudflare DNS API credentials for DNS-01 challenge."""

    email: Optional[EmailStr] = Field(None, description="Cloudflare account email (required for Global API Key).")
    api_token: Optional[str] = Field(None, description="Cloudflare API Token (recommended - scoped permissions).")
    api_key: Optional[str] = Field(None, description="Cloudflare Global API Key (legacy - full account access).")

    @property
    def exists(self) -> bool:
        """Check if any Cloudflare credentials are configured."""
        return bool(self.api_token or self.api_key)

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


class FMLetsencryptConfig(BaseModel):
    """Let's Encrypt configuration for certificate registration."""

    email: Optional[EmailStr] = Field(
        None, description="Email for Let's Encrypt certificate registration and notifications."
    )

    @property
    def exists(self) -> bool:
        """Check if Let's Encrypt email is configured."""
        return bool(self.email and self.email != 'dummy@fm.fm')

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


class FMValidationConfig(BaseModel):
    """Validation settings for Frappe Manager operations."""

    enforce_domain_uniqueness: bool = Field(default=True, description="Enforce domain uniqueness across benches")

    def get_toml_doc(self):
        model_dict = self.model_dump(exclude_none=True)
        toml_doc = tomlkit.document()
        for key, value in model_dict.items():
            toml_doc[key] = value
        return toml_doc

    @classmethod
    def import_from_toml_doc(cls, toml_doc):
        return cls(**toml_doc)


class FMConfigManager(BaseModel):
    root_path: Path
    version: Version
    cloudflare: FMCloudflareConfig = Field(default=FMCloudflareConfig())
    ngrok_auth_token: Optional[str] = Field(None, description="Ngrok authentication token")
    validation: FMValidationConfig = Field(default=FMValidationConfig())

    def export_to_toml(self, path: Path = CLI_FM_CONFIG_PATH) -> bool:
        exclude = {'root_path'}

        if not self.cloudflare.exists:
            exclude.add('cloudflare')

        if self.version < Version('0.13.0'):
            path = CLI_FM_CONFIG_PATH.parent / '.fm.toml'

        fm_config_dict = self.model_dump(exclude=exclude, exclude_none=True)

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
        input_data = {}

        old_config_path = path.parent / '.fm.toml'

        input_data['version'] = Version('0.8.3')
        input_data['cloudflare'] = FMCloudflareConfig(email=None, api_key=None, api_token=None)
        input_data['root_path'] = str(path)
        input_data['ngrok_auth_token'] = None
        input_data['validation'] = FMValidationConfig()

        if old_config_path.exists():
            old_data = tomlkit.parse(old_config_path.read_text())
            input_data['version'] = Version(old_data.get('version', '0.8.3'))
        elif path.exists():
            data = tomlkit.parse(path.read_text())
            input_data['version'] = Version(data.get('version', get_current_fm_version()))

            if 'cloudflare' in data:
                input_data['cloudflare'] = FMCloudflareConfig(**data['cloudflare'])
            elif 'letsencrypt' in data:
                le_data = data['letsencrypt']
                input_data['cloudflare'] = FMCloudflareConfig(
                    email=le_data.get('email'), api_token=le_data.get('api_token'), api_key=le_data.get('api_key')
                )

            input_data['ngrok_auth_token'] = data.get('ngrok_auth_token', None)

            if 'validation' in data:
                input_data['validation'] = FMValidationConfig(**data['validation'])

        fm_config_instance = cls(**input_data)
        return fm_config_instance
