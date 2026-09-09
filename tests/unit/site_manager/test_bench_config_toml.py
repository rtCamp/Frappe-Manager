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

import datetime

import pytest

from frappe_manager.site_manager.bench_config import BenchConfig, DeployState
from frappe_manager.ssl_manager import LETSENCRYPT_PREFERRED_CHALLENGE, SUPPORTED_SSL_TYPES
from frappe_manager.ssl_manager.certificate import RETIRED_CERTIFICATE_KEYS
from frappe_manager.ssl_manager.letsencrypt_certificate import LetsencryptSSLCertificate
from frappe_manager.utils.config_keys import collect_unknown_keys

_BASE = 'name = "dev.localhost"\ndeveloper_mode = true\nadmin_tools = true\nenvironment = "dev"\n'

_DEPLOY_STATE = (
    "\n[deploy_state]\n"
    'current_image = "v2"\n'
    'previous_image = "v1"\n'
    'last_deploy_at = "2026-01-01T00:00:00"\n'
    "[[deploy_state.history]]\n"
    'image = "v2"\n'
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
        assert bc.deploy_state.current_image == "v2"
        assert bc.deploy_state.previous_image == "v1"
        assert bc.deploy_state.last_deploy_at == "2026-01-01T00:00:00"
        assert [e.image for e in bc.deploy_state.history] == ["v2"]
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
        assert reimported.deploy_state.current_image == "v2"
        assert [e.image for e in reimported.deploy_state.history] == ["v2"]


class TestStaleDeployStateKeysWarnLoudly:
    """`current_tag`/`previous_tag` are the pre-rename spellings. Reading the new names with
    ``.get()`` (rather than splatting into ``DeployState``) means a stale top-level key never
    reaches the model at all, so import must announce it rather than silently loading an empty
    (indistinguishable from never-deployed) deploy_state.
    """

    _OLD_SHAPED = (
        "\n[deploy_state]\n"
        'current_tag = "local/mybench:v2"\n'
        'previous_tag = "local/mybench:v1"\n'
        'last_deploy_at = "2026-01-01T00:00:00"\n'
    )

    def test_import_survives_and_yields_an_empty_but_present_deploy_state(self, tmp_path):
        bc = _import(tmp_path, _BASE + self._OLD_SHAPED)

        assert isinstance(bc.deploy_state, DeployState)
        assert bc.deploy_state.current_image is None
        assert bc.deploy_state.previous_image is None
        assert bc.deploy_state.history == []

    def test_a_warning_names_the_bench_and_the_stale_keys(self, tmp_path):
        from unittest.mock import MagicMock

        from frappe_manager.output_manager import set_global_output_handler
        from frappe_manager.output_manager.base import OutputHandler

        handler = MagicMock(spec=OutputHandler)
        set_global_output_handler(handler)
        try:
            _import(tmp_path, _BASE + self._OLD_SHAPED)
        finally:
            set_global_output_handler(None)

        handler.warning.assert_called_once()
        message = handler.warning.call_args.args[0]
        assert "dev.localhost" in message
        assert "current_tag" in message
        assert "previous_tag" in message
        assert "rollback" in message

    def test_no_warning_when_the_keys_are_already_current(self, tmp_path):
        from unittest.mock import MagicMock

        from frappe_manager.output_manager import set_global_output_handler
        from frappe_manager.output_manager.base import OutputHandler

        handler = MagicMock(spec=OutputHandler)
        set_global_output_handler(handler)
        try:
            _import(tmp_path, _BASE + _DEPLOY_STATE)
        finally:
            set_global_output_handler(None)

        handler.warning.assert_not_called()


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
            ("custom", SUPPORTED_SSL_TYPES.custom),
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


class TestBehindProxyRoundTrip:
    """`behind_proxy` is optional-with-a-default on the base model (see certificate.py), like
    `hsts`/`enabled` before it: an old on-disk config that has never heard of it must still load,
    with the field defaulting to False, and no migration is needed to introduce it."""

    @pytest.mark.parametrize("ssl_type", ["dev", "letsencrypt"])
    def test_behind_proxy_true_round_trips(self, tmp_path, ssl_type):
        text = f'\n[[ssl.certificates]]\ndomain = "c.gg.com"\nssl_type = "{ssl_type}"\nbehind_proxy = true\n'
        bc = _import(tmp_path, _BASE + text)

        assert bc.ssl_certificates[0].behind_proxy is True

        out = tmp_path / "out.toml"
        bc.export_to_toml(out)
        reimported = BenchConfig.import_from_toml(out).ssl_certificates[0]
        assert reimported.behind_proxy is True
        assert reimported.ssl_type.value == ssl_type

    @pytest.mark.parametrize("ssl_type", ["dev", "letsencrypt", "custom"])
    def test_an_old_config_with_no_behind_proxy_key_defaults_to_false(self, tmp_path, ssl_type):
        """Proves the claim this field's addition rested on: no migration needed, since a config
        written before this field existed simply lacks the key, and the model default fills it in."""
        text = f'\n[[ssl.certificates]]\ndomain = "c.gg.com"\nssl_type = "{ssl_type}"\n'

        assert _import(tmp_path, _BASE + text).ssl_certificates[0].behind_proxy is False

    def test_behind_proxy_false_also_round_trips_explicitly(self, tmp_path):
        text = '\n[[ssl.certificates]]\ndomain = "c.gg.com"\nssl_type = "dev"\nbehind_proxy = false\n'
        bc = _import(tmp_path, _BASE + text)

        out = tmp_path / "out.toml"
        bc.export_to_toml(out)
        assert BenchConfig.import_from_toml(out).ssl_certificates[0].behind_proxy is False


class TestCustomCertificateSourceFieldsNeverSurviveTheTomlBoundary:
    """`cert_source`/`key_source`/`ca_source` are `exclude=True` on the model (see certificate.py),
    so they never appear in an fm-written file. But `exclude=True` only governs OUTPUT: pydantic
    still accepts the key on INPUT, since it is a real field and not a typo. A hand-edited file
    naming one therefore parses today and is silently dropped on the next export -- pinned here so
    that stays true rather than becoming an `extra_forbidden` error or, worse, a path fm starts
    treating as durable state.
    """

    def test_a_bare_custom_certificate_round_trips(self, tmp_path):
        text = '\n[[ssl.certificates]]\ndomain = "c.gg.com"\nssl_type = "custom"\n'
        bc = _import(tmp_path, _BASE + text)

        cert = bc.ssl_certificates[0]
        assert cert.domain == "c.gg.com"
        assert cert.ssl_type is SUPPORTED_SSL_TYPES.custom
        assert cert.cert_source is None

        out = tmp_path / "out.toml"
        bc.export_to_toml(out)
        reimported = BenchConfig.import_from_toml(out).ssl_certificates[0]
        assert reimported.ssl_type is SUPPORTED_SSL_TYPES.custom
        assert reimported.cert_source is None

    def test_a_hand_written_source_field_is_accepted_on_read_but_dropped_on_export(self, tmp_path):
        text = (
            '\n[[ssl.certificates]]\ndomain = "c.gg.com"\nssl_type = "custom"\n'
            'cert_source = "/home/op/c.gg.com.crt"\n'
        )
        bc = _import(tmp_path, _BASE + text)

        cert = bc.ssl_certificates[0]
        assert str(cert.cert_source) == "/home/op/c.gg.com.crt"

        out = tmp_path / "out.toml"
        bc.export_to_toml(out)
        assert "cert_source" not in out.read_text()
        assert BenchConfig.import_from_toml(out).ssl_certificates[0].cert_source is None


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
            "behind_proxy": False,
            "acme_client": "acme.sh",
            "dns_provider": None,
            "delegation_cname": "a-gg-com.fm.gw",
        }


