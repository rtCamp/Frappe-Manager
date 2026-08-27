"""Contract tests for `fm create`'s runtime wiring (#323).

The runtime (mount vs image) is selected by `--runtime` only.

There is no `--image` on create. `--base-image` is the image the containers RUN in both
runtimes: on mount the base frappe image under the editable workspace, on image runtime
the app image itself. `--image` means the image PRODUCED, which only `fm bake` does, and
one word cannot point both ways. The runtimes persist it differently: image runtime keeps
the tag-stripped repo in top-level `image` and the ref in `[deploy_state].current_tag`,
which `fm switch` later rewrites, while mount keeps the whole ref in `base_image` and
nothing rewrites it.

Every test here goes through `bench_config_from_inputs`, which is the single seam `fm create`
itself uses between its parameters and `create_bench`. Asserting against a re-assembled chain
of the individual steps would pass while the command quietly stopped calling one of them.

A flag and the same value written into `--config` MUST reach the same answer; the pairs below
exist because they did not. `--runtime image --apps X` was refused while a `--config` declaring
`runtime = "image"` accepted `--apps X` and left it in bench_config.toml doing nothing.
"""

import pytest
import typer

from frappe_manager.commands.create import _flag_overlay, bench_config_from_inputs
from frappe_manager.site_manager.bench_config import (
    AppConfig,
    BenchConfig,
    BenchRuntime,
    DeployState,
    FMBenchEnvType,
)
from frappe_manager.utils.helpers import has_explicit_tag

_BENCH = "x.localhost"


def _build(config=None, base_image=None, **flags):
    """Build through create's real seam. `flags` are the options the user passed."""
    return bench_config_from_inputs(
        config=list(config or []),
        flag_overlay=_flag_overlay(set(flags), flags),
        benchname=_BENCH,
        root_path=f"/tmp/{_BENCH}/bench_config.toml",
        base_image=base_image,
        db_name="fm_x_localhost_dead",
    )[0]


def _resolve(runtime=None, base_image=None, apps=None, python=None, node=None):
    """The old tuple shape, so the cases below still read as one-liners.

    Returns (runtime, image, current_tag, base_image) off the built config.
    """
    flags = {}
    if runtime is not None:
        flags["runtime"] = runtime
    if apps:
        flags["apps"] = [AppConfig.from_string(a) if isinstance(a, str) else a for a in apps]
    if python is not None:
        flags["python_version"] = python
    if node is not None:
        flags["node_version"] = node
    bc = _build(base_image=base_image, **flags)
    return (
        bc.runtime,
        bc.image,
        bc.deploy_state.current_tag if bc.deploy_state else None,
        bc.base_image,
    )


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
    with pytest.raises(typer.BadParameter, match="base_image must include a tag"):
        _resolve(base_image="ghcr.io/acme/frappe-custom")


def test_image_runtime_requires_a_prebuilt_image():
    """The image runtime has nothing to run without one, and the message says what to supply."""
    with pytest.raises(typer.BadParameter, match="needs a pre-built image") as excinfo:
        _resolve(runtime=BenchRuntime.image)

    assert "base_image" in str(excinfo.value)
    assert "current_tag" in str(excinfo.value), "the --config spelling must be offered too"


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

_TAGGED_SEED = "ghcr.io/acme/erp:jun01"


def test_seed_image_rejects_image_runtime():
    with pytest.raises(typer.BadParameter, match="MOUNT"):
        _build(runtime=BenchRuntime.image, seed_image=_TAGGED_SEED)


def test_seed_image_requires_explicit_tag():
    with pytest.raises(typer.BadParameter, match="tag"):
        _build(seed_image="localhost:5000/repo")


def test_seed_image_valid_contract_passes():
    # --apps (overrides) and --python/--node (toolchain swap) are ALLOWED with --seed-image;
    # only image runtime and tagless references are rejected.
    bc = _build(seed_image=_TAGGED_SEED, apps=[AppConfig.from_string("erpnext")], python_version="3.12")
    assert bc.seed_image == _TAGGED_SEED


def test_a_seeded_workspace_keeps_its_own_frappe():
    """No frappe auto-injection: the seed carries one, and a default would clobber it."""
    seeded = _build(seed_image=_TAGGED_SEED)
    assert seeded.apps_list == [], "an empty override list must stay empty for a seeded workspace"

    unseeded = _build()
    assert [a.name for a in unseeded.apps_list] == ["frappe"], "an unseeded create still gets frappe first"


