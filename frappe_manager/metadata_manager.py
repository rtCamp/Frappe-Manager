from pathlib import Path
from typing import Any

import tomlkit
from pydantic import BaseModel, ConfigDict, Field

from frappe_manager import CLI_FM_CONFIG_PATH
from frappe_manager.migration_manager.version import Version
from frappe_manager.ssl_manager import DNS_PROVIDER
from frappe_manager.ssl_manager.dns_provider import DNSProviderConfig
from frappe_manager.utils import toml_document
from frappe_manager.utils.config_keys import collect_unknown_keys, unwrap_toml_value
from frappe_manager.utils.helpers import get_current_fm_version


class FMValidationConfig(BaseModel):
    """Validation settings for Frappe Manager operations."""

    # extra="allow": an unknown key here is reported by `collect_unknown_keys` and the caller
    # decides what to do with it, same as the bench-side nested models since Phase 1. fm_config.toml
    # is read by every `fm` command before the migration gate runs, so a reader that raises on a
    # typo here breaks all of fm, not just one bench. `FMConfigManager` itself (below) now carries
    # the same extra="allow" plus hand-merged top-level retention as `BenchConfig`, so a stray
    # survives at every level of this file, not just this one.
    model_config = ConfigDict(extra="allow")

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

    # extra="allow": see the design note on FMValidationConfig above; same file, same reasoning.
    model_config = ConfigDict(extra="allow")

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

    # extra="allow": see the design note on FMValidationConfig above; same file, same reasoning.
    model_config = ConfigDict(extra="allow")

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

    # extra="allow": see the design note on FMValidationConfig above; same file, same reasoning.
    model_config = ConfigDict(extra="allow")

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


def recognised_fm_config_keys() -> frozenset[str]:
    """Every top-level fm_config.toml key `FMConfigManager.import_from_toml` treats as meaningful.

    Derived from `FMConfigManager.model_fields` rather than listed a second time, for the same
    reason as `recognised_bench_config_keys` in bench_config.py: a field added or renamed here
    changes the recognised set for free. Three names are not fields and are added by hand:
    `ssl` (the table `dns_providers` is read out of), `migration_state` (kept in `_raw_config`,
    never a pydantic field), and the pre-0.20.0 top-level `[cloudflare]` table this reader still
    folds into `dns_providers` by hand.
    """
    return frozenset(FMConfigManager.model_fields) | {"ssl", "migration_state", "cloudflare"}