def test_a_bench_config_carrying_keys_removed_in_0_20_0_still_loads(tmp_path):
    """Keys and tables deleted from the models must not break benches that still carry them.

    `[switch].search_replace` was removed as a key, and `[registry]` as a whole table, in
    0.20.0. `import_from_toml` splats each TOML table into its model, so a stale KEY used to make
    every command that loads the bench die with a pydantic ValidationError while the models were
    `extra="forbid"` (now `extra="allow"`, so a stale key is simply retained as an unknown extra
    rather than raising). `REMOVED_CONFIG_KEYS`/`REMOVED_CONFIG_TABLES` no longer filter either
    one out of the read path -- retention plus the version-gated warning covers that now, the
    same as any other stray -- they only drive `_drop_removed_config_keys`'s on-disk strip during
    the actual 0.20.0 migration (`migrate_0_20_0.py`). `search_replace` was deleted once before
    on the grounds that nothing read it, and it took down `fm info` and `fm ssl list` on a live
    bench whose config carried `search_replace = true`; `[registry]` went entirely, since every
    field in it existed only to run `docker login`, which docker already owns.
    """
    path = tmp_path / "bench_config.toml"
    path.write_text(
        _BASE + '[switch]\nmigrate = true\nsearch_replace = true\n\n[registry]\nregistry = "ghcr.io/acme"\n'
    )

    cfg = BenchConfig.import_from_toml(path)

    assert cfg.switch is not None
    assert cfg.switch.migrate is True
    # Retained, not filtered: a pre-migration bench that still carries either one keeps it on the
    # next save (`fm never deletes a key it does not understand`), and the version-gated warning
    # is silent about both until `migrated_to` catches up.
    assert collect_unknown_keys(cfg.switch) == ["search_replace"]
    assert collect_unknown_keys(cfg) == ["registry", "switch.search_replace"]


