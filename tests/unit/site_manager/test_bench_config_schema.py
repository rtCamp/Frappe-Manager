"""Round-trip contract for the redesigned bench_config schema.

Locks the TOML shape: top-level `environment`/`image`, `[[apps]]` with per-app
`hooks`/`hooks.host`, `[monitoring.newrelic]`, `[switch]` + `[switch.hooks]`/
`[switch.hooks.host]`, `[build]`, and `[ssl]`
(`dns_challenge_providers` + `certificates`). Import + export + re-import must
preserve every value.
"""

import pytest
from pydantic import ValidationError

from frappe_manager.site_manager.bench_config import (
    BenchConfig,
    BenchRuntime,
    FMBenchEnvType,
    MonitoringConfig,
    NewRelicConfig,
)

_TOML = """
name = "fm.com"
developer_mode = false
admin_tools = false
environment = "prod"
runtime = "image"
image = "ghcr.io/fmcom/fm"
restart_policy = "unless-stopped"
alias_domains = ["www.fm.com"]

[[apps]]
name = "erpnext"
repo = "frappe/erpnext"
ref = "version-15"
hooks.before_deps = "bench pip check"
hooks.after_build = "./ci/upload.sh $APP"
hooks.host.before_build = "./ci/patch.sh"

[monitoring.newrelic]
enabled = true
license_key = "nrkey"

[switch]
migrate = true
maintenance_mode_phases = ["migrate"]
[switch.hooks]
before_migrate = "bench pre"
after_restart = "bench post"
[switch.hooks.host]
after_restart = "./ci/slack.sh"
[switch.common_site_config]
mail_server = "smtp.internal"

[build]
python_version = "3.12"
node_version = "20"


[ssl.dns_challenge_providers.cloudflare]
api_token = "cf-token"
[[ssl.certificates]]
domain = "fm.com"
ssl_type = "letsencrypt"
challenge_type = "dns01"
"""


def _assert_full(bc: BenchConfig):
    assert bc.name == "fm.com"
    assert bc.environment_type == FMBenchEnvType.prod
    assert bc.runtime == BenchRuntime.image
    assert bc.image == "ghcr.io/fmcom/fm"
    # NOTE: apps_list is input-only (excluded from export); asserted separately.
    assert bc.monitoring.newrelic.enabled is True
    assert bc.monitoring.newrelic.license_key == "nrkey"
    assert bc.switch.migrate is True
    assert bc.switch.maintenance_mode_phases == ["migrate"]
    assert bc.switch.hooks.before_migrate == "bench pre"
    assert bc.switch.hooks.after_restart == "bench post"
    assert bc.switch.hooks.host.after_restart == "./ci/slack.sh"
    assert bc.switch.common_site_config == {"mail_server": "smtp.internal"}
    assert bc.build.python_version == "3.12"
    assert bc.build.node_version == "20"
    assert [c.domain for c in bc.ssl_certificates] == ["fm.com"]
    assert list((bc.dns_providers or {}).keys()) == ["cloudflare"]


def test_import_new_schema(tmp_path):
    p = tmp_path / "bench_config.toml"
    p.write_text(_TOML)
    bc = BenchConfig.import_from_toml(p)
    _assert_full(bc)
    assert [a.name for a in bc.apps_list] == ["erpnext"]
    app = bc.apps_list[0]
    assert app.hooks.before_deps == "bench pip check"
    assert app.hooks.after_build == "./ci/upload.sh $APP"
    assert app.hooks.host.before_build == "./ci/patch.sh"


def test_export_reimport_roundtrip(tmp_path):
    p = tmp_path / "bench_config.toml"
    p.write_text(_TOML)
    bc = BenchConfig.import_from_toml(p)

    out = tmp_path / "out.toml"
    assert bc.export_to_toml(out) is True
    text = out.read_text()
    # Scalars must render before any table header (no bleed into [switch]).
    assert text.index("environment =") < text.index("[switch]")
    assert text.index("image =") < text.index("[switch]")
    # New table shape, not the old flat keys.
    assert "[monitoring.newrelic]" in text
    assert "[[ssl.certificates]]" in text
    assert "[ssl.dns_challenge_providers.cloudflare]" in text
    assert "environment_type" not in text
    assert "newrelic_enabled" not in text

    reimported = BenchConfig.import_from_toml(out)
    _assert_full(reimported)
    assert reimported.apps_list == []  # apps are input-only, not persisted on export


def test_mount_bench_has_no_pipeline_or_image_identity(tmp_path):
    p = tmp_path / "bench_config.toml"
    p.write_text('name = "dev.localhost"\ndeveloper_mode = true\nadmin_tools = true\nenvironment = "dev"\n')
    bc = BenchConfig.import_from_toml(p)
    assert bc.runtime == BenchRuntime.mount
    assert bc.switch is None
    assert bc.image is None
    assert bc.build is None


def test_export_reimport_preserves_the_monitoring_table(tmp_path):
    """`[monitoring.newrelic]` is now the only representation of the setting, in memory
    and on disk, so a bench built in code has to survive the write/read cycle through it."""
    bc = BenchConfig(
        name="nr.localhost",
        developer_mode=False,
        admin_tools=False,
        environment_type=FMBenchEnvType.prod,
        root_path=tmp_path / "bench_config.toml",
        monitoring=MonitoringConfig(newrelic=NewRelicConfig(enabled=True, license_key="nrkey")),
    )

    out = tmp_path / "out.toml"
    assert bc.export_to_toml(out) is True
    assert "[monitoring.newrelic]" in out.read_text()

    reimported = BenchConfig.import_from_toml(out)
    assert reimported.monitoring.newrelic.enabled is True
    assert reimported.monitoring.newrelic.license_key == "nrkey"


@pytest.mark.parametrize(
    "table",
    [
        pytest.param('[monitoring.newrelic]\nenabld = true\nlicense_key = "nrkey"\n', id="misspelled-key"),
        pytest.param("[monitoring.newrelick]\nenabled = true\n", id="misspelled-table"),
    ],
)
def test_a_misspelled_monitoring_key_is_refused(tmp_path, table):
    """Both of these used to load cleanly and leave NewRelic silently off: the loader
    hand-read monitoring.newrelic.enabled and dropped everything it did not recognise.
    `[monitoring]` is a model now, and the docs promise it rejects keys it does not define."""
    p = tmp_path / "bench_config.toml"
    p.write_text('name = "nr.localhost"\ndeveloper_mode = false\nadmin_tools = false\nenvironment = "prod"\n' + table)

    with pytest.raises(ValidationError):
        BenchConfig.import_from_toml(p)
