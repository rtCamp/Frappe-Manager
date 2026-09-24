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


class FMPruneConfig(BaseModel):
    """Disk-hygiene retention (`[prune]` in fm_config.toml).

    Host-wide defaults for `fm prune` and `fm services prune`; a bench's own `[prune]`
    table overrides per key, and command flags override both. Cleanup only ever happens
    when one of those commands runs -- never as a side effect of another operation.
    """

    # extra="allow": see the design note on FMValidationConfig above; same file, same reasoning.
    model_config = ConfigDict(extra="allow")

    keep_backup_sessions: int = Field(
        default=3,
        description="Backup sessions (timestamped dirs under backups/migrations and backups/workers) "
        "kept per location by the prune commands. The newest session is never pruned.",
    )
    keep_log_archives: int = Field(
        default=3,
        description="Rotated .gz archives kept per log file by the prune commands.",
    )
    rotate_logs_over: str = Field(
        default="10M",
        description="Only log files larger than this are rotated (e.g. '500K', '10M', '1G').",
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
        description="CIDR subnet for the frontend-network (e.g. 10.1.0.0/16)",
    )
    proxy_ip: str | None = Field(
        default=None,
        description="Static IP of nginx-proxy on frontend-network (e.g. 10.1.0.2)",
    )

    @property
    def configured(self) -> bool:
        return bool(self.subnet_cidr and self.proxy_ip)


def recognised_fm_config_keys() -> frozenset[str]:
    """Every top-level fm_config.toml key `FMConfigManager.import_from_toml` treats as meaningful.

    Derived from `FMConfigManager.model_fields` rather than listed a second time, for the same
    reason as `recognised_bench_config_keys` in bench_config.py: a field added or renamed here
    changes the recognised set for free. Five names are not fields and are added by hand:
    `ssl` (the table `dns_providers` is read out of), `schema` (kept in `_raw_config`,
    never a pydantic field), its pre-1.0.0 spelling `migration_state`, the pre-1.0.0 top-level
    `[cloudflare]` table this reader still folds into `dns_providers` by hand, and the retired
    top-level `version` key (v1.0.0's migration strips both from disk; recognised-but-inert until
    then so a not-yet-migrated file is not warned about its own keys).
    """
    return frozenset(FMConfigManager.model_fields) | {"ssl", "schema", "migration_state", "cloudflare", "version"}


def recognised_global_schema_keys() -> frozenset[str]:
    """Every `[schema]` key this file itself gives meaning to: `version` (read in
    `get_system_migration_version`, written in `set_system_migration_version`) and the two
    spellings it has had before, `migrated_to` and `system_migrated_to` (seeded from at load,
    renamed on disk by v1.0.0's migration; recognised so a not-yet-migrated host is not warned
    about its own ledger).

    `[schema]` is kept as a raw dict in `_raw_config`, never a pydantic field (see the
    `extra="allow"` comment on `FMConfigManager` below and `import_from_toml`'s handling of the
    table), so it has no `model_extra` of its own for `collect_unknown_keys` to walk into -- the
    same hole bench_config.py's `[ssl]` has, and the same fix: an explicit recognised set,
    checked by hand in `import_from_toml`, derived here instead of hand-listed a second time
    there.
    """
    return frozenset({"version", "migrated_to", "system_migrated_to"})