def test_switch_config_now_retains_a_genuinely_unknown_key_instead_of_rejecting_it(tmp_path):
    """`SwitchConfig` moved from `extra="forbid"` to `extra="allow"`: a key that is not one of the
    compatibility names above, and not in `REMOVED_CONFIG_KEYS`, is retained rather than rejected.
    The `--config` overlay refusing a typo before it ever reaches this model is a later phase's
    concern; this only pins that the model itself no longer raises.
    """
    path = tmp_path / "bench_config.toml"
    path.write_text(_BASE + "[switch]\nmigrate = true\nserch_replace = true\n")

    cfg = BenchConfig.import_from_toml(path)

    assert cfg.switch.migrate is True
    assert collect_unknown_keys(cfg.switch) == ["serch_replace"]


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


class TestUnrecognisedKeysWarnRatherThanVanish:
    """A key `import_from_toml` does not read used to disappear with no signal at all: the input
    dict below names every key it wants and silently drops the rest. `fm list`/`fm bake`/`fm
    switch`/`fm maintenance` skip the migration gate, so this must warn rather than raise --
    the same tradeoff `TestStaleDeployStateKeysWarnLoudly` already makes for the deploy_state
    rename.

    Every fixture that expects a warning stamps `[migration_state].migrated_to` at fm's current
    version: the warning itself is version-gated (Phase 5), silent on a bench that has simply
    never been migrated, so proving "a typo warns" needs a bench the gate treats as current.
    """

    def _current(self) -> str:
        from frappe_manager.utils.helpers import get_current_fm_version

        return f'\n[migration_state]\nmigrated_to = "{get_current_fm_version()}"\n'

    def _warn(self, tmp_path, text: str):
        from unittest.mock import MagicMock

        from frappe_manager.output_manager import set_global_output_handler
        from frappe_manager.output_manager.base import OutputHandler

        handler = MagicMock(spec=OutputHandler)
        set_global_output_handler(handler)
        try:
            bc = _import(tmp_path, text)
        finally:
            set_global_output_handler(None)
        return bc, handler

    def test_an_unknown_top_level_key_warns(self, tmp_path):
        bc, handler = self._warn(tmp_path, _BASE + "typoed_kee = true\n" + self._current())

        assert bc.name == "dev.localhost"  # loads regardless
        handler.warning.assert_called_once()
        message = handler.warning.call_args.args[0]
        assert "dev.localhost" in message
        assert "typoed_kee" in message

    def test_a_misspelled_table_name_warns(self, tmp_path):
        bc, handler = self._warn(tmp_path, _BASE + "[swithc]\nmigrate = true\n" + self._current())

        assert bc.switch is None  # the misspelled table is never read into the real field
        handler.warning.assert_called_once()
        assert "swithc" in handler.warning.call_args.args[0]

    def test_no_warning_for_an_ordinary_config(self, tmp_path):
        _, handler = self._warn(tmp_path, _BASE + "[switch]\nmigrate = true\n")

        handler.warning.assert_not_called()

    def test_a_retired_key_and_the_retired_table_do_not_warn(self, tmp_path):
        """A bench that has not been migrated yet keeps loading quietly, whatever it still
        carries: the version gate (Phase 5) is silent about EVERY unrecognised key while
        `migrated_to` is behind fm's current version, not just `REMOVED_CONFIG_KEYS`/
        `REMOVED_CONFIG_TABLES` (which now only drive the migration's own on-disk strip). No
        `[migration_state]` here at all, which is every un-migrated bench's actual shape."""
        bc, handler = self._warn(
            tmp_path,
            _BASE + '[switch]\nmigrate = true\nsearch_replace = true\n\n[registry]\nregistry = "ghcr.io/acme"\n',
        )

        assert bc.switch.migrate is True
        handler.warning.assert_not_called()

    def test_an_unknown_deploy_state_key_warns(self, tmp_path):
        """The same hole one level down: `[deploy_state]` is read the same hand-written way."""
        bc, handler = self._warn(
            tmp_path,
            _BASE + '\n[deploy_state]\ncurrent_image = "v1"\ncurent_image = "typo"\n' + self._current(),
        )

        assert bc.deploy_state.current_image == "v1"
        handler.warning.assert_called_once()
        message = handler.warning.call_args.args[0]
        assert "deploy_state" in message
        assert "curent_image" in message

    def test_an_unknown_ssl_key_warns(self, tmp_path):
        """The same hole one level down as [deploy_state]: [ssl] is read the same hand-written
        way (`certificates`/`dns_providers`), so a typo there needs the identical guard."""
        bc, handler = self._warn(tmp_path, _BASE + "\n[ssl]\ncertificatess = []\n" + self._current())

        assert bc.ssl_certificates == []  # the misspelled key is never read
        handler.warning.assert_called_once()
        message = handler.warning.call_args.args[0]
        assert "ssl" in message
        assert "certificatess" in message


