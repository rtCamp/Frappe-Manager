"""Contract tests for `fm create --config` (precedence B).

Precedence B: explicit CLI flags > --config overlays > create defaults. The
overlay is imported into a BenchConfig; the top-level image identity lives on
`image`, the switch pipeline under `[switch]`, and image-runtime tag resolution
records `[deploy_state].current_tag`.
"""

import ast
import inspect
from pathlib import Path

import pytest
import typer

from frappe_manager.commands.create import _build_overlay_bench_config
from frappe_manager.site_manager.bench_config import (
    AppConfig,
    BenchRuntime,
    FMBenchEnvType,
    RestartPolicyEnum,
)

_ROOT = Path("build") / "x" / "bench_config.toml"

_CFG = """
environment = "prod"
restart_policy = "always"
image = "ghcr.io/acme/app"
[[apps]]
name = "frappe"
repo = "frappe/frappe"
ref = "version-15"
[switch]
backup_db = true
"""


def _build(
    config,
    *,
    explicit=(),
    apps=None,
    environment=FMBenchEnvType.dev,
    developer_mode_status=False,
    alias_domains=None,
    restart=None,
    runtime=None,
    base_image=None,
    seed_image=None,
):
    return _build_overlay_bench_config(
        config=config,
        benchname="x.localhost",
        root_path=_ROOT,
        apps=apps or [],
        environment=environment,
        developer_mode_status=developer_mode_status,
        admin_pass="admin",
        alias_domains=alias_domains,
        github_token=None,
        python_version=None,
        node_version=None,
        restart=restart,
        newrelic=False,
        newrelic_license_key=None,
        runtime=runtime,
        base_image=base_image,
        seed_image=seed_image,
        db_name="fm_x_deadbeef",
        explicit=set(explicit),
    )


def test_config_supplies_fields_when_no_flags():
    bc, apps_from_user = _build([_CFG])
    assert bc.environment_type == FMBenchEnvType.prod
    assert bc.restart_policy == RestartPolicyEnum.always
    assert bc.image == "ghcr.io/acme/app"
    assert bc.switch.backup_db is True
    assert apps_from_user is True
    assert [a.name for a in bc.apps_list] == ["frappe"]


def test_explicit_flag_overrides_config():
    # --environment dev beats config's prod, and dev forces developer/admin tools.
    bc, _ = _build([_CFG], explicit={"environment"}, environment=FMBenchEnvType.dev)
    assert bc.environment_type == FMBenchEnvType.dev
    assert bc.developer_mode is True
    assert bc.admin_tools is True


def test_explicit_restart_overrides_config():
    bc, _ = _build([_CFG], explicit={"restart"}, restart=RestartPolicyEnum.no)
    assert bc.restart_policy == RestartPolicyEnum.no


def test_restart_default_from_env_when_unset():
    # Neither config nor flag sets restart -> BenchConfig validator derives from env.
    bc, _ = _build(['environment = "prod"\nimage = "r/x"\n'])
    assert bc.restart_policy == RestartPolicyEnum.unless_stopped


def test_explicit_apps_override_config_apps_frappe_first():
    bc, _ = _build([_CFG], explicit={"apps"}, apps=[AppConfig.from_string("erpnext:version-15")])
    names = [a.name for a in bc.apps_list]
    assert names[0] == "frappe"  # auto-added, first
    assert "erpnext" in names


def test_name_and_root_are_authoritative():
    bc, _ = _build(['name = "ignored"\nimage = "r/x"\n'])
    assert bc.name == "x.localhost"
    assert bc.root_path == _ROOT


def test_image_runtime_via_flags_resolves_tag():
    bc, _ = _build(
        ['environment = "prod"\n'],
        explicit={"runtime", "base_image"},
        runtime=BenchRuntime.image,
        base_image="ghcr.io/acme/app:fm-1",
    )
    assert bc.runtime == BenchRuntime.image
    assert bc.image == "ghcr.io/acme/app"  # tag stripped for top-level image
    assert bc.deploy_state.current_tag == "ghcr.io/acme/app:fm-1"
    assert bc.base_image is None  # the ref went to image + deploy_state, not base_image


def test_image_runtime_purely_from_config():
    cfg = """
runtime = "image"
image = "ghcr.io/acme/app"
[deploy_state]
current_tag = "ghcr.io/acme/app:fm-9"
"""
    bc, _ = _build([cfg])
    assert bc.runtime == BenchRuntime.image
    assert bc.deploy_state.current_tag == "ghcr.io/acme/app:fm-9"


def test_explicit_apps_rejected_in_image_runtime():
    # --runtime image + explicit --apps must raise (apps are baked into the image).
    with pytest.raises(typer.BadParameter):
        _build(
            [],
            explicit={"runtime", "apps"},
            runtime=BenchRuntime.image,
            apps=[AppConfig.from_string("erpnext:version-15")],
        )


def test_an_explicit_seed_image_overrides_the_config():
    """`fm create --config x.toml --seed-image repo:tag` silently dropped the seed: it was only read
    on the no---config branch, so the bench cloned and installed its apps from scratch instead of
    seeding from the baked image. --config's own help promises explicit flags win."""
    bc, _ = _build([_CFG], explicit={"seed_image"}, seed_image="ghcr.io/acme/app:seed-1")

    assert bc.seed_image == "ghcr.io/acme/app:seed-1"


def test_a_seed_image_only_in_the_config_is_still_honoured():
    bc, _ = _build(['seed_image = "ghcr.io/acme/app:from-cfg"\n'])

    assert bc.seed_image == "ghcr.io/acme/app:from-cfg"


def test_an_unversioned_seed_image_is_refused():
    """A floating tag would make the seeded workspace unreproducible, so `_validate_seed_image`
    demands an explicit tag. That check ran only on the flag path before."""
    with pytest.raises(typer.BadParameter):
        _build([], explicit={"seed_image"}, seed_image="ghcr.io/acme/app")


def test_every_flag_the_overlay_honours_is_marked_explicit_by_the_command():
    """The wiring invariant, and the one that catches this bug class.

    `_build_overlay_bench_config` only applies a flag when its name is in `explicit`, and the
    command builds that set from a hand-written tuple of names. A flag the helper checks but the
    tuple omits is accepted on the command line and silently dropped: that is exactly what happened
    to `--seed-image`, which the helper had no branch for and the tuple did not list, so
    `fm create --config x.toml --seed-image repo:tag` cloned and installed from scratch.

    Asserting through the helper alone cannot catch it, because a test passes `explicit` in
    directly. This reads both halves out of the source instead.
    """
    source = Path(inspect.getsourcefile(_build_overlay_bench_config)).read_text()
    tree = ast.parse(source)

    checked = {
        node.left.value
        for node in ast.walk(tree)
        if isinstance(node, ast.Compare)
        and isinstance(node.left, ast.Constant)
        and isinstance(node.left.value, str)
        and any(isinstance(op, ast.In) for op in node.ops)
        and any(isinstance(c, ast.Name) and c.id == "explicit" for c in node.comparators)
    }

    declared = {
        elt.value
        for node in ast.walk(tree)
        if isinstance(node, (ast.Set, ast.Tuple, ast.List))
        for elt in node.elts
        if isinstance(elt, ast.Constant) and isinstance(elt.value, str) and elt.value in checked
    }

    assert checked, 'found no `"x" in explicit` checks; this test has lost its anchor'
    missing = sorted(checked - declared)
    assert missing == [], f"the overlay honours these but the command never marks them explicit: {missing}"
