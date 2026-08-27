"""Contracts around BenchConfig's TOML boundary and its create-time-only fields.

Three things are defended here.

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

3. ``[[ssl.certificates]]`` entries are parsed through ``CERTIFICATE_ADAPTER``, so ``ssl_type``
   alone selects the variant and no field can be lost by a reader forgetting to name it. The
   reader that this replaced dropped ``hsts`` once and ``delegation_cname`` once, each time by
   omitting the key from a fixed kwarg list, so both are pinned across a full write/read cycle.
"""

import pytest

from frappe_manager.site_manager.bench_config import BenchConfig, DeployState
from frappe_manager.ssl_manager import LETSENCRYPT_PREFERRED_CHALLENGE, SUPPORTED_SSL_TYPES
from frappe_manager.ssl_manager.certificate import RETIRED_CERTIFICATE_KEYS
from frappe_manager.ssl_manager.letsencrypt_certificate import LetsencryptSSLCertificate

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
        bc.export_to_toml(out)
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

        bc.export_to_toml(out)
        text = out.read_text()
        assert "db_password_generated" not in text
        assert "generated-secret" not in text
        assert "encryption_key" not in text


_DELEGATED_CERT = (
    "\n[[ssl.certificates]]\n"
    'domain = "a.gg.com"\n'
    'ssl_type = "letsencrypt"\n'
    'challenge_type = "dns01"\n'
    'delegation_cname = "a-gg-com.fm.gw"\n'
)

_PLAIN_CERT = '\n[[ssl.certificates]]\ndomain = "b.gg.com"\nssl_type = "letsencrypt"\nchallenge_type = "dns01"\n'


class TestDelegatedCertificateSurvivesTheTomlBoundary:
    """`delegation_cname` is written by ssl_certificate_to_toml_doc, so the reader must read it.

    acme.sh gets `--challenge-alias` exactly when a bench certificate's `delegation_cname` is
    truthy, so losing the field silently downgrades a delegated certificate. The reader used to
    construct the certificate from a fixed kwarg list, so the persisted key was dropped on import
    and erased again on the next export: every later Bench held a non-delegating cert and re-issue
    tried to write _acme-challenge into a zone fm does not control. Parsing through
    `CERTIFICATE_ADAPTER` removes the kwarg list that could forget a field, and these tests hold
    the reader to that at the file boundary.
    """

    def test_delegation_cname_is_read_back(self, tmp_path):
        bc = _import(tmp_path, _BASE + _DELEGATED_CERT)

        cert = bc.ssl_certificates[0]
        assert type(cert) is LetsencryptSSLCertificate
        assert cert.delegation_cname == "a-gg-com.fm.gw"
        assert cert.domain == "a.gg.com"
        assert cert.challenge_type == LETSENCRYPT_PREFERRED_CHALLENGE.dns01

    def test_a_certificate_without_delegation_reads_back_undelegated(self, tmp_path):
        cert = _import(tmp_path, _BASE + _PLAIN_CERT).ssl_certificates[0]

        assert type(cert) is LetsencryptSSLCertificate
        assert cert.delegation_cname is None

    def test_delegation_cname_survives_export_and_reimport(self, tmp_path):
        """The bug erased the key from the file on the next write, not just from the object."""
        bc = _import(tmp_path, _BASE + _DELEGATED_CERT)

        out = tmp_path / "out.toml"
        bc.export_to_toml(out)
        assert 'delegation_cname = "a-gg-com.fm.gw"' in out.read_text()

        assert BenchConfig.import_from_toml(out).ssl_certificates[0].delegation_cname == "a-gg-com.fm.gw"


class TestCertificateVariantSelection:
    """`ssl_type` on disk picks the certificate class, with no help from any other key."""

    @pytest.mark.parametrize(
        ("ssl_type", "expected"),
        [
            ("letsencrypt", SUPPORTED_SSL_TYPES.le),
            ("dev", SUPPORTED_SSL_TYPES.dev),
            ("disable", SUPPORTED_SSL_TYPES.none),
        ],
    )
    def test_each_ssl_type_reads_back_as_itself(self, tmp_path, ssl_type, expected):
        text = f'\n[[ssl.certificates]]\ndomain = "c.gg.com"\nssl_type = "{ssl_type}"\n'

        assert _import(tmp_path, _BASE + text).ssl_certificates[0].ssl_type is expected

    def test_a_disabled_certificate_is_dropped_by_the_writer(self, tmp_path):
        """`disable` is accepted on read so an unmigrated bench loads, but fm never writes one."""
        text = '\n[[ssl.certificates]]\ndomain = "c.gg.com"\nssl_type = "disable"\n'
        bc = _import(tmp_path, _BASE + text)

        out = tmp_path / "out.toml"
        bc.export_to_toml(out)

        assert "c.gg.com" not in out.read_text()
        assert BenchConfig.import_from_toml(out).ssl_certificates == []