class TestPreMigrationBenchConfigNeverWarns:
    """The regression this fixes: `admin_tools_username`/`admin_tools_password`,
    `alias_domains`, and `[database]` are all top-level names fm itself wrote at 0.19.x or
    earlier, relocated (not retired) by the unreleased `migrate_0_20_0` migration. Every
    existing host is pre-migration while that migration is unreleased, and `fm list`/`fm bake`/
    `fm switch`/`fm maintenance` read a bench's config before offering to run it -- so each of
    these four names used to produce a fabricated "check for a typo" warning on the very first
    load of every bench on a host.

    Phase 5 replaced the hand-list that used to exempt exactly these four spellings
    (`RELOCATED_CONFIG_KEYS`) with a version check: silent while `[migration_state].migrated_to`
    is behind fm's current version, regardless of WHICH names are unrecognised, because the
    operator's next instruction is `fm migrate` and naming a key that command is about to move or
    strip is noise. That is strictly more general than the four-name list it replaced -- a fifth
    pre-migration name never hand-added to a list cannot be missed, because nothing is looked up
    by name any more.
    """

    # The shape `migrate_0_19_0.py` actually produces: `alias_domains` written at the top level
    # (`_add_new_config_fields`), `admin_tools_username`/`admin_tools_password` from an even
    # earlier version untouched by that migration, and a site-keyed `[database]` table
    # (`_write_sites_table`'s docstring: "the [database] table already had a site as its key").
    # No `[migration_state]` at all: a bench that has never been migrated, which is every bench
    # while 0.20.0 is unreleased.
    _PRE_0_20_0_SHAPED = (
        _BASE
        + 'admin_tools_username = "admin"\n'
        + 'admin_tools_password = "secret123"\n'
        + 'alias_domains = ["alias.example.com"]\n'
        + '\n[database."dev.localhost"]\n'
        + 'host = "10.0.0.5"\n'
        + 'name = "dev_localhost"\n'
    )

    def test_loads_with_zero_warnings(self, tmp_path):
        from unittest.mock import MagicMock

        from frappe_manager.output_manager import set_global_output_handler
        from frappe_manager.output_manager.base import OutputHandler

        handler = MagicMock(spec=OutputHandler)
        set_global_output_handler(handler)
        try:
            bc = _import(tmp_path, self._PRE_0_20_0_SHAPED)
        finally:
            set_global_output_handler(None)

        assert bc.name == "dev.localhost"  # loads regardless
        handler.warning.assert_not_called()
        # Retained, not dropped: the version gate silences the WARNING, not the data. A bench
        # that never migrates keeps every one of these on the next save, same as any other typo.
        assert collect_unknown_keys(bc) == ["admin_tools_password", "admin_tools_username", "alias_domains", "database"]

    def test_at_current_version_every_stray_including_the_legacy_ones_warns_together(self, tmp_path):
        """Once `migrated_to` reaches fm's current version, the silence lifts for ALL of them at
        once, in one message alongside a genuine typo sitting next to them -- there is no name
        left on this side that gets special treatment, because the gate is on the bench's
        version, never on which key it is."""
        from unittest.mock import MagicMock

        from frappe_manager.output_manager import set_global_output_handler
        from frappe_manager.output_manager.base import OutputHandler
        from frappe_manager.utils.helpers import get_current_fm_version

        shaped = self._PRE_0_20_0_SHAPED.replace(
            '\n[database."dev.localhost"]', '\ntypoed_kee = true\n\n[database."dev.localhost"]'
        )
        shaped += f'\n[migration_state]\nmigrated_to = "{get_current_fm_version()}"\n'

        handler = MagicMock(spec=OutputHandler)
        set_global_output_handler(handler)
        try:
            bc = _import(tmp_path, shaped)
        finally:
            set_global_output_handler(None)

        assert bc.name == "dev.localhost"
        handler.warning.assert_called_once()
        message = handler.warning.call_args.args[0]
        for key in ("admin_tools_password", "admin_tools_username", "alias_domains", "database", "typoed_kee"):
            assert key in message


