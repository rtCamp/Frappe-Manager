"""`collect_unknown_keys` walks a loaded config model and names every key pydantic retained under
`extra="allow"`, as a dotted path. It is exercised here against the real bench-side models
(`SwitchConfig`, `BuildConfig`, `WorkersConfig`, `AuthConfig`, `SSLCertificate`,
`DNSProviderConfig`) loaded through `BenchConfig.import_from_toml`, because the collector's job is
specifically to replace a load-time crash with a reportable path on THOSE models -- a small ad hoc
model would prove the walk works but not that it is wired to the config surface it exists for.

One case is the exception: `[output.colors]` (a `dict[str, str]` whose keys already contain dots,
e.g. `'fm.env.prod'`) lives on the GLOBAL config in `metadata_manager.py`, which this phase does not
touch. That shape is reproduced with a small local model instead, since the property under test --
building a path by joining segments rather than splitting a key string -- belongs to the collector,
not to any one model that happens to have a `dict[str, str]` field.
"""

from pydantic import BaseModel, ConfigDict

from frappe_manager.site_manager.bench_config import AppBuildHooks, BenchConfig, BuildHookScripts
from frappe_manager.ssl_manager import SUPPORTED_SSL_TYPES
from frappe_manager.ssl_manager.certificate import RETIRED_CERTIFICATE_KEYS, DevCertificate, SSLCertificate
from frappe_manager.ssl_manager.letsencrypt_certificate import CERTIFICATE_ADAPTER
from frappe_manager.utils.config_keys import collect_unknown_keys, declared_field, unwrap_toml_value

_BASE = 'name = "x.localhost"\ndeveloper_mode = false\nadmin_tools = false\nenvironment = "prod"\n'


def _bench(tmp_path, text: str) -> BenchConfig:
    path = tmp_path / "bench_config.toml"
    path.write_text(_BASE + text)
    return BenchConfig.import_from_toml(path)


def test_an_unknown_switch_key_is_collected_with_its_full_path_and_does_not_raise(tmp_path):
    bc = _bench(tmp_path, "[switch]\nmigrate = true\nswitch_typo_key = true\n")

    assert bc.switch.migrate is True
    assert collect_unknown_keys(bc) == ["switch.switch_typo_key"]


def test_an_unknown_build_key_is_collected_with_its_full_path_and_does_not_raise(tmp_path):
    bc = _bench(tmp_path, '[build]\npython_version = "3.12"\nbuild_typo_key = true\n')

    assert bc.build.python_version == "3.12"
    assert collect_unknown_keys(bc) == ["build.build_typo_key"]


def test_an_unknown_workers_key_is_collected_with_its_full_path_and_does_not_raise(tmp_path):
    bc = _bench(tmp_path, "[workers]\ndrain = false\nworkers_typo_key = true\n")

    assert bc.workers.drain is False
    assert collect_unknown_keys(bc) == ["workers.workers_typo_key"]


def test_an_unknown_auth_key_is_collected_with_its_full_path_and_does_not_raise(tmp_path):
    bc = _bench(tmp_path, "[auth]\nweb = true\nauth_typo_key = true\n")

    assert bc.auth.web is True
    assert collect_unknown_keys(bc) == ["auth.auth_typo_key"]


def test_a_key_nested_two_deep_inside_switch_hooks_host_reports_the_full_path(tmp_path):
    bc = _bench(
        tmp_path,
        "[switch]\nmigrate = true\n"
        '[switch.hooks]\nbefore_restart = "echo container"\n'
        '[switch.hooks.host]\nbefore_restart = "echo host"\nhost_typo_key = true\n',
    )

    assert bc.switch.hooks.host.before_restart == "echo host"
    assert collect_unknown_keys(bc) == ["switch.hooks.host.host_typo_key"]


def test_common_site_config_is_free_form_and_contributes_zero_collected_keys(tmp_path):
    # A key here matches no field on any model -- there is no model on the other side of a
    # dict[str, Any] to check it against, so it must never be reported as "unknown".
    bc = _bench(
        tmp_path,
        "[switch]\nmigrate = true\n"
        "[switch.common_site_config]\n"
        'mail_server = "smtp.internal"\n'
        "matches_no_model_field_anywhere = 1\n",
    )

    assert bc.switch.common_site_config == {
        "mail_server": "smtp.internal",
        "matches_no_model_field_anywhere": 1,
    }
    assert collect_unknown_keys(bc) == []


