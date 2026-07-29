from pathlib import Path

import tomlkit
from pydantic import BaseModel, EmailStr, Field

from frappe_manager import CLI_FM_CONFIG_PATH
from frappe_manager.migration_manager.version import Version
from frappe_manager.utils.helpers import get_current_fm_version


class FMCloudflareConfig(BaseModel):
    """Cloudflare DNS API credentials for DNS-01 challenge."""

    email: EmailStr | None = Field(None, description="Cloudflare account email (required for Global API Key).")
    api_token: str | None = Field(None, description="Cloudflare API Token (recommended; scoped permissions).")
    api_key: str | None = Field(None, description="Cloudflare Global API Key (legacy; full account access).")

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

    email: EmailStr | None = Field(
        None,
        description="Email for Let's Encrypt certificate registration and notifications.",
    )

    @property
    def exists(self) -> bool:
        """Check if Let's Encrypt email is configured."""
        return bool(self.email and self.email != "dummy@fm.fm")

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


class FMLogsConfig(BaseModel):
    """Logging configuration for file and console output."""

    file_level: str = Field(
        default="DEBUG",
        description="Log level for file logs (DEBUG, INFO, WARNING, ERROR, CRITICAL)",
    )

    def get_toml_doc(self):
        model_dict = self.model_dump(exclude_none=True)
        toml_doc = tomlkit.document()
        for key, value in model_dict.items():
            toml_doc[key] = value
        return toml_doc

    @classmethod
    def import_from_toml_doc(cls, toml_doc):
        return cls(**toml_doc)


class FMOutputConfig(BaseModel):
    """Terminal output appearance: color THEME + layout STYLE + token overrides."""

    theme: str = Field(
        default="default",
        description="Output color theme: default, mono (color-blind safe), high-contrast. Env: FM_THEME.",
    )
    style: str = Field(
        default="rail",
        description="Output layout style: rail, box, flat, ascii. Env: FM_STYLE.",
    )
    colors: dict[str, str] = Field(
        default={},
        description="Per-token style overrides, e.g. 'fm.env.prod' = 'bold magenta'.",
    )

    def get_toml_doc(self):
        model_dict = self.model_dump(exclude_none=True)
        toml_doc = tomlkit.document()
        for key, value in model_dict.items():
            toml_doc[key] = value
        return toml_doc

    @classmethod
    def import_from_toml_doc(cls, toml_doc):
        return cls(**toml_doc)


class FMNetworkConfig(BaseModel):
    """Network configuration for the global frontend network."""

    subnet_cidr: str | None = Field(
        default=None,
        description="CIDR subnet for the global-frontend-network (e.g. 10.1.0.0/16)",
    )
    proxy_ip: str | None = Field(
        default=None,
        description="Static IP of global-nginx-proxy on global-frontend-network (e.g. 10.1.0.2)",
    )

    @property
    def configured(self) -> bool:
        return bool(self.subnet_cidr and self.proxy_ip)


class FMConfigManager(BaseModel):
    root_path: Path
    version: Version
    cloudflare: FMCloudflareConfig = Field(default=FMCloudflareConfig())
    ngrok_auth_token: str | None = Field(None, description="Ngrok authentication token")
    validation: FMValidationConfig = Field(default=FMValidationConfig())
    logs: FMLogsConfig = Field(default=FMLogsConfig())
    network: FMNetworkConfig = Field(default=FMNetworkConfig())
    output: FMOutputConfig = Field(default=FMOutputConfig())

    def __init__(self, **data):
        super().__init__(**data)
        self._raw_config = {}

    def get_system_migration_version(self) -> Version:
        """Get version system is migrated to."""
        if hasattr(self, "_raw_config") and "migration_state" in self._raw_config:
            version_str = self._raw_config["migration_state"].get("system_migrated_to")
            if version_str:
                return Version(version_str)
        return self.version

    def set_system_migration_version(self, version: Version) -> None:
        """Update system migration version."""
        if not hasattr(self, "_raw_config"):
            self._raw_config = {}

        if "migration_state" not in self._raw_config:
            self._raw_config["migration_state"] = {}

        self._raw_config["migration_state"]["system_migrated_to"] = str(version.version)
        self.export_to_toml()

    def _ensure_migration_state(self) -> None:
        """Ensure migration_state exists in config."""
        if not hasattr(self, "_raw_config"):
            self._raw_config = {}

        if "migration_state" not in self._raw_config:
            self._raw_config["migration_state"] = {
                "system_migrated_to": str(self.version.version),
            }
            self.export_to_toml()

    _config_data: dict | None = None

    def export_to_toml(self, path: Path = CLI_FM_CONFIG_PATH) -> None:
        exclude = {"root_path"}

        if not self.cloudflare.exists:
            exclude.add("cloudflare")

        fm_config_dict = self.model_dump(exclude=exclude, exclude_none=True)

        fm_config_dict["version"] = self.version.version

        if hasattr(self, "_raw_config") and "migration_state" in self._raw_config:
            fm_config_dict["migration_state"] = self._raw_config["migration_state"]

        toml_doc = tomlkit.document()

        for key, value in fm_config_dict.items():
            toml_doc[key] = value

        try:
            with open(path, "w") as f:
                f.write(tomlkit.dumps(toml_doc))
        except Exception as e:
            raise RuntimeError(f"Failed to write FM config to {path}: {e}") from e

    @classmethod
    def import_from_toml(cls, path: Path = CLI_FM_CONFIG_PATH) -> "FMConfigManager":
        input_data = {}

        input_data["version"] = Version(get_current_fm_version())
        input_data["cloudflare"] = FMCloudflareConfig(email=None, api_key=None, api_token=None)
        input_data["root_path"] = str(path)
        input_data["ngrok_auth_token"] = None
        input_data["validation"] = FMValidationConfig()
        input_data["logs"] = FMLogsConfig()
        input_data["network"] = FMNetworkConfig()
        input_data["output"] = FMOutputConfig()

        raw_config_data = {}

        if path.exists():
            data = tomlkit.parse(path.read_text())
            input_data["version"] = Version(data.get("version", get_current_fm_version()))

            if "cloudflare" in data:
                input_data["cloudflare"] = FMCloudflareConfig(**data["cloudflare"])

            input_data["ngrok_auth_token"] = data.get("ngrok_auth_token", None)

            if "validation" in data:
                input_data["validation"] = FMValidationConfig(**data["validation"])

            if "logs" in data:
                input_data["logs"] = FMLogsConfig(**data["logs"])

            if "network" in data:
                input_data["network"] = FMNetworkConfig(**data["network"])

            if "output" in data:
                input_data["output"] = FMOutputConfig(**data["output"])

            if "migration_state" in data:
                import json

                raw_config_data["migration_state"] = json.loads(json.dumps(data["migration_state"]))

        fm_config_instance = cls(**input_data)
        fm_config_instance._raw_config = raw_config_data
        return fm_config_instance