class TestVersionGateIsolatesATypoFromMigrationNoise:
    """The finding that motivated Phase 5: a bench that has simply never been migrated must not
    have its typo-detection swamped by (or conflated with) the pre-migration shape it is still
    carrying. Same file, two versions: silent while behind, and naming only the ACTUAL typo once
    current -- not the version bump itself, which is not a key at all.
    """

    _CLEAN_CURRENT_SCHEMA = _BASE + "[switch]\nmigrate = true\n"

    def test_a_legacy_un_migrated_config_produces_zero_warnings(self, tmp_path):
        from unittest.mock import MagicMock

        from frappe_manager.output_manager import set_global_output_handler
        from frappe_manager.output_manager.base import OutputHandler

        handler = MagicMock(spec=OutputHandler)
        set_global_output_handler(handler)
        try:
            _import(tmp_path, self._CLEAN_CURRENT_SCHEMA)
        finally:
            set_global_output_handler(None)

        handler.warning.assert_not_called()

    def test_the_same_file_at_current_version_with_a_typo_warns_about_the_typo_only(self, tmp_path):
        from unittest.mock import MagicMock

        from frappe_manager.output_manager import set_global_output_handler
        from frappe_manager.output_manager.base import OutputHandler
        from frappe_manager.utils.helpers import get_current_fm_version

        shaped = (
            _BASE
            + "typoed_stray = true\n"
            + f'\n[migration_state]\nmigrated_to = "{get_current_fm_version()}"\n'
            + "\n[switch]\nmigrate = true\n"
        )
        handler = MagicMock(spec=OutputHandler)
        set_global_output_handler(handler)
        try:
            _import(tmp_path, shaped)
        finally:
            set_global_output_handler(None)

        handler.warning.assert_called_once()
        assert handler.warning.call_args.args[0].endswith(
            "has unrecognised key(s) typoed_stray; check for a typo, since fm will not use them."
        )


class TestNoWarningReachesTheTerminalDuringShellCompletion:
    """`warn_or_log`'s old premise -- that no output handler is attached during completion -- is
    false: `cli_entrypoint()` (main.py) installs a `RichOutputHandler` before `app()` runs, and
    completion dispatches from inside `app()`. So an unrecognised key used to repaint a warning
    into the operator's shell prompt on every single TAB. `_FM_COMPLETE`, the env var click/typer
    set while dispatching a completion request, not "no handler", is what has to silence it.
    """

    def test_import_from_toml_is_silent_on_both_streams_during_completion(self, tmp_path, monkeypatch, capsys):
        from frappe_manager.output_manager import set_global_output_handler
        from frappe_manager.output_manager.rich_output import RichOutputHandler

        monkeypatch.setenv("_FM_COMPLETE", "zsh_complete")

        # The real handler cli_entrypoint() attaches, not a mock: a mock would hide that a
        # handler being attached is exactly what used to leak this warning.
        set_global_output_handler(RichOutputHandler())
        try:
            bc = _import(tmp_path, _BASE + "typoed_kee = true\n")
        finally:
            set_global_output_handler(None)

        assert bc.name == "dev.localhost"  # still loads regardless
        captured = capsys.readouterr()
        assert captured.out == ""
        assert captured.err == ""

    def test_the_real_completion_callback_stays_silent_too(self, tmp_path, monkeypatch, capsys):
        """Drives the actual function click/typer call for `fm auth BENCH/<TAB>`
        (`bench_site_autocompletion_callback`), which loads the bench's config on the way to
        listing its sites -- the exact path the review reproduced the leak on.
        """
        from frappe_manager.output_manager import set_global_output_handler
        from frappe_manager.output_manager.rich_output import RichOutputHandler
        from frappe_manager.utils import callbacks

        bench_dir = tmp_path / "leaky.localhost"
        bench_dir.mkdir()
        (bench_dir / "bench_config.toml").write_text(
            'name = "leaky.localhost"\ndeveloper_mode = true\nadmin_tools = true\n'
            'environment = "dev"\ntypoed_kee = true\n\n[sites."leaky.localhost"]\n'
        )
        monkeypatch.setattr(callbacks, "CLI_BENCHES_DIRECTORY", tmp_path)
        monkeypatch.setenv("_FM_COMPLETE", "zsh_complete")

        set_global_output_handler(RichOutputHandler())
        try:
            suggestions = callbacks.bench_site_autocompletion_callback("leaky.localhost/")
        finally:
            set_global_output_handler(None)

        assert suggestions == ["leaky.localhost/leaky.localhost"]  # completion still works
        captured = capsys.readouterr()
        assert captured.out == ""
        assert captured.err == ""