def test_a_dict_str_str_field_with_dotted_keys_contributes_zero_collected_keys():
    """Reproduces the `[output.colors]` shape (a global-config field this phase does not touch)
    against the collector directly: a dotted dict key must never be split into path segments,
    because there is nothing under it -- a plain string value -- for the walk to descend into."""

    class StyleMap(BaseModel):
        model_config = ConfigDict(extra="allow")

        colors: dict[str, str] = {}

    styles = StyleMap(colors={"fm.env.prod": "bold red", "fm.env.dev": "green"})

    assert collect_unknown_keys(styles) == []


def test_a_retired_certificate_key_contributes_zero_collected_keys(tmp_path):
    # Every RETIRED_CERTIFICATE_KEYS entry is dropped by `_drop_retired_keys` before pydantic ever
    # assigns it, so none of them may show up as "unknown" -- they are legal (if inert) on disk.
    retired_lines = "".join(f'{key} = "carried"\n' for key in sorted(RETIRED_CERTIFICATE_KEYS) if key != "toml_exclude")
    bc = _bench(
        tmp_path,
        '[[ssl.certificates]]\ndomain = "x.localhost"\nssl_type = "letsencrypt"\nchallenge_type = "dns01"\n'
        + retired_lines,
    )

    assert RETIRED_CERTIFICATE_KEYS.isdisjoint(bc.ssl_certificates[0].model_extra or {})
    assert collect_unknown_keys(bc) == []


def test_a_dict_of_models_region_reports_the_value_key_never_the_users_own_table_key(tmp_path):
    """`[sites."<name>"]` is `dict[str, SiteConfig]`: the site name is the user's own choice and
    must never itself be reported as unknown, only a genuinely unknown key found INSIDE that
    site's table."""
    bc = _bench(
        tmp_path,
        '[sites."x.localhost"]\nalias_domains = ["www.x.localhost"]\n'
        '[sites."x.localhost".database]\nhost = "db.example"\nname = "x_prod"\nsite_db_typo_key = true\n',
    )

    assert bc.sites["x.localhost"].database.host == "db.example"
    assert collect_unknown_keys(bc) == ["sites.x.localhost.database.site_db_typo_key"]


def test_a_dns_providers_dict_of_models_region_reports_the_value_key_never_the_label(tmp_path):
    """`[ssl.dns_providers."<label>"]` is `dict[str, DNSProviderConfig]`: the label is the
    user's own choice and must never itself be reported as unknown."""
    bc = _bench(
        tmp_path,
        '[ssl.dns_providers.acct-b]\napi_token = "tok"\ndns_typo_key = true\n',
    )

    assert bc.dns_providers["acct-b"].api_token == "tok"
    assert collect_unknown_keys(bc) == ["dns_providers.acct-b.dns_typo_key"]


def test_certificate_without_forbid_no_longer_raises_on_an_unknown_key():
    """The load-bearing behaviour change this phase makes: constructing a certificate with an
    unknown key succeeds and reports the key, rather than raising `ValidationError`."""
    cert = SSLCertificate(domain="a.com", ssl_type=SUPPORTED_SSL_TYPES.le, hstss="bad")

    assert collect_unknown_keys(cert) == ["hstss"]


def test_declared_field_blocks_a_stray_that_shadows_a_sibling_variants_field():
    """The read-side counterpart to `collect_unknown_keys`: a stray retained under `extra="allow"`
    must never be read back as though it were a field declared on the concrete type actually in
    hand. `dns_provider`/`delegation_cname`/`acme_client` are declared only on
    `LetsencryptSSLCertificate`; loaded onto a `dev` certificate they land in `model_extra`, and
    plain `getattr` resolves them anyway (`BaseModel.__getattr__` falls through to
    `__pydantic_extra__`).
    """
    cert = CERTIFICATE_ADAPTER.validate_python(
        {
            "domain": "a.example",
            "ssl_type": "dev",
            "dns_provider": "acct-b",
            "delegation_cname": "x.fm.gw",
            "acme_client": "zerossl",
        }
    )

    assert type(cert) is DevCertificate
    # Retained: tolerating a stray at load is the whole point of extra="allow".
    assert collect_unknown_keys(cert) == ["acme_client", "delegation_cname", "dns_provider"]
    # Still readable through plain getattr -- this IS the hazard declared_field closes.
    assert getattr(cert, "dns_provider", None) == "acct-b"

    # declared_field asks the TYPE first: none of these are real fields on DevCertificate.
    assert declared_field(cert, "dns_provider") is None
    assert declared_field(cert, "delegation_cname") is None
    assert declared_field(cert, "acme_client", "letsencrypt") == "letsencrypt"
    # Reading through it has no side effect on the model: still retained afterwards.
    assert collect_unknown_keys(cert) == ["acme_client", "delegation_cname", "dns_provider"]