class FMConfigManager(BaseModel):
    # extra="allow": a top-level stray in fm_config.toml (a mistyped table header or bare key)
    # used to be silently dropped -- `import_from_toml` builds `input_data` by hand and never
    # named it, so the warning below fired once and `export_to_toml`'s `toml_document.apply` prune
    # deleted the evidence on the very next ordinary write, including the first `[schema]`
    # write every host gets from `set_system_migration_version`. Retained the same way `BenchConfig`
    # retains one (see `retained_top_level` in bench_config.py): fm never deletes a key it does not
    # understand, at any depth.
    model_config = ConfigDict(extra="allow")

    root_path: Path
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
    prune: FMPruneConfig = Field(default=FMPruneConfig())
    network: FMNetworkConfig = Field(default=FMNetworkConfig())
    output: FMOutputConfig = Field(default=FMOutputConfig())

    def __init__(self, **data):
        super().__init__(**data)
        self._raw_config = {}

    def get_system_migration_version(self) -> Version:
        """The global services & configuration ledger: the version `fm services migrate` last
        completed. THE single source of truth for that tier -- both migration gates and the
        executor's own discovery read it through here. One key: `[schema].version`,
        symmetric with the bench ledger in bench_config.toml. Legacy spellings (the pre-rename
        `system_migrated_to`, the retired top-level `version`) are seeded into this key by
        `import_from_toml`, never read here. Absent entirely means "never migrated / unknown"
        and reads as 0.0.0, the same convention as a bench with no `[schema]`.

        Reads through a local `schema_data` rather than the `self._raw_config[...]`
        chain inline, so this method's literal key read is visible to the AST guard test in
        `tests/unit/site_manager/test_config_surface.py` the same way `[ssl]`'s hand-read keys
        in bench_config.py are: that scan only recognises `name.get("key")` on a plain local
        variable, not a subscript-of-a-subscript.
        """
        if hasattr(self, "_raw_config") and "schema" in self._raw_config:
            schema_data = self._raw_config["schema"]
            version_str = schema_data.get("version")
            if version_str:
                return Version(version_str)
        return Version("0.0.0")

    def set_system_migration_version(self, version: Version) -> None:
        """Stamp the ledger. Writes `version` and pops the pre-rename spellings `migrated_to` and
        `system_migrated_to`: `export_to_toml` writes `_raw_config["schema"]` back verbatim, so a
        lingering old key here would be resurrected on disk by every later save. Together with the
        export prune retiring the top-level `version` key, the first stamp on a legacy host
        leaves exactly one version key on disk. Persists immediately: the ledger is what a
        concurrent gate would read."""
        if not hasattr(self, "_raw_config"):
            self._raw_config = {}

        if "schema" not in self._raw_config:
            self._raw_config["schema"] = {}

        self._raw_config["schema"]["version"] = str(version.version)
        self._raw_config["schema"].pop("migrated_to", None)
        self._raw_config["schema"].pop("system_migrated_to", None)
        self.export_to_toml()


    def export_to_toml(self, path: Path | None = None) -> None:
        # Default to the file this config was LOADED from (`root_path`), never a module-level
        # constant: `set_system_migration_version` saves through this default, and with the
        # constant here a config imported from any other path (tests, tooling) silently wrote
        # the OPERATOR'S real ~/frappe/fm_config.toml -- observed clobbering a live host's
        # ledger, network table and ngrok token from the unit suite.
        if path is None:
            path = Path(self.root_path)
        # dns_providers is written by hand below, nested under [ssl]; leaving it in the dump would
        # also emit it as a flat top-level key.
        exclude = {"root_path", "dns_providers"}

        fm_config_dict = self.model_dump(exclude=exclude, exclude_none=True)

        if hasattr(self, "_raw_config") and "schema" in self._raw_config:
            fm_config_dict["schema"] = self._raw_config["schema"]

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

        input_data["root_path"] = str(path)
        input_data["ngrok_auth_token"] = None
        input_data["validation"] = FMValidationConfig()
        input_data["logs"] = FMLogsConfig()
        input_data["prune"] = FMPruneConfig()
        input_data["network"] = FMNetworkConfig()
        input_data["output"] = FMOutputConfig()
        input_data["dns_providers"] = None

        raw_config_data = {}

        # Dotted paths for a stray inside [schema], the one hand-read table with no
        # model of its own to hold a stray as `model_extra` -- see
        # `recognised_global_schema_keys`.
        hand_read_unknown_keys: list[str] = []

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


            input_data["ngrok_auth_token"] = data.get("ngrok_auth_token", None)

            if "validation" in data:
                input_data["validation"] = FMValidationConfig(**data["validation"])

            if "logs" in data:
                input_data["logs"] = FMLogsConfig(**data["logs"])

            if "prune" in data:
                input_data["prune"] = FMPruneConfig(**data["prune"])

            if "network" in data:
                input_data["network"] = FMNetworkConfig(**data["network"])

            if "output" in data:
                input_data["output"] = FMOutputConfig(**data["output"])

            dns_providers = {}
            for label, provider_data in ((data.get("ssl") or {}).get("dns_providers") or {}).items():
                if isinstance(provider_data, dict):
                    dns_providers[label] = DNSProviderConfig.import_from_toml_doc(provider_data)

            # A pre-1.0.0 file keeps its default account in a top-level `[cloudflare]` table. It is
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

            # `[migration_state]` is `[schema]`'s pre-1.0.0 spelling. Read here, never written:
            # the table is captured under its new name, and v1.0.0's migration is what removes the
            # old one from disk.
            schema_table_name = "schema" if "schema" in data else "migration_state"
            if schema_table_name in data:
                import json

                # `json.loads(json.dumps(...))` is a two-step unwrap: it strips tomlkit's `Item`
                # wrapper the same way `unwrap_toml_value` does elsewhere, but in one pass over the
                # whole table rather than key by key, since the table is captured wholesale rather
                # than splatted into named fields. Captured BEFORE the stray check below, and
                # completely unfiltered by it: `export_to_toml` writes this dict back verbatim
                # (see its own `schema` line), so a stray here surviving a save was never
                # contingent on it being recognised -- only on it staying in this dict.
                schema_data = json.loads(json.dumps(data[schema_table_name]))
                raw_config_data["schema"] = schema_data

                # Same hole `[ssl]` has in bench_config.py: this table is read by hand, not
                # splatted into a model, so a typo'd key (e.g. `sytem_migrated_to`) parses cleanly
                # and was previously never looked at again. `collect_unknown_keys` below cannot
                # find it either -- `_raw_config` is a plain dict, not a `BaseModel`, so it has no
                # `model_extra` for that walk to reach -- hence the explicit check here, unioned
                # into the same message below.
                if isinstance(schema_data, dict):
                    hand_read_unknown_keys.extend(
                        f"{schema_table_name}.{key}"
                        for key in set(schema_data.keys()) - recognised_global_schema_keys()
                    )

            # THE one place legacy ledger spellings are understood, and memory-only: the ledger
            # is read (by the gates and the executor's discovery) BEFORE any migration runs, so
            # a pre-rename file must still read correctly here or a v0.19 host would read 0.0.0
            # and discovery would re-select the frozen v0.19 migration against it. Disk is cut
            # over by the write path instead: the first stamp writes `[schema].version` and pops
            # the old spellings, and the export prune retires the top-level `version` key.
            # Precedence runs newest to oldest: `migrated_to` (the v1.0.0-cycle ledger key),
            # `system_migrated_to` (its predecessor), then the top-level `version` (hosts from
            # before the ledger existed, where it doubled as one).
            schema = raw_config_data.setdefault("schema", {})
            if isinstance(schema, dict) and not schema.get("version"):
                legacy_ledger = (
                    schema.get("migrated_to")
                    or schema.get("system_migrated_to")
                    or unwrap_toml_value(data.get("version"))
                )
                if legacy_ledger:
                    schema["version"] = str(legacy_ledger)
            if not raw_config_data["schema"]:
                del raw_config_data["schema"]

        input_data.update(retained_top_level)
        fm_config_instance = cls(**input_data)
        fm_config_instance._raw_config = raw_config_data

        # `collect_unknown_keys` walks every `extra="allow"` model from this instance down,
        # including top-level strays (`retained_top_level` put them on this instance's own
        # `model_extra` before the walk). The one region outside its reach is `[schema]`
        # -- raw JSON in `_raw_config`, no model, no `model_extra` -- so `hand_read_unknown_keys`
        # is unioned in: strays from either family report through the SAME warning, never two.
        all_unknown_keys = sorted(set(collect_unknown_keys(fm_config_instance)) | set(hand_read_unknown_keys))
        if all_unknown_keys:
            from frappe_manager.output_manager import warn_or_log

            warn_or_log(
                "metadata_manager",
                f"fm_config.toml has unrecognised key(s) {', '.join(all_unknown_keys)}; "
                "check for a typo, since fm will not use them.",
            )

        return fm_config_instance