class TestUnknownKeysRoundTripLosslessly:
    """`extra="allow"` means an unknown key is retained data, not a dropped one: `fm` never
    deletes a line it does not understand, because pruning it would erase the evidence an
    operator needs to find and fix their own typo. This is a property of `export_to_toml`
    calling `model_dump()`, which includes `model_extra` by default -- there is no separate
    prune step of fm's own that could start dropping it, so a future switch to
    `model_dump(exclude_unset=True)` or an explicit `exclude` would silently break the guarantee
    these tests pin.

    Every test here asserts the stray survives against the ORIGINAL source text, never only
    cycle-to-cycle: a key deleted during the FIRST cycle makes cycle one and cycle two
    byte-identical, so `first == second` alone would pass on a reader that silently drops the
    key just as readily as on one that keeps it. `[ssl]`/`[deploy_state]` are exactly the shape
    that mistake would hide, since neither is a model-backed field the way `[switch]` and
    `[[ssl.certificates]]` already were -- both needed their own retention mechanism (see
    `_ssl_unknown` and `DeployState`'s retained remainder in bench_config.py), and a test that
    only checked the fixed point would have passed before that mechanism existed too.
    """

    def test_a_switch_table_stray_key_survives_two_load_save_cycles_unchanged(self, tmp_path):
        path = tmp_path / "bench_config.toml"
        original = _BASE + "[switch]\nmigrate = true\nswitch_typo_key = 'stray-in-switch'\n"
        path.write_text(original)

        BenchConfig.import_from_toml(path).export_to_toml(path)
        first = path.read_text()
        BenchConfig.import_from_toml(path).export_to_toml(path)
        second = path.read_text()

        assert "switch_typo_key" in first, "must survive against the ORIGINAL, not just cycle-to-cycle"
        assert first == second, "a stray key must not drift position across repeated saves"
        assert collect_unknown_keys(BenchConfig.import_from_toml(path).switch) == ["switch_typo_key"]

    def test_a_certificate_stray_key_survives_while_its_retired_neighbour_is_dropped(self, tmp_path):
        # `api_token` is RETIRED_CERTIFICATE_KEYS: dropped by `_drop_retired_keys` regardless of
        # extra="allow". `cert_typo_key` is not retired: it must survive both fields sitting in
        # the same table, proving the retired-key stripper does not over-reach onto a neighbour.
        path = tmp_path / "bench_config.toml"
        path.write_text(
            _BASE + "[[ssl.certificates]]\n"
            'domain = "dev.localhost"\n'
            'ssl_type = "letsencrypt"\n'
            'challenge_type = "http01"\n'
            'api_token = "should-be-dropped"\n'
            "cert_typo_key = 'stray-should-survive'\n"
        )

        BenchConfig.import_from_toml(path).export_to_toml(path)
        first = path.read_text()
        BenchConfig.import_from_toml(path).export_to_toml(path)
        second = path.read_text()

        assert "cert_typo_key" in first, "must survive against the ORIGINAL, not just cycle-to-cycle"
        assert "api_token" not in first
        assert first == second
        cert = BenchConfig.import_from_toml(path).ssl_certificates[0]
        assert not hasattr(cert, "api_token")
        assert collect_unknown_keys(cert) == ["cert_typo_key"]

    def test_an_ssl_table_stray_key_survives_two_load_save_cycles_unchanged(self, tmp_path):
        """The hand-read table with no model of its own (`_ssl_unknown`): `ssl_table` in
        `export_to_toml` is rebuilt from `ssl_certificates`/`dns_providers` alone, which is
        exactly the prune that erased this stray before `_ssl_unknown` existed -- a two-cycle
        fixed point alone would not have caught that, since a bench with the bug reaches the
        same (wrong) fixed point on its very first save.
        """
        path = tmp_path / "bench_config.toml"
        path.write_text(_BASE + "[ssl]\ncertificatess = []\n")

        BenchConfig.import_from_toml(path).export_to_toml(path)
        first = path.read_text()
        BenchConfig.import_from_toml(path).export_to_toml(path)
        second = path.read_text()

        assert "certificatess" in first, "must survive against the ORIGINAL, not just cycle-to-cycle"
        assert first == second
        assert BenchConfig.import_from_toml(path).hand_read_unknown_keys() == ["ssl.certificatess"]

    def test_a_deploy_state_stray_key_survives_two_load_save_cycles_unchanged(self, tmp_path):
        """`DeployState` is built from four named kwargs, not a splat -- before its retained
        remainder existed, a stray here never reached the model at all, so `export_to_toml`
        (which rebuilds `[deploy_state]` from the model) silently dropped it on the FIRST save.
        Asserted against the original for the same reason as the `[ssl]` case above.
        """
        path = tmp_path / "bench_config.toml"
        path.write_text(_BASE + '[deploy_state]\ncurrent_image = "repo:tag"\nds_typo = "boom"\n')

        BenchConfig.import_from_toml(path).export_to_toml(path)
        first = path.read_text()
        BenchConfig.import_from_toml(path).export_to_toml(path)
        second = path.read_text()

        assert "ds_typo" in first, "must survive against the ORIGINAL, not just cycle-to-cycle"
        assert first == second
        assert collect_unknown_keys(BenchConfig.import_from_toml(path).deploy_state) == ["ds_typo"]

    def test_a_dns_provider_label_with_only_a_typo_survives_two_load_save_cycles_unchanged(self, tmp_path):
        """`export_to_toml` used to gate a label's emission on `provider_config.exists` alone (a
        real credential, `api_token` or `api_key`), so a label whose ONLY content is a typo'd
        field name had no real credential, was never written, and vanished on the very first
        save -- the identical defect the equivalent gate in metadata_manager.py had. Widened to
        `exists or model_extra`, mirroring that fix so the two readers cannot diverge.
        """
        path = tmp_path / "bench_config.toml"
        path.write_text(_BASE + '[ssl.dns_providers.acct_a]\ntypo_only_field = "boom"\n')

        BenchConfig.import_from_toml(path).export_to_toml(path)
        first = path.read_text()
        BenchConfig.import_from_toml(path).export_to_toml(path)
        second = path.read_text()

        assert "typo_only_field" in first, "must survive against the ORIGINAL, not just cycle-to-cycle"
        assert first == second
        cfg = BenchConfig.import_from_toml(path)
        assert collect_unknown_keys(cfg.dns_providers["acct_a"]) == ["typo_only_field"]

    def test_a_top_level_stray_of_every_awkward_toml_shape_survives_two_load_save_cycles_unchanged(self, tmp_path):
        """Exercises `unwrap_toml_value` at the top-level retained remainder against every shape
        named in its own docstring: a quoted string, a sub-table, an array of tables, a datetime,
        and a dotted key. Asserted against the ORIGINAL text, not only a cycle-to-cycle fixed
        point, for the same reason as every other test in this class -- and asserted by EXACT
        type on the reloaded model, not just by text presence: a value still wrapped in a
        tomlkit `Item` would pass every text assertion below unchanged (tomlkit's `Item` types
        already subclass their plain equivalents), so only the type check would catch
        `unwrap_toml_value` reverted to a no-op.
        """
        path = tmp_path / "bench_config.toml"
        original = (
            _BASE
            + 'stray_str = "hello \\"world\\" quoted"\n'
            + "stray_dt = 1979-05-27T07:32:00Z\n"
            + '"stray.dotted.key" = "dotted-value"\n'
            + "\n[stray_sub]\na = 1\nb = 'nested'\n"
            + "\n[[stray_aot]]\nx = 1\n[[stray_aot]]\nx = 2\n"
        )
        path.write_text(original)

        BenchConfig.import_from_toml(path).export_to_toml(path)
        first = path.read_text()
        BenchConfig.import_from_toml(path).export_to_toml(path)
        second = path.read_text()

        for marker in ("stray_str", "hello", "stray_dt", "1979-05-27", "stray.dotted.key", "stray_sub", "stray_aot"):
            assert marker in first, f"{marker!r} must survive against the ORIGINAL, not just cycle-to-cycle"
        assert first == second

        extra = BenchConfig.import_from_toml(path).model_extra or {}
        assert type(extra["stray_str"]) is str
        assert extra["stray_str"] == 'hello "world" quoted'
        assert type(extra["stray_dt"]) is datetime.datetime
        assert extra["stray_dt"] == datetime.datetime(1979, 5, 27, 7, 32, tzinfo=datetime.UTC)
        assert type(extra["stray.dotted.key"]) is str
        assert extra["stray.dotted.key"] == "dotted-value"
        assert type(extra["stray_sub"]) is dict
        assert extra["stray_sub"] == {"a": 1, "b": "nested"}
        assert type(extra["stray_aot"]) is list
        assert extra["stray_aot"] == [{"x": 1}, {"x": 2}]
        assert collect_unknown_keys(BenchConfig.import_from_toml(path)) == sorted(
            ["stray_str", "stray_dt", "stray.dotted.key", "stray_sub", "stray_aot"]
        )