class FMConfigManager(BaseModel):
    # extra="allow": a top-level stray in fm_config.toml (a mistyped table header or bare key)
    # used to be silently dropped -- `import_from_toml` builds `input_data` by hand and never
    # named it, so the warning below fired once and `export_to_toml`'s `toml_document.apply` prune
    # deleted the evidence on the very next ordinary write, including the first `[migration_state]`
    # write every host gets from `_ensure_migration_state`. Retained the same way `BenchConfig`
    # retains one (see `retained_top_level` in bench_config.py): fm never deletes a key it does not
    # understand, at any depth.
    model_config = ConfigDict(extra="allow")

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
        # not grow an empty [ssl] section. `model_extra` is checked alongside `exists`: a label
        # written only because of a typo'd key (e.g. `api_toekn`, no real `api_token`/`api_key`)
        # would otherwise be silently skipped here even though `DNSProviderConfig` already retained
        # it -- `import_from_toml` warns about it, and this is the write path that turns that
        # warning into a lie by dropping the very key it just warned about.
        ssl_table = tomlkit.table()
        if self.dns_providers:
            dns = tomlkit.table()
            for label, provider_config in self.dns_providers.items():
                if provider_config.exists or provider_config.model_extra:
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

        # Populated only when the file exists; merged into `input_data` below so a stray this
        # loader does not recognise round-trips through `model_extra` instead of being silently
        # dropped by `export_to_toml`'s prune.
        retained_top_level: dict[str, Any] = {}

        if path.exists():
            data = tomlkit.parse(path.read_text())

            # A misspelled top-level key or table header (e.g. `[validaton]`) parses cleanly here
            # and is simply never looked at below, exactly the same silent-drop hazard as the
            # bench-side reader. This host's global config is read by every `fm` command, so a
            # typo warns rather than raises -- and, since `FMConfigManager` is `extra="allow"`,
            # `retained_top_level` below carries it onto the model so the warning is not the last
            # anyone sees of it. No separate hand-list of these feeds the warning below the way
            # bench_config's `[ssl]` needs one: once `retained_top_level` lands in `input_data`,
            # each of these becomes a `model_extra` entry on `fm_config_instance` ITSELF -- the
            # root of `collect_unknown_keys`'s walk, so its path there is the bare key name, with
            # no dotted prefix -- and that walk finds it below without help.
            top_level_unknown_keys = set(data.keys()) - recognised_fm_config_keys()
            retained_top_level = {key: unwrap_toml_value(data[key]) for key in top_level_unknown_keys}

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
            #
            # The whole table is splatted, not just the three named credential fields: a key inside
            # `[cloudflare]` this reader does not recognise (e.g. a typo'd `api_toekn`) used to be
            # read by nobody at all -- not counted in `top_level_unknown_keys` above (`cloudflare`
            # is itself a recognised top-level key), and not passed to `DNSProviderConfig`, whose old
            # three keyword arguments simply never named it. It is retained the same way a stray inside an
            # already-migrated `[ssl.dns_providers.<label>]` entry already is: via
            # `DNSProviderConfig`'s own extra="allow", where `collect_unknown_keys` can see it.
            legacy = data.get("cloudflare")
            if isinstance(legacy, dict) and DNS_PROVIDER.cloudflare.value not in dns_providers:
                legacy_entry = DNSProviderConfig(
                    **{key: unwrap_toml_value(value) for key, value in legacy.items() if key != "provider"},
                    provider=DNS_PROVIDER.cloudflare,
                )
                # `exists` alone would drop a table that holds nothing but a typo (no valid
                # api_token/api_key ever reaches it either way): `model_extra` catches that case so
                # the stray still survives, while a table with neither real credentials nor an
                # unrecognised key still grows no empty label.
                if legacy_entry.exists or legacy_entry.model_extra:
                    dns_providers[DNS_PROVIDER.cloudflare.value] = legacy_entry

            input_data["dns_providers"] = dns_providers or None

            if "migration_state" in data:
                import json

                raw_config_data["migration_state"] = json.loads(json.dumps(data["migration_state"]))

        input_data.update(retained_top_level)
        fm_config_instance = cls(**input_data)
        fm_config_instance._raw_config = raw_config_data

        # Every unknown key this walk can find, at every depth: `collect_unknown_keys` reaches the
        # nested `extra="allow"` models above and `dns_providers` entries under `[ssl]` the same
        # way `bench_config.py` does, AND the top-level strays, since `retained_top_level` above
        # put those onto `fm_config_instance`'s OWN `model_extra` before this walk ever starts --
        # the walk's root is that same instance, so a top-level stray surfaces with a bare, undotted
        # path. A hand-built top-level list has nothing left to add once that is true: unlike
        # bench_config's `[ssl]`, which has no model of its own to hold a hand-read stray (earning
        # `hand_read_unknown_keys()` a real union there), every hand-read region in this file --
        # `[ssl].dns_providers.<label>` and the legacy `[cloudflare]` fold -- lands inside a
        # `DNSProviderConfig`, whose own `extra="allow"` this same walk already reaches.
        # `[migration_state]` is the one hand-read region genuinely outside this walk's reach (kept
        # as raw JSON in `_raw_config`, never a model field), but a top-level list built from
        # `data.keys()` never looked inside a recognised top-level key like `migration_state`
        # either, so removing it loses no coverage. A second, hand-built list here used to repeat
        # every top-level name a second time -- one typo, printed twice.
        all_unknown_keys = collect_unknown_keys(fm_config_instance)
        if all_unknown_keys:
            from frappe_manager.output_manager import warn_or_log

            warn_or_log(
                "metadata_manager",
                f"fm_config.toml has unrecognised key(s) {', '.join(all_unknown_keys)}; "
                "check for a typo, since fm will not use them.",
            )

        return fm_config_instance
