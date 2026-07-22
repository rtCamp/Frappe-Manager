"""Contract tests for `fm create`'s deploy-mode wiring (#323).

Covers `_resolve_deploy_options` — the pure resolver that turns the create CLI
flags (--deployment-mode/--image) into the deployment_mode + [deploy] config +
current tag + mount base-image override. Mode is selected only by
--deployment-mode (default mount); --image is mode-scoped. Also a full
BenchConfig export/import round-trip proving persisted deploy fields survive.
"""

import pytest
import typer

from frappe_manager.commands.create import _has_explicit_tag, _resolve_deploy_options
from frappe_manager.site_manager.bench_config import (
    BenchConfig,
    DeploymentMode,
    DeployState,
    FMBenchEnvType,
)


def _resolve(deployment_mode=None, image=None, apps=None, python=None, node=None):
    return _resolve_deploy_options(deployment_mode, image, apps or [], python, node)


def test_default_is_mount_backward_compatible():
    # Plain `fm create` stays mount with no deploy/tag/override.
    mode, deploy, current_tag, base_image = _resolve()
    assert mode == DeploymentMode.mount
    assert deploy is None
    assert current_tag is None
    assert base_image is None


def test_image_flag_does_not_imply_image_mode():
    # --image alone no longer flips the mode; it's a mount base-image override.
    mode, deploy, current_tag, base_image = _resolve(image="ghcr.io/acme/frappe-custom:v15")
    assert mode == DeploymentMode.mount
    assert deploy is None
    assert current_tag is None
    assert base_image == "ghcr.io/acme/frappe-custom:v15"


def test_mount_override_requires_tag():
    with pytest.raises(typer.BadParameter):
        _resolve(image="ghcr.io/acme/frappe-custom")


def test_explicit_image_mode_requires_image():
    with pytest.raises(typer.BadParameter):
        _resolve(deployment_mode=DeploymentMode.image)


def test_image_mode_requires_tag():
    with pytest.raises(typer.BadParameter):
        _resolve(deployment_mode=DeploymentMode.image, image="ghcr.io/acme/mybench")


def test_image_mode_rejects_apps():
    with pytest.raises(typer.BadParameter):
        _resolve(deployment_mode=DeploymentMode.image, image="ghcr.io/acme/mybench:v1", apps=["erpnext"])


def test_image_mode_rejects_python():
    with pytest.raises(typer.BadParameter):
        _resolve(deployment_mode=DeploymentMode.image, image="ghcr.io/acme/mybench:v1", python="3.12")


def test_image_mode_rejects_node():
    with pytest.raises(typer.BadParameter):
        _resolve(deployment_mode=DeploymentMode.image, image="ghcr.io/acme/mybench:v1", node="20")


def test_image_mode_splits_repo_and_keeps_tag():
    mode, deploy, current_tag, base_image = _resolve(
        deployment_mode=DeploymentMode.image, image="ghcr.io/acme/mybench:fm-1"
    )
    assert mode == DeploymentMode.image
    assert deploy is not None
    assert deploy.image == "ghcr.io/acme/mybench"
    assert current_tag == "ghcr.io/acme/mybench:fm-1"
    assert base_image is None


def test_has_explicit_tag_ignores_host_port():
    # A registry host:port is not a tag; a real :tag after the last '/' is.
    assert _has_explicit_tag("localhost:5000/repo") is False
    assert _has_explicit_tag("localhost:5000/repo:v1") is True
    assert _has_explicit_tag("ghcr.io/acme/x:tag") is True
    assert _has_explicit_tag("repo") is False


def test_created_image_bench_persists_deploy_fields(tmp_path):
    # The full path a created image bench takes: resolver -> BenchConfig -> TOML -> reload.
    path = tmp_path / "bench_config.toml"
    mode, deploy, current_tag, base_image = _resolve(
        deployment_mode=DeploymentMode.image, image="ghcr.io/acme/mybench:fm-1"
    )
    bc = BenchConfig(
        name="mybench.localhost",
        developer_mode=False,
        admin_tools=False,
        environment_type=FMBenchEnvType.prod,
        root_path=path,
        deployment_mode=mode,
        deploy=deploy,
        base_image=base_image,
        deploy_state=DeployState(current_tag=current_tag),
    )
    assert bc.export_to_toml(path) is True

    reloaded = BenchConfig.import_from_toml(path)
    assert reloaded.deployment_mode == DeploymentMode.image
    assert reloaded.deploy is not None
    assert reloaded.deploy.image == "ghcr.io/acme/mybench"
    assert reloaded.deploy_state is not None
    assert reloaded.deploy_state.current_tag == "ghcr.io/acme/mybench:fm-1"
    assert reloaded.base_image is None


def test_created_mount_bench_persists_base_image(tmp_path):
    path = tmp_path / "bench_config.toml"
    mode, deploy, _current_tag, base_image = _resolve(image="local/frappe-base:test")
    bc = BenchConfig(
        name="ovr.localhost",
        developer_mode=True,
        admin_tools=True,
        environment_type=FMBenchEnvType.dev,
        root_path=path,
        deployment_mode=mode,
        deploy=deploy,
        base_image=base_image,
    )
    assert bc.export_to_toml(path) is True

    reloaded = BenchConfig.import_from_toml(path)
    assert reloaded.deployment_mode == DeploymentMode.mount
    assert reloaded.deploy is None
    assert reloaded.base_image == "local/frappe-base:test"
