"""Contract tests for `fm create`'s image-mode wiring (#323).

Covers `_resolve_deploy_options` — the pure resolver that turns the create CLI
flags (--deployment-mode/--image/--registry/--distribution) into the
deployment_mode + [deploy]/[build]/[registry] config, plus a full
BenchConfig export/import round-trip proving a created image bench persists the
exact fields `fm bake`/`fm deploy` require.
"""

import pytest
import typer

from frappe_manager.commands.create import _resolve_deploy_options
from frappe_manager.site_manager.bench_config import (
    BenchConfig,
    DeploymentMode,
    FMBenchEnvType,
)


def _resolve(deployment_mode=None, image=None, registry=None, distribution="registry", python=None, node=None):
    return _resolve_deploy_options(deployment_mode, image, registry, distribution, python, node)


def test_default_is_mount_backward_compatible():
    # Plain `fm create` (and `--environment prod`, which does not touch this) stays mount.
    mode, deploy, build, registry = _resolve()
    assert mode == DeploymentMode.mount
    assert deploy is None
    assert build is None
    assert registry is None


def test_image_flag_implies_image_mode():
    mode, deploy, build, registry = _resolve(image="ghcr.io/acme/mybench")
    assert mode == DeploymentMode.image
    assert deploy is not None
    assert deploy.image == "ghcr.io/acme/mybench"
    assert build is None  # no --python/--node given
    assert registry is None


def test_explicit_image_mode_requires_image():
    with pytest.raises(typer.BadParameter):
        _resolve(deployment_mode=DeploymentMode.image)


def test_image_flag_rejected_in_mount_mode():
    with pytest.raises(typer.BadParameter):
        _resolve(deployment_mode=DeploymentMode.mount, image="ghcr.io/acme/x")


def test_registry_rejected_in_mount_mode():
    with pytest.raises(typer.BadParameter):
        _resolve(registry="ghcr.io/acme")


def test_bad_distribution_rejected():
    with pytest.raises(typer.BadParameter):
        _resolve(image="ghcr.io/acme/x", registry="ghcr.io/acme", distribution="bogus")


def test_build_config_only_when_versions_given():
    _, _, build_none, _ = _resolve(image="ghcr.io/acme/x")
    assert build_none is None
    _, _, build, _ = _resolve(image="ghcr.io/acme/x", python="3.11", node="20")
    assert build is not None
    assert build.python_version == "3.11"
    assert build.node_version == "20"


def test_registry_config_populated():
    _, _, _, registry = _resolve(image="ghcr.io/acme/x", registry="ghcr.io/acme", distribution="save_load")
    assert registry is not None
    assert registry.registry == "ghcr.io/acme"
    assert registry.distribution == "save_load"


def test_created_image_bench_persists_deploy_fields(tmp_path):
    # The full path a created image bench takes: resolver -> BenchConfig -> TOML -> reload.
    path = tmp_path / "bench_config.toml"
    mode, deploy, build, registry = _resolve(
        image="ghcr.io/acme/mybench", registry="ghcr.io/acme", python="3.11", node="20"
    )
    bc = BenchConfig(
        name="mybench.localhost",
        developer_mode=False,
        admin_tools=False,
        environment_type=FMBenchEnvType.prod,
        root_path=path,
        deployment_mode=mode,
        deploy=deploy,
        build=build,
        registry=registry,
    )
    assert bc.export_to_toml(path) is True

    reloaded = BenchConfig.import_from_toml(path)
    assert reloaded.deployment_mode == DeploymentMode.image
    assert reloaded.deploy is not None
    assert reloaded.deploy.image == "ghcr.io/acme/mybench"
    assert reloaded.build is not None
    assert reloaded.build.python_version == "3.11"
    assert reloaded.registry is not None
    assert reloaded.registry.registry == "ghcr.io/acme"
