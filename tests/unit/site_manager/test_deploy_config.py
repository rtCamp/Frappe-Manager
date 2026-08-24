"""Contract tests for the image-based deploy config on BenchConfig.

The new schema splits the old monolithic deploy config into a top-level image
identity, a `[switch]` migrate pipeline (SwitchConfig), a `[build]` image build
config (BuildConfig), and a `[registry]` transport config. These tests lock the
round-trip of each.
"""

import pytest
import tomlkit
from pydantic import ValidationError

from frappe_manager.site_manager.bench_config import (
    BenchConfig,
    BenchRuntime,
    BuildConfig,
    FMBenchEnvType,
    RegistryConfig,
    SwitchConfig,
)


def _mount_bench(path):
    return BenchConfig(
        name="y.localhost",
        developer_mode=True,
        admin_tools=True,
        environment_type=FMBenchEnvType.dev,
        root_path=path,
    )


def _image_bench(path):
    return BenchConfig(
        name="x.localhost",
        developer_mode=False,
        admin_tools=False,
        environment_type=FMBenchEnvType.prod,
        root_path=path,
        runtime=BenchRuntime.image,
        image="ghcr.io/acme/x",
        switch=SwitchConfig(maintenance_mode_phases=["migrate"]),
        build=BuildConfig(base_image="ghcr.io/rtcamp/frappe-manager-frappe", python_version="3.11"),
        registry=RegistryConfig(registry="ghcr.io/acme", username="u", distribution="save_load"),
    )


def test_runtime_defaults_to_mount(tmp_path):
    bc = _mount_bench(tmp_path / "bench_config.toml")
    assert bc.runtime == BenchRuntime.mount
    assert bc.switch is None


def test_missing_deploy_keys_loads_as_mount(tmp_path):
    # A pre-existing bench_config.toml with no deploy keys must still import as mount.
    path = tmp_path / "bench_config.toml"
    doc = tomlkit.document()
    doc["name"] = "legacy.localhost"
    doc["developer_mode"] = True
    doc["admin_tools"] = True
    doc["environment"] = "dev"
    path.write_text(tomlkit.dumps(doc))

    bc = BenchConfig.import_from_toml(path)
    assert bc.runtime == BenchRuntime.mount
    assert bc.image is None
    assert bc.switch is None
    assert bc.build is None
    assert bc.registry is None


def test_image_deploy_roundtrip(tmp_path):
    path = tmp_path / "bench_config.toml"
    assert _image_bench(path).export_to_toml(path) is True

    bc = BenchConfig.import_from_toml(path)
    assert bc.runtime == BenchRuntime.image
    assert bc.image == "ghcr.io/acme/x"
    assert bc.switch is not None
    assert bc.switch.maintenance_mode_phases == ["migrate"]
    assert bc.switch.migrate is True
    assert bc.build is not None
    assert bc.build.python_version == "3.11"
    assert bc.registry is not None
    assert bc.registry.distribution == "save_load"


def test_additive_optout_empty_phases_roundtrip(tmp_path):
    # maintenance_mode_phases = [] is the operator-asserted backward-compatible opt-out;
    # it must survive the round-trip as an empty list, not fall back to the default.
    path = tmp_path / "bench_config.toml"
    bc = BenchConfig(
        name="a.localhost",
        developer_mode=False,
        admin_tools=False,
        environment_type=FMBenchEnvType.prod,
        root_path=path,
        runtime=BenchRuntime.image,
        image="ghcr.io/acme/a",
        switch=SwitchConfig(maintenance_mode_phases=[]),
    )
    bc.export_to_toml(path)

    reloaded = BenchConfig.import_from_toml(path)
    assert reloaded.switch is not None
    assert reloaded.switch.maintenance_mode_phases == []


def test_switch_config_forbids_unknown_keys():
    with pytest.raises(ValidationError):
        SwitchConfig(migrate=True, bogus_key=True)


def test_registry_config_forbids_unknown_keys():
    with pytest.raises(ValidationError):
        RegistryConfig(registry="ghcr.io/acme", nope="x")
