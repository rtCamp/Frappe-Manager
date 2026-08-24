"""Contract tests for `fm create`'s runtime wiring (#323).

`_resolve_deploy_options` selects the runtime (mount vs image) from --runtime
only. Each image flag has exactly one meaning: --image is the pre-built app
image an image-runtime bench runs, --base-image is the mount runtime's base
frappe image. It returns (resolved_mode, image_repo, current_tag, base_image);
image_repo is the tag-stripped app image repo (top-level BenchConfig.image) in
image mode, else None.
"""

import pytest
import typer

from frappe_manager.commands.create import (
    _resolve_deploy_options,
    _resolve_developer_mode,
    _validate_from_image,
)
from frappe_manager.site_manager.bench_config import (
    BenchConfig,
    BenchRuntime,
    DeployState,
    FMBenchEnvType,
)
from frappe_manager.utils.helpers import has_explicit_tag


def _resolve(runtime=None, image=None, base_image=None, apps=None, python=None, node=None):
    return _resolve_deploy_options(runtime, image, base_image, apps or [], python, node)


def test_default_is_mount_backward_compatible():
    # Plain `fm create` stays mount with no image/tag/override.
    mode, image_repo, current_tag, base_image = _resolve()
    assert mode == BenchRuntime.mount
    assert image_repo is None
    assert current_tag is None
    assert base_image is None


def test_base_image_flag_does_not_imply_image_runtime():
    # --base-image does not flip the runtime; it overrides the mount base image.
    mode, image_repo, current_tag, base_image = _resolve(base_image="ghcr.io/acme/frappe-custom:v15")
    assert mode == BenchRuntime.mount
    assert image_repo is None
    assert current_tag is None
    assert base_image == "ghcr.io/acme/frappe-custom:v15"


def test_mount_base_image_requires_tag():
    with pytest.raises(typer.BadParameter, match="--base-image must include a tag"):
        _resolve(base_image="ghcr.io/acme/frappe-custom")


def test_mount_runtime_rejects_image_and_points_at_base_image():
    # --image is the app image an image-runtime bench runs, never a mount base.
    with pytest.raises(typer.BadParameter, match="--base-image") as excinfo:
        _resolve(image="ghcr.io/acme/frappe-custom:v15")
    assert "--runtime image" in str(excinfo.value)


def test_image_runtime_rejects_base_image_and_points_at_image():
    with pytest.raises(typer.BadParameter, match="--base-image does not apply") as excinfo:
        _resolve(
            runtime=BenchRuntime.image,
            image="ghcr.io/acme/mybench:v1",
            base_image="ghcr.io/acme/frappe-custom:v15",
        )
    assert "--image" in str(excinfo.value)


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
    mode, image_repo, current_tag, base_image = _resolve(runtime=BenchRuntime.image, image="ghcr.io/acme/mybench:fm-1")
    assert mode == BenchRuntime.image
    assert image_repo == "ghcr.io/acme/mybench"
    assert current_tag == "ghcr.io/acme/mybench:fm-1"
    assert base_image is None


def test_has_explicit_tag_ignores_host_port():
    # A registry host:port is not a tag; a real :tag after the last '/' is.
    assert has_explicit_tag("localhost:5000/repo") is False
    assert has_explicit_tag("localhost:5000/repo:v1") is True
    assert has_explicit_tag("ghcr.io/acme/x:tag") is True
    assert has_explicit_tag("repo") is False


def test_created_image_bench_persists_deploy_fields(tmp_path):
    # The full path a created image bench takes: resolver -> BenchConfig -> TOML -> reload.
    path = tmp_path / "bench_config.toml"
    mode, image_repo, current_tag, base_image = _resolve(runtime=BenchRuntime.image, image="ghcr.io/acme/mybench:fm-1")
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
    mode, image_repo, _current_tag, base_image = _resolve(base_image="local/frappe-base:test")
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
        _validate_from_image("r:t", BenchRuntime.image)


def test_from_image_requires_explicit_tag():
    with pytest.raises(typer.BadParameter, match="tag"):
        _validate_from_image("localhost:5000/repo", BenchRuntime.mount)


def test_from_image_valid_contract_passes():
    # --apps (overrides) and --python/--node (toolchain swap) are ALLOWED with
    # --from-image; only image runtime and tagless references are rejected.
    _validate_from_image("ghcr.io/acme/erp:jun01", BenchRuntime.mount)


# ------------------------------------------------------------ seed overrides merge


def test_merge_app_overrides_replaces_in_place_and_appends():
    from frappe_manager.site_manager.bench_config import AppConfig
    from frappe_manager.site_manager.modules.bench_app import merge_app_overrides

    baked = [AppConfig.from_string("frappe"), AppConfig.from_string("erpnext")]
    frappe_dev = AppConfig.from_string("frappe:develop")
    added = AppConfig.from_string("myorg/frappe-hello-world:main")
    added.name = "frappe_hello_world"  # post-clone corrected module name

    merged = merge_app_overrides(baked, [frappe_dev, added])
    assert [a.name for a in merged] == ["frappe", "erpnext", "frappe_hello_world"]  # frappe first, add appended
    assert merged[0].ref == "develop"  # replaced in place with the override ref


# ------------------------------------------------------------ developer mode


def test_developer_mode_matrix():
    # dev env auto-enables on mount; prod honors the explicit flag.
    assert _resolve_developer_mode(FMBenchEnvType.dev, BenchRuntime.mount, explicit_enable=False) is True
    assert _resolve_developer_mode(FMBenchEnvType.prod, BenchRuntime.mount, explicit_enable=True) is True
    assert _resolve_developer_mode(FMBenchEnvType.prod, BenchRuntime.mount, explicit_enable=False) is False
    # image runtime NEVER auto-enables (doctype files -> ephemeral layer).
    assert _resolve_developer_mode(FMBenchEnvType.dev, BenchRuntime.image, explicit_enable=False) is False
    assert _resolve_developer_mode(FMBenchEnvType.prod, BenchRuntime.image, explicit_enable=False) is False


def test_developer_mode_enable_refused_on_image_runtime():
    with pytest.raises(typer.BadParameter, match="developer-mode"):
        _resolve_developer_mode(FMBenchEnvType.dev, BenchRuntime.image, explicit_enable=True)