def test_seed_image_survives_a_config_overlay():
    """The flag used to be dropped whenever --config was also passed, and the bench then cloned
    and installed its apps from scratch while reporting success."""
    bc = _build(config=['upload_limit = "77M"'], seed_image=_TAGGED_SEED)
    assert bc.seed_image == _TAGGED_SEED
    assert bc.upload_limit == "77M", "the overlay must still apply"


# ------------------------------------------------------------ seed overrides merge


def test_merge_app_overrides_replaces_in_place_and_appends():
    from frappe_manager.site_manager.modules.bench_app import merge_app_overrides

    baked = [AppConfig.from_string("frappe"), AppConfig.from_string("erpnext")]
    frappe_dev = AppConfig.from_string("frappe:develop")
    added = AppConfig.from_string("myorg/frappe-hello-world:main")
    added.name = "frappe_hello_world"  # post-clone corrected module name

    merged = merge_app_overrides(baked, [frappe_dev, added])
    assert [a.name for a in merged] == ["frappe", "erpnext", "frappe_hello_world"]  # frappe first, add appended
    assert merged[0].ref == "develop"  # replaced in place with the override ref


# ------------------------------------------------------------ developer mode


@pytest.mark.parametrize(
    ("environment", "runtime", "enable", "expected"),
    [
        # dev env auto-enables on mount; prod honours the flag.
        (FMBenchEnvType.dev, BenchRuntime.mount, False, True),
        (FMBenchEnvType.prod, BenchRuntime.mount, True, True),
        (FMBenchEnvType.prod, BenchRuntime.mount, False, False),
        # image runtime NEVER auto-enables: doctype files would land in the ephemeral layer.
        (FMBenchEnvType.dev, BenchRuntime.image, False, False),
        (FMBenchEnvType.prod, BenchRuntime.image, False, False),
    ],
)
def test_developer_mode_matrix(environment, runtime, enable, expected):
    flags = {"environment": environment, "developer_mode": enable}
    base_image = "ghcr.io/acme/app:v1" if runtime == BenchRuntime.image else None
    if runtime == BenchRuntime.image:
        flags["runtime"] = runtime
    assert _build(base_image=base_image, **flags).developer_mode is expected


def test_developer_mode_enable_refused_on_image_runtime():
    with pytest.raises(typer.BadParameter, match="developer mode"):
        _build(
            runtime=BenchRuntime.image,
            base_image="ghcr.io/acme/app:v1",
            environment=FMBenchEnvType.dev,
            developer_mode=True,
        )


def test_a_dev_image_bench_is_not_refused_for_asking_nothing():
    """A --config declaring `runtime = "image"` used to be refused in a dev environment, because
    create forced developer_mode on for dev and then refused the value it had just set."""
    bc = _build(config=['runtime = "image"\n[deploy_state]\ncurrent_tag = "ghcr.io/acme/app:v1"'])
    assert bc.runtime == BenchRuntime.image
    assert bc.developer_mode is False


# ------------------------------------------------------------ flag / --config parity


@pytest.mark.parametrize(
    "mount_only", [{"apps": [AppConfig.from_string("erpnext")]}, {"python_version": "3.12"}, {"node_version": "20"}]
)
def test_mount_only_inputs_are_refused_whichever_way_the_runtime_was_spelled(mount_only):
    """The asymmetry this seam exists to remove: the flag path refused these, the --config path
    accepted them and left the values on disk with nothing reading them."""
    with pytest.raises(typer.BadParameter, match="image runtime carries its own"):
        _build(runtime=BenchRuntime.image, base_image="ghcr.io/acme/app:v1", **mount_only)

    with pytest.raises(typer.BadParameter, match="image runtime carries its own"):
        _build(config=['runtime = "image"\n[deploy_state]\ncurrent_tag = "ghcr.io/acme/app:v1"'], **mount_only)


@pytest.mark.parametrize(
    ("overlay", "expected"),
    [
        ('seed_image = "ghcr.io/acme/erp"', "seed_image requires an explicit"),
        ('base_image = "ghcr.io/acme/frappe"', "base_image must include a tag"),
        ('runtime = "image"\nimage = "ghcr.io/acme/app"', "needs a pre-built image"),
    ],
)
def test_config_declared_values_are_validated_like_flags(overlay, expected):
    """Tag and coherence checks used to run only on the flag path, so a --config could write an
    untagged reference straight to bench_config.toml."""
    with pytest.raises(typer.BadParameter, match=expected):
        _build(config=[overlay])


def test_an_explicit_flag_beats_a_config_value():
    bc = _build(config=['python_version = "3.11"'], python_version="3.13")
    assert bc.python_version == "3.13"


def test_a_config_value_survives_when_no_flag_contradicts_it():
    bc = _build(config=['python_version = "3.11"'])
    assert bc.python_version == "3.11"