class TestPreMigrationCertificateEntry:
    """A bench whose file predates the 0.20.0 migration must still load.

    `fm list`, `fm bake` and `fm switch` skip the migration gate, so a ValidationError here would
    take `fm list` down for every bench on the host because one file had not been migrated yet.
    """

    def _pre_migration_toml(self) -> str:
        retired = "".join(f'{key} = "carried"\n' for key in sorted(RETIRED_CERTIFICATE_KEYS) if key != "toml_exclude")
        return (
            "\n[[ssl.certificates]]\n"
            'domain = "a.gg.com"\n'
            'ssl_type = "letsencrypt"\n'
            'challenge_type = "dns01"\n'
            'delegation_cname = "a-gg-com.fm.gw"\n'
            'hsts = "max-age=31536000"\n'
            'toml_exclude = ["domain"]\n' + retired
        )

    def test_it_loads_and_keeps_the_fields_that_still_exist(self, tmp_path):
        cert = _import(tmp_path, _BASE + self._pre_migration_toml()).ssl_certificates[0]

        assert cert.hsts == "max-age=31536000"
        assert cert.delegation_cname == "a-gg-com.fm.gw"
        assert cert.challenge_type == LETSENCRYPT_PREFERRED_CHALLENGE.dns01

    def test_the_retired_keys_are_not_written_back_out(self, tmp_path):
        """Tolerating a key on read must not make the writer perpetuate it."""
        bc = _import(tmp_path, _BASE + self._pre_migration_toml())

        out = tmp_path / "out.toml"
        bc.export_to_toml(out)
        text = out.read_text()

        for key in RETIRED_CERTIFICATE_KEYS:
            assert f"{key} =" not in text, f"{key} must not survive a save"

    def test_the_first_save_is_already_a_fixed_point(self, tmp_path):
        """import -> export must converge in one step, on the pre-migration shape too.

        `fm migrate` records its success with `set_bench_migration_version`, which round-trips the
        whole file through this model the instant the migration finishes. Anything the model cannot
        represent, or spells differently on the way out, is erased right there, so a migration can
        only be trusted if this cycle is stable.
        """
        first = tmp_path / "first.toml"
        _import(tmp_path, _BASE + self._pre_migration_toml()).export_to_toml(first)

        reloaded = BenchConfig.import_from_toml(first)
        second = tmp_path / "second.toml"
        reloaded.export_to_toml(second)

        assert second.read_text() == first.read_text()
        assert reloaded.ssl_certificates[0].model_dump() == {
            "domain": "a.gg.com",
            "ssl_type": SUPPORTED_SSL_TYPES.le,
            "challenge_type": LETSENCRYPT_PREFERRED_CHALLENGE.dns01,
            "enabled": True,
            "hsts": "max-age=31536000",
            "acme_client": "acme.sh",
            "dns_provider": None,
            "delegation_cname": "a-gg-com.fm.gw",
        }


def test_a_bench_config_carrying_keys_removed_in_0_20_0_still_loads(tmp_path):
    """Keys and tables deleted from the models must not break benches that still carry them.

    `[switch].search_replace` was removed as a key, and `[registry]` as a whole table, in
    0.20.0. The models are `extra="forbid"` and `import_from_toml` splats each TOML table
    into its model, so a stale KEY would otherwise make every command that loads the bench
    die with a pydantic ValidationError. `search_replace` was deleted once before on the
    grounds that nothing read it, and it took down `fm info` and `fm ssl list` on a live
    bench whose config carried `search_replace = true`.

    A removed TABLE is safer by construction: the loader names the tables it reads, so an
    unknown one never reaches a model. It is still stripped by the migration, so it stops
    being carried forward into a version that might reuse the name for something else.
    """
    path = tmp_path / "bench_config.toml"
    path.write_text(
        _BASE + '[switch]\nmigrate = true\nsearch_replace = true\n\n[registry]\nregistry = "ghcr.io/acme"\n'
    )

    cfg = BenchConfig.import_from_toml(path)

    assert cfg.switch is not None
    assert cfg.switch.migrate is True
    assert not hasattr(cfg.switch, "search_replace")
    assert not hasattr(cfg, "registry"), "the table is gone from the model, not merely emptied"


def test_switch_config_still_rejects_a_genuinely_unknown_key():
    """The compatibility field must not become a licence to accept typos."""
    from pydantic import ValidationError

    from frappe_manager.site_manager.bench_config import SwitchConfig

    with pytest.raises(ValidationError):
        SwitchConfig(serch_replace=True)


_SSL_WITH_HSTS = (
    "\n[[ssl.certificates]]\n"
    'domain = "dev.localhost"\n'
    'ssl_type = "letsencrypt"\n'
    'challenge_type = "http01"\n'
    'hsts = "max-age=31536000; includeSubDomains"\n'
)


class TestHstsSurvivesTheTomlRoundTrip:
    """`ssl_certificate_to_toml_doc` dumps the whole model, so hsts reaches disk, but the reader
    rebuilt the certificate from an explicit field list that omitted it. Every bench therefore
    reloaded as hsts="off" and nginx-proxy never received the header the config asked for, silently
    undoing the value migrate_0_19_0 goes out of its way to carry forward."""

    def test_the_configured_value_is_read_back(self, tmp_path):
        cert = _import(tmp_path, _BASE + _SSL_WITH_HSTS).get_primary_certificate()

        assert cert.hsts == "max-age=31536000; includeSubDomains"

    def test_the_value_reaches_the_nginx_container(self, tmp_path):
        """The property that matters: what the proxy is actually told."""
        inputs = _import(tmp_path, _BASE + _SSL_WITH_HSTS).export_to_compose_inputs()

        assert inputs["environment"]["nginx"]["HSTS"] == "max-age=31536000; includeSubDomains"

    def test_an_absent_key_still_defaults_to_off(self, tmp_path):
        """The fix must not turn HSTS on for benches that never asked for it."""
        without = _SSL_WITH_HSTS.replace('hsts = "max-age=31536000; includeSubDomains"\n', "")

        assert _import(tmp_path, _BASE + without).get_primary_certificate().hsts == "off"

    def test_it_survives_an_export_and_reimport(self, tmp_path):
        """Round-trip through fm's own writer, not just a hand-written file."""
        original = _import(tmp_path, _BASE + _SSL_WITH_HSTS)
        out = tmp_path / "exported.toml"
        original.export_to_toml(out)

        assert BenchConfig.import_from_toml(out).get_primary_certificate().hsts == (
            "max-age=31536000; includeSubDomains"
        )
