"""Contract tests for the image-based deploy config on BenchConfig.

Defends: the `deployment_mode` two-axis default, the TOML round-trip of the nested
`deploy`/`build`/`registry`/`remote` tables, `extra="forbid"` on the deploy models,
and backward compatibility (a bench_config.toml with no deploy keys loads as mount).
"""

import pytest
import tomlkit
from pydantic import ValidationError

from frappe_manager.site_manager.bench_config import (
    BenchConfig,
    DeployBuildConfig,
    DeployConfig,
    DeploymentMode,
    FMBenchEnvType,
    RegistryConfig,
    RemoteConfig,
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
        deployment_mode=DeploymentMode.image,
        deploy=DeployConfig(image="ghcr.io/acme/x", maintenance_mode_phases=["migrate"]),
        build=DeployBuildConfig(base_image="ghcr.io/rtcamp/frappe-manager-frappe", python_version="3.11"),
        registry=RegistryConfig(registry="ghcr.io/acme", username="u", distribution="save_load"),
        remote=RemoteConfig(ssh_server="host.example", ssh_user="frappe", ssh_port=2222),
    )


def test_deployment_mode_defaults_to_mount(tmp_path):
    bc = _mount_bench(tmp_path / "bench_config.toml")
    assert bc.deployment_mode == DeploymentMode.mount
    assert bc.deploy is None


def test_missing_deploy_keys_loads_as_mount(tmp_path):
    # A pre-existing bench_config.toml with no deploy keys must still import as mount.
    path = tmp_path / "bench_config.toml"
    doc = tomlkit.document()
    doc["name"] = "legacy.localhost"
    doc["developer_mode"] = True
    doc["admin_tools"] = True
    doc["environment_type"] = "dev"
    path.write_text(tomlkit.dumps(doc))

    bc = BenchConfig.import_from_toml(path)
    assert bc.deployment_mode == DeploymentMode.mount
    assert bc.deploy is None
    assert bc.build is None
    assert bc.registry is None
    assert bc.remote is None


def test_image_deploy_roundtrip(tmp_path):
    path = tmp_path / "bench_config.toml"
    assert _image_bench(path).export_to_toml(path) is True

    bc = BenchConfig.import_from_toml(path)
    assert bc.deployment_mode == DeploymentMode.image
    assert bc.deploy is not None
    assert bc.deploy.image == "ghcr.io/acme/x"
    assert bc.deploy.maintenance_mode_phases == ["migrate"]
    assert bc.deploy.migrate is True
    assert bc.build is not None
    assert bc.build.python_version == "3.11"
    assert bc.registry is not None
    assert bc.registry.distribution == "save_load"
    assert bc.remote is not None
    assert bc.remote.ssh_port == 2222


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
        deployment_mode=DeploymentMode.image,
        deploy=DeployConfig(image="ghcr.io/acme/a", maintenance_mode_phases=[]),
    )
    bc.export_to_toml(path)

    reloaded = BenchConfig.import_from_toml(path)
    assert reloaded.deploy is not None
    assert reloaded.deploy.maintenance_mode_phases == []


def test_deploy_config_forbids_unknown_keys():
    with pytest.raises(ValidationError):
        DeployConfig(image="ghcr.io/acme/x", bogus_key=True)


def test_registry_config_forbids_unknown_keys():
    with pytest.raises(ValidationError):
        RegistryConfig(registry="ghcr.io/acme", nope="x")
