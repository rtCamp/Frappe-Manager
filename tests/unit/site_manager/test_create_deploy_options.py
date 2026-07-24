"""Contract tests for `fm create`'s runtime wiring (#323).

`_resolve_deploy_options` selects the runtime (mount vs image) from --runtime
only; --image is mode-scoped (mount: base-image override; image: the pre-built
app image). It returns (resolved_mode, image_repo, current_tag, base_image);
image_repo is the tag-stripped app image repo (top-level BenchConfig.image) in
image mode, else None.
"""

import pytest
import typer

from frappe_manager.commands.create import _has_explicit_tag, _resolve_deploy_options, _validate_from_image
from frappe_manager.site_manager.bench_config import (
    BenchConfig,
    BenchRuntime,
    DeployState,
    FMBenchEnvType,
)


def _resolve(runtime=None, image=None, apps=None, python=None, node=None):
    return _resolve_deploy_options(runtime, image, apps or [], python, node)


def test_default_is_mount_backward_compatible():
    # Plain `fm create` stays mount with no image/tag/override.
    mode, image_repo, current_tag, base_image = _resolve()
    assert mode == BenchRuntime.mount
    assert image_repo is None
    assert current_tag is None
    assert base_image is None


def test_image_flag_does_not_imply_image_runtime():
    # --image alone no longer flips the runtime; it's a mount base-image override.
    mode, image_repo, current_tag, base_image = _resolve(image="ghcr.io/acme/frappe-custom:v15")
    assert mode == BenchRuntime.mount
    assert image_repo is None
    assert current_tag is None
    assert base_image == "ghcr.io/acme/frappe-custom:v15"


def test_mount_override_requires_tag():
    with pytest.raises(typer.BadParameter):
        _resolve(image="ghcr.io/acme/frappe-custom")


def test_explicit_image_runtime_requires_image():
    with pytest.raises(typer.BadParameter):
        _resolve(runtime=BenchRuntime.image)


def test_image_runtime_requires_tag():
    with pytest.raises(typer.BadParameter):
        _resolve(runtime=BenchRuntime.image, image="ghcr.io/acme/mybench")


def test_image_runtime_rejects_apps():
    with pytest.raises(typer.BadParameter):
        _resolve(runtime=BenchRuntime.image, image="ghcr.io/acme/mybench:v1", apps=["erpnext"])


def test_image_runtime_rejects_python():
    with pytest.raises(typer.BadParameter):
        _resolve(runtime=BenchRuntime.image, image="ghcr.io/acme/mybench:v1", python="3.12")


def test_image_runtime_rejects_node():
    with pytest.raises(typer.BadParameter):
        _resolve(runtime=BenchRuntime.image, image="ghcr.io/acme/mybench:v1", node="20")


def test_image_runtime_splits_repo_and_keeps_tag():
    mode, image_repo, current_tag, base_image = _resolve(
        runtime=BenchRuntime.image, image="ghcr.io/acme/mybench:fm-1"
    )
    assert mode == BenchRuntime.image
    assert image_repo == "ghcr.io/acme/mybench"
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
    mode, image_repo, current_tag, base_image = _resolve(
        runtime=BenchRuntime.image, image="ghcr.io/acme/mybench:fm-1"
    )
    bc = BenchConfig(
        name="mybench.localhost",
        developer_mode=False,
        admin_tools=False,
        environment_type=FMBenchEnvType.prod,
        root_path=path,
        runtime=mode,
        image=image_repo,
        base_image=base_image,
        deploy_state=DeployState(current_tag=current_tag),
    )
    assert bc.export_to_toml(path) is True

    reloaded = BenchConfig.import_from_toml(path)
    assert reloaded.runtime == BenchRuntime.image
    assert reloaded.image == "ghcr.io/acme/mybench"
    assert reloaded.deploy_state is not None
    assert reloaded.deploy_state.current_tag == "ghcr.io/acme/mybench:fm-1"
    assert reloaded.base_image is None


def test_created_mount_bench_persists_base_image(tmp_path):
    path = tmp_path / "bench_config.toml"
    mode, image_repo, _current_tag, base_image = _resolve(image="local/frappe-base:test")
    bc = BenchConfig(
        name="ovr.localhost",
        developer_mode=True,
        admin_tools=True,
        environment_type=FMBenchEnvType.dev,
        root_path=path,
        runtime=mode,
        image=image_repo,
        base_image=base_image,
    )
    assert bc.export_to_toml(path) is True

    reloaded = BenchConfig.import_from_toml(path)
    assert reloaded.runtime == BenchRuntime.mount
    assert reloaded.image is None
    assert reloaded.base_image == "local/frappe-base:test"


# ------------------------------------------------------------ --from-image


def test_from_image_rejects_image_runtime():
    with pytest.raises(typer.BadParameter, match="MOUNT"):
        _validate_from_image("r:t", BenchRuntime.image, [], None, None)


def test_from_image_rejects_provisioning_flags():
    # apps / python / node come from the image.
    with pytest.raises(typer.BadParameter, match="--apps"):
        _validate_from_image("r:t", BenchRuntime.mount, ["erpnext"], None, None)
    with pytest.raises(typer.BadParameter):
        _validate_from_image("r:t", BenchRuntime.mount, [], "3.12", None)
    with pytest.raises(typer.BadParameter):
        _validate_from_image("r:t", BenchRuntime.mount, [], None, "20")


def test_from_image_requires_explicit_tag():
    with pytest.raises(typer.BadParameter, match="tag"):
        _validate_from_image("localhost:5000/repo", BenchRuntime.mount, [], None, None)


def test_from_image_valid_contract_passes():
    _validate_from_image("ghcr.io/acme/erp:jun01", BenchRuntime.mount, [], None, None)
