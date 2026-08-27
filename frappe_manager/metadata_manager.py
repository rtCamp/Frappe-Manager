from pathlib import Path

import tomlkit
from pydantic import BaseModel, ConfigDict, Field

from frappe_manager import CLI_FM_CONFIG_PATH
from frappe_manager.migration_manager.version import Version
from frappe_manager.ssl_manager import DNS_PROVIDER
from frappe_manager.ssl_manager.dns_provider import DNSProviderConfig
from frappe_manager.utils.helpers import get_current_fm_version
from frappe_manager.utils import toml_document


class FMValidationConfig(BaseModel):
    """Validation settings for Frappe Manager operations."""

    # extra="forbid", matching every bench-side model. Without it a typo'd key was ignored at load
    # and then DELETED from the user's file by the write-side prune, so the evidence of the typo
    # disappeared along with the setting. The same key in a bench file is a hard error.
    model_config = ConfigDict(extra="forbid")

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

    model_config = ConfigDict(extra="forbid")

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

    model_config = ConfigDict(extra="forbid")

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

    model_config = ConfigDict(extra="forbid")

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
    dns_providers: dict[str, DNSProviderConfig] | None = Field(
        None,
        description=(
            "Labelled DNS-01 credential sets shared by every bench on this host, keyed by label. "
            "A bench-level entry with the same label wins. The set labelled 'cloudflare' is the "
            "default account a certificate gets when it names no label."
        ),
    )
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

    def export_to_toml(self, path: Path = CLI_FM_CONFIG_PATH) -> None:
        # dns_providers is written by hand below, nested under [ssl]; leaving it in the dump would
        # also emit it as a flat top-level key.
        exclude = {"root_path", "dns_providers"}

        fm_config_dict = self.model_dump(exclude=exclude, exclude_none=True)

        fm_config_dict["version"] = self.version.version

        if hasattr(self, "_raw_config") and "migration_state" in self._raw_config:
            fm_config_dict["migration_state"] = self._raw_config["migration_state"]

        desired: dict = dict(fm_config_dict)

        # [ssl.dns_providers.<label>], matching the bench-side table so a label means the same thing
        # at either scope. Attached only when non-empty: a host with no labelled credentials must
        # not grow an empty [ssl] section.
        ssl_table = tomlkit.table()
        if self.dns_providers:
            dns = tomlkit.table()
            for label, provider_config in self.dns_providers.items():
                if provider_config.exists:
                    dns[label] = provider_config.get_toml_doc()
            if len(dns) > 0:
                ssl_table["dns_providers"] = dns
        if len(ssl_table) > 0:
            desired["ssl"] = ssl_table

        # Applied onto the document already on disk so a comment the reader wrote survives the save;
        # `apply` prunes keys the model no longer produces, which is what retires `[cloudflare]`.
        toml_doc = toml_document.load_or_new(path)
        toml_document.apply(toml_doc, desired)

        # Atomic, and 0600 from creation: see toml_document.save. This is the primary store for the
        # DNS-01 credentials and the ngrok token now that certificates no longer carry a copy, and a
        # truncating write left an EMPTY fm_config.toml, which breaks every fm command on the host.
        try:
            toml_document.save(path, toml_doc)
        except Exception as e:
            raise RuntimeError(f"Failed to write FM config to {path}: {e}") from e

    @classmethod
    def import_from_toml(cls, path: Path = CLI_FM_CONFIG_PATH) -> "FMConfigManager":
        input_data = {}

        input_data["version"] = Version(get_current_fm_version())
        input_data["root_path"] = str(path)
        input_data["ngrok_auth_token"] = None
        input_data["validation"] = FMValidationConfig()
        input_data["logs"] = FMLogsConfig()
        input_data["network"] = FMNetworkConfig()
        input_data["output"] = FMOutputConfig()
        input_data["dns_providers"] = None

        raw_config_data = {}

        if path.exists():
            data = tomlkit.parse(path.read_text())
            input_data["version"] = Version(data.get("version", get_current_fm_version()))

            input_data["ngrok_auth_token"] = data.get("ngrok_auth_token", None)

            if "validation" in data:
                input_data["validation"] = FMValidationConfig(**data["validation"])

            if "logs" in data:
                input_data["logs"] = FMLogsConfig(**data["logs"])

            if "network" in data:
                input_data["network"] = FMNetworkConfig(**data["network"])

            if "output" in data:
                input_data["output"] = FMOutputConfig(**data["output"])

            dns_providers = {}
            for label, provider_data in ((data.get("ssl") or {}).get("dns_providers") or {}).items():
                if isinstance(provider_data, dict):
                    dns_providers[label] = DNSProviderConfig.import_from_toml_doc(provider_data)

            # A pre-0.20.0 file keeps its default account in a top-level `[cloudflare]` table. It is
            # folded into the `cloudflare` label here, and NOT left to the migration, because the
            # model can no longer represent that table while `export_to_toml` rebuilds the whole
            # file: any command that writes fm_config.toml would drop the credential silently, and
            # `migrate_services` does not run at all once the infrastructure version is current, so
            # the loss could never be repaired. Verified on a real host, one ordinary write emptied
            # it. An existing label wins, since it is the newer spelling, and the next write leaves
            # only the new shape on disk.
            legacy = data.get("cloudflare")
            if isinstance(legacy, dict) and DNS_PROVIDER.cloudflare.value not in dns_providers:
                legacy_entry = DNSProviderConfig(
                    provider=DNS_PROVIDER.cloudflare,
                    email=legacy.get("email"),
                    api_token=legacy.get("api_token"),
                    api_key=legacy.get("api_key"),
                )
                if legacy_entry.exists:
                    dns_providers[DNS_PROVIDER.cloudflare.value] = legacy_entry

            input_data["dns_providers"] = dns_providers or None

            if "migration_state" in data:
                import json

                raw_config_data["migration_state"] = json.loads(json.dumps(data["migration_state"]))

        fm_config_instance = cls(**input_data)
        fm_config_instance._raw_config = raw_config_data
        return fm_config_instance
