"""Contract tests for `fm create`'s runtime wiring (#323).

`_resolve_deploy_options` selects the runtime (mount vs image) from --runtime only.

There is no `--image` on create. `--base-image` is the image the containers RUN in both
runtimes: on mount the base frappe image under the editable workspace, on image runtime
the app image itself. `--image` means the image PRODUCED, which only `fm bake` does, and
one word cannot point both ways.

The return tuple splits the value because the runtimes persist it differently:
(resolved_mode, image_repo, current_tag, base_image). In image mode image_repo is the
tag-stripped repo for top-level BenchConfig.image and current_tag seeds
[deploy_state].current_tag, which `fm switch` later rewrites. In mount mode the whole ref
goes to base_image and nothing rewrites it.
"""

import pytest
import typer

from frappe_manager.commands.create import (
    _resolve_deploy_options,
    _resolve_developer_mode,
    _validate_seed_image,
)
from frappe_manager.site_manager.bench_config import (
    BenchConfig,
    BenchRuntime,
    DeployState,
    FMBenchEnvType,
)
from frappe_manager.utils.helpers import has_explicit_tag


def _resolve(runtime=None, base_image=None, apps=None, python=None, node=None):
    return _resolve_deploy_options(runtime, base_image, apps or [], python, node)


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


def test_image_runtime_requires_base_image():
    """The image runtime has nothing to run without one, and the message says which flag."""
    with pytest.raises(typer.BadParameter, match="--base-image") as excinfo:
        _resolve(runtime=BenchRuntime.image)

    assert "--runtime image requires" in str(excinfo.value)


def test_base_image_serves_both_runtimes_from_one_flag():
    """Same flag, same meaning (what the containers run), different persistence."""
    mount = _resolve(base_image="ghcr.io/acme/frappe:v16")
    image = _resolve(runtime=BenchRuntime.image, base_image="ghcr.io/acme/app:v42")

    assert (mount[0], mount[3]) == (BenchRuntime.mount, "ghcr.io/acme/frappe:v16")
    assert (image[0], image[1], image[2]) == (BenchRuntime.image, "ghcr.io/acme/app", "ghcr.io/acme/app:v42")
    assert image[3] is None, "image runtime routes the ref to image + deploy_state, not base_image"


def test_image_runtime_requires_tag():
    with pytest.raises(typer.BadParameter):
        _resolve(runtime=BenchRuntime.image, base_image="ghcr.io/acme/mybench")


def test_image_runtime_rejects_apps():
    with pytest.raises(typer.BadParameter):
        _resolve(runtime=BenchRuntime.image, base_image="ghcr.io/acme/mybench:v1", apps=["erpnext"])


def test_image_runtime_rejects_python():
    with pytest.raises(typer.BadParameter):
        _resolve(runtime=BenchRuntime.image, base_image="ghcr.io/acme/mybench:v1", python="3.12")


def test_image_runtime_rejects_node():
    with pytest.raises(typer.BadParameter):
        _resolve(runtime=BenchRuntime.image, base_image="ghcr.io/acme/mybench:v1", node="20")


def test_image_runtime_splits_repo_and_keeps_tag():
    mode, image_repo, current_tag, base_image = _resolve(
        runtime=BenchRuntime.image, base_image="ghcr.io/acme/mybench:fm-1"
    )
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
    mode, image_repo, current_tag, base_image = _resolve(
        runtime=BenchRuntime.image, base_image="ghcr.io/acme/mybench:fm-1"
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
    bc.export_to_toml(path)

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
    bc.export_to_toml(path)

    reloaded = BenchConfig.import_from_toml(path)
    assert reloaded.runtime == BenchRuntime.mount
    assert reloaded.image is None
    assert reloaded.base_image == "local/frappe-base:test"


# ------------------------------------------------------------ --seed-image


def test_seed_image_rejects_image_runtime():
    with pytest.raises(typer.BadParameter, match="MOUNT"):
        _validate_seed_image("r:t", BenchRuntime.image)


def test_seed_image_requires_explicit_tag():
    with pytest.raises(typer.BadParameter, match="tag"):
        _validate_seed_image("localhost:5000/repo", BenchRuntime.mount)


def test_seed_image_valid_contract_passes():
    # --apps (overrides) and --python/--node (toolchain swap) are ALLOWED with
    # --seed-image; only image runtime and tagless references are rejected.
    _validate_seed_image("ghcr.io/acme/erp:jun01", BenchRuntime.mount)


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