class TestVersionGateNeverRaisesOnAnOddMigratedTo:
    """The version gate replacing `RELOCATED_CONFIG_KEYS` (`TestVersionGateIsolatesATypoFromMigrationNoise`
    above) reads `[migration_state].migrated_to` on every load that reaches `_bench_is_pre_migration`, on
    every command that skips the migration gate (`fm list`/`bake`/`switch`/`maintenance`). Two crash sites
    fed off the same untrusted value: `packaging.version.Version` raised `InvalidVersion` on anything not
    PEP 440, and `MigrationState.migrated_to: str | None` rejected a TOML-native non-string value (a bare
    date, a bool, a nested table...) with a `pydantic.ValidationError` before the gate was even reached.
    One bad `migrated_to` in one bench's file used to take every bench on the host down. An unparseable or
    oddly-typed version is treated exactly like an ABSENT one: fm cannot tell whether it is ahead of or
    behind, so it stays silent -- never raises, never warns -- until `fm migrate` writes one it can parse.
    """

    def _loads_silently(self, tmp_path, migration_state_body: str) -> BenchConfig:
        from unittest.mock import MagicMock

        from frappe_manager.output_manager import set_global_output_handler
        from frappe_manager.output_manager.base import OutputHandler

        handler = MagicMock(spec=OutputHandler)
        set_global_output_handler(handler)
        try:
            bc = _import(tmp_path, _BASE + "typo_key = true\n\n[migration_state]\n" + migration_state_body)
        finally:
            set_global_output_handler(None)
        assert bc.name == "dev.localhost"  # loaded, never raised
        handler.warning.assert_not_called()
        return bc

    def test_a_free_text_migrated_to_loads_and_warns_nothing(self, tmp_path):
        """The exact scenario reported: an unrecognised key AND a non-PEP-440 `migrated_to`."""
        self._loads_silently(tmp_path, 'migrated_to = "nightly"\n')

    def test_a_bare_toml_date_loads_and_warns_nothing(self, tmp_path):
        """tomlkit hands a bare (unquoted) TOML date back as `datetime.date`, not `str`."""
        self._loads_silently(tmp_path, "migrated_to = 2024-01-15\n")

    def test_a_bare_toml_datetime_loads_and_warns_nothing(self, tmp_path):
        self._loads_silently(tmp_path, "migrated_to = 2024-01-15T10:00:00\n")

    def test_a_boolean_migrated_to_loads_and_warns_nothing(self, tmp_path):
        self._loads_silently(tmp_path, "migrated_to = true\n")

    def test_a_nested_table_migrated_to_loads_and_warns_nothing(self, tmp_path):
        """A stray `[migration_state.migrated_to]` sub-table, e.g. a fat-fingered nested key."""
        self._loads_silently(tmp_path, "[migration_state.migrated_to]\nx = 1\n")

    def test_an_array_migrated_to_loads_and_warns_nothing(self, tmp_path):
        self._loads_silently(tmp_path, "migrated_to = [1, 2]\n")

    def test_migration_state_present_but_empty_loads_and_warns_nothing(self, tmp_path):
        self._loads_silently(tmp_path, "")

    def test_an_empty_string_migrated_to_loads_and_warns_nothing(self, tmp_path):
        self._loads_silently(tmp_path, 'migrated_to = ""\n')

    def test_an_epoch_and_a_local_segment_version_compare_without_raising(self, tmp_path):
        """Both are valid PEP 440 (an epoch marker, a local version segment) and never touch the
        except branch at all -- included to prove the try/except addition does not change
        behaviour for a version `Version()` actually accepts. Both sit far below any released fm
        version, so the comparison lands deterministically on the silent, pre-migration side.
        """
        from unittest.mock import MagicMock

        from frappe_manager.output_manager import set_global_output_handler
        from frappe_manager.output_manager.base import OutputHandler

        for label, migrated_to in (("epoch", "0!0.0.1"), ("local", "0.0.1+local")):
            sub = tmp_path / label
            sub.mkdir()
            handler = MagicMock(spec=OutputHandler)
            set_global_output_handler(handler)
            try:
                bc = _import(
                    sub,
                    _BASE + f'typo_key = true\n\n[migration_state]\nmigrated_to = "{migrated_to}"\n',
                )
            finally:
                set_global_output_handler(None)
            assert bc.name == "dev.localhost"
            handler.warning.assert_not_called()

    def test_last_migration_date_as_a_bare_toml_date_does_not_raise(self, tmp_path):
        """The sibling field on the same model, coerced the same way. A real (parseable, current)
        `migrated_to` keeps the gate itself out of this assertion."""
        from unittest.mock import MagicMock

        from frappe_manager.output_manager import set_global_output_handler
        from frappe_manager.output_manager.base import OutputHandler
        from frappe_manager.utils.helpers import get_current_fm_version

        handler = MagicMock(spec=OutputHandler)
        set_global_output_handler(handler)
        try:
            bc = _import(
                tmp_path,
                _BASE + "\n[migration_state]\n"
                f'migrated_to = "{get_current_fm_version()}"\n'
                "last_migration_date = 2024-01-15\n",
            )
        finally:
            set_global_output_handler(None)
        assert bc.name == "dev.localhost"
        handler.warning.assert_not_called()  # no unrecognised key here, only the odd type
        assert bc.migration_state.last_migration_date == "2024-01-15"  # coerced, not dropped

    def test_an_odd_migrated_to_type_is_retained_as_its_string_form_not_dropped(self, tmp_path):
        """fm never deletes a key it does not understand: an odd TYPE on a recognised key is
        coerced to a string it can round-trip, not silently blanked to None."""
        path = tmp_path / "bench_config.toml"
        path.write_text(_BASE + "\n[migration_state]\nmigrated_to = 2024-01-15\n")

        bc = BenchConfig.import_from_toml(path)

        assert bc.migration_state is not None
        assert bc.migration_state.migrated_to == "2024-01-15"