def test_declared_field_still_reads_a_genuine_value_on_the_variant_that_declares_it():
    """Over-tightening risk: a REAL field on the variant that actually declares it must still work."""
    cert = CERTIFICATE_ADAPTER.validate_python(
        {
            "domain": "a.example",
            "ssl_type": "letsencrypt",
            "dns_provider": "cloudflare-prod",
            "delegation_cname": "cname.fm.gw",
            "acme_client": "acme.sh",
        }
    )

    assert declared_field(cert, "dns_provider") == "cloudflare-prod"
    assert declared_field(cert, "delegation_cname") == "cname.fm.gw"
    assert declared_field(cert, "acme_client", "letsencrypt") == "acme.sh"
    assert collect_unknown_keys(cert) == []


def test_declared_field_guards_a_second_unrelated_subsystem_the_same_way():
    """The hazard is not certificate-specific: `host` is declared only on `AppBuildHooks`, not its
    own base `BuildHookScripts`. A stray `host` retained on a bare `BuildHookScripts` must not read
    back as a nested hook-scripts sub-model -- exactly the shape `site_manager/hooks.py` guards
    against for the same reason a certificate probe does: the value ends up feeding a subprocess.
    """
    stray = BuildHookScripts.model_validate({"before_deps": "echo hi", "host": "not-a-real-host-block"})

    assert collect_unknown_keys(stray) == ["host"]
    assert getattr(stray, "host", None) == "not-a-real-host-block"
    assert declared_field(stray, "host") is None

    real = AppBuildHooks.model_validate({"before_deps": "echo hi", "host": {"before_deps": "echo host"}})
    assert declared_field(real, "host").before_deps == "echo host"


def test_unwrap_toml_value_strips_the_tomlkit_item_off_every_awkward_shape():
    """The guard `unwrap_toml_value` exists for: a value still wrapped in a tomlkit `Item`
    happens to pass an `isinstance` check against its plain equivalent (`tomlkit.items.String`
    IS a `str`), so a test that only asserted `isinstance` would pass whether or not this
    function does anything. Asserting the EXACT type is what would fail if the function were
    reverted to a no-op (`return value`): each parsed value below starts out as its tomlkit
    subclass, and only survives as its plain base type because `unwrap_toml_value` calls
    `.unwrap()`.
    """
    import datetime as dt_module

    import tomlkit

    doc = tomlkit.parse(
        'stray_str = "hello \\"world\\" quoted"\n'
        "stray_dt = 1979-05-27T07:32:00Z\n"
        '"stray.dotted.key" = "dotted-value"\n'
        '\n[stray_sub]\na = 1\nb = "nested"\n'
        "\n[[stray_aot]]\nx = 1\n[[stray_aot]]\nx = 2\n"
    )

    raw_str, raw_dt, raw_dotted, raw_sub, raw_aot = (
        doc["stray_str"],
        doc["stray_dt"],
        doc["stray.dotted.key"],
        doc["stray_sub"],
        doc["stray_aot"],
    )
    # Before unwrapping: every one of these is a tomlkit Item subclass, not the plain builtin.
    assert type(raw_str) is not str
    assert type(raw_dt) is not dt_module.datetime
    assert type(raw_dotted) is not str
    assert type(raw_sub) is not dict
    assert type(raw_aot) is not list

    assert unwrap_toml_value(raw_str) == 'hello "world" quoted'
    assert type(unwrap_toml_value(raw_str)) is str
    assert unwrap_toml_value(raw_dt) == dt_module.datetime(1979, 5, 27, 7, 32, tzinfo=dt_module.UTC)
    assert type(unwrap_toml_value(raw_dt)) is dt_module.datetime
    assert unwrap_toml_value(raw_dotted) == "dotted-value"
    assert type(unwrap_toml_value(raw_dotted)) is str
    assert unwrap_toml_value(raw_sub) == {"a": 1, "b": "nested"}
    assert type(unwrap_toml_value(raw_sub)) is dict
    assert unwrap_toml_value(raw_aot) == [{"x": 1}, {"x": 2}]
    assert type(unwrap_toml_value(raw_aot)) is list


def test_unwrap_toml_value_passes_a_plain_python_object_through_unchanged():
    """Not tomlkit-specific: a value with no `.unwrap` attribute (already plain, or never came
    from a tomlkit document at all) is returned as-is, the identical object."""
    plain: dict[str, int] = {"a": 1}

    assert unwrap_toml_value(plain) is plain
    assert unwrap_toml_value(None) is None
    assert unwrap_toml_value(42) == 42
