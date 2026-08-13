"""Contracts around BenchConfig's TOML boundary and its create-time-only fields.

Two things are defended here.

1. ``[deploy_state]`` import is guarded by ``deploy_state_data and isinstance(..., dict)``.
   bench_config.toml is a user-editable file, so a hand-edited ``deploy_state = "..."`` scalar
   (or a leftover empty table) must be ignored, not fed to ``.get()``: dropping either half of
   that conjunction turns a cosmetically broken config file into a crash on every fm command
   that loads the bench, or invents an empty DeployState that makes `fm rollback` think a
   deploy has happened.

2. ``db_password_generated`` (and its create-time-only siblings) carry ``exclude=True``, so they
   are runtime-only inputs that NEVER reach a serialized form. The design forbids credentials and
   one-shot provisioning flags in bench_config.toml, and `model_dump()`/`model_dump_json()` are
   the generic serialization paths that any future caller reaches for; only the field-level
   exclude protects those, since the explicit exclude set in export_to_toml covers export alone.
"""

from frappe_manager.site_manager.bench_config import BenchConfig, DeployState

_BASE = 'name = "dev.localhost"\ndeveloper_mode = true\nadmin_tools = true\nenvironment = "dev"\n'

_DEPLOY_STATE = (
    "\n[deploy_state]\n"
    'current_tag = "v2"\n'
    'previous_tag = "v1"\n'
    'last_deploy_at = "2026-01-01T00:00:00"\n'
    "[[deploy_state.history]]\n"
    'tag = "v2"\n'
    'deployed_at = "2026-01-01T00:00:00"\n'
    'migrate_status = "migrated"\n'
)


def _import(tmp_path, text: str) -> BenchConfig:
    path = tmp_path / "bench_config.toml"
    path.write_text(text)
    return BenchConfig.import_from_toml(path)


class TestDeployStateImportGuard:
    """`if deploy_state_data and isinstance(deploy_state_data, dict)` — both conjuncts matter."""

    def test_well_formed_table_is_parsed(self, tmp_path):
        bc = _import(tmp_path, _BASE + _DEPLOY_STATE)

        assert isinstance(bc.deploy_state, DeployState)
        assert bc.deploy_state.current_tag == "v2"
        assert bc.deploy_state.previous_tag == "v1"
        assert bc.deploy_state.last_deploy_at == "2026-01-01T00:00:00"
        assert [e.tag for e in bc.deploy_state.history] == ["v2"]
        assert bc.deploy_state.history[0].migrate_status == "migrated"

    def test_missing_key_yields_none(self, tmp_path):
        assert _import(tmp_path, _BASE).deploy_state is None

    def test_scalar_deploy_state_is_ignored_not_dereferenced(self, tmp_path):
        """`deploy_state = "corrupt"` is truthy but not a mapping: import must survive it."""
        bc = _import(tmp_path, _BASE + '\ndeploy_state = "corrupt"\n')

        assert bc.deploy_state is None
        assert bc.name == "dev.localhost"

    def test_array_deploy_state_is_ignored(self, tmp_path):
        bc = _import(tmp_path, _BASE + '\ndeploy_state = ["v1", "v2"]\n')

        assert bc.deploy_state is None

    def test_empty_table_yields_none_not_a_blank_deploy_state(self, tmp_path):
        """An empty `[deploy_state]` is falsy: no deploy has happened, so state stays None."""
        bc = _import(tmp_path, _BASE + "\n[deploy_state]\n")

        assert bc.deploy_state is None

    def test_deploy_state_survives_export_and_reimport(self, tmp_path):
        bc = _import(tmp_path, _BASE + _DEPLOY_STATE)

        out = tmp_path / "out.toml"
        assert bc.export_to_toml(out) is True
        reimported = BenchConfig.import_from_toml(out)

        assert isinstance(reimported.deploy_state, DeployState)
        assert reimported.deploy_state.current_tag == "v2"
        assert [e.tag for e in reimported.deploy_state.history] == ["v2"]


class TestCreateTimeOnlyFieldsAreNeverSerialized:
    """`exclude=True` keeps runtime-only create inputs out of every dump of the model."""

    def _config(self, tmp_path) -> BenchConfig:
        bc = _import(tmp_path, _BASE)
        bc.db_password_generated = True
        bc.db_password = "generated-secret"
        bc.db_admin_user = "root"
        bc.db_admin_password = "admin-secret"
        bc.attach_existing_site = True
        bc.encryption_key = "enc-key"
        return bc

    def test_fields_are_readable_at_runtime(self, tmp_path):
        bc = self._config(tmp_path)

        assert bc.db_password_generated is True
        assert bc.db_password == "generated-secret"
        assert bc.attach_existing_site is True

    def test_model_dump_omits_create_time_only_fields(self, tmp_path):
        dumped = self._config(tmp_path).model_dump()

        for field in (
            "db_password_generated",
            "db_password",
            "db_admin_user",
            "db_admin_password",
            "attach_existing_site",
            "encryption_key",
        ):
            assert field not in dumped, f"{field} must not be serialized"
        # A field that IS part of the persisted config, to prove the dump is not simply empty.
        assert dumped["name"] == "dev.localhost"

    def test_model_dump_json_omits_create_time_only_fields(self, tmp_path):
        payload = self._config(tmp_path).model_dump_json()

        assert "db_password_generated" not in payload
        assert "generated-secret" not in payload
        assert "admin-secret" not in payload
        assert "enc-key" not in payload
        assert "dev.localhost" in payload

    def test_exported_toml_omits_create_time_only_fields(self, tmp_path):
        bc = self._config(tmp_path)
        out = tmp_path / "out.toml"

        assert bc.export_to_toml(out) is True
        text = out.read_text()
        assert "db_password_generated" not in text
        assert "generated-secret" not in text
        assert "encryption_key" not in text
