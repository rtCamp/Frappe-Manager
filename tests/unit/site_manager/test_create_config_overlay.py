"""Contract tests for `fm create --config` (precedence B).

`_build_overlay_bench_config` layers inputs: create defaults < `--config` overlay
< explicit CLI flags. `explicit` is the set of parameter names the user actually
passed. These defend the precedence and the runtime resolution.
"""

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
environment_type = "prod"
restart_policy = "always"
[[apps_list]]
name = "frappe"
repo = "frappe/frappe"
ref = "version-15"
[deploy]
image = "ghcr.io/acme/app"
backups = true
[registry]
registry = "ghcr.io/acme"
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
    image=None,
):
    return _build_overlay_bench_config(
        config=config,
        benchname="x.localhost",
        root_path=_ROOT,
        apps=apps or [],
        environment=environment,
        developer_mode_status=developer_mode_status,
        admin_pass="admin",  # noqa: S106
        alias_domains=alias_domains,
        github_token=None,
        python_version=None,
        node_version=None,
        restart=restart,
        newrelic=False,
        newrelic_license_key=None,
        runtime=runtime,
        image=image,
        db_name="fm_x_deadbeef",
        explicit=set(explicit),
    )


def test_config_supplies_fields_when_no_flags():
    bc, apps_from_user = _build([_CFG])
    assert bc.environment_type == FMBenchEnvType.prod
    assert bc.restart_policy == RestartPolicyEnum.always
    assert bc.deploy.image == "ghcr.io/acme/app"
    assert bc.deploy.backups is True
    assert bc.registry.registry == "ghcr.io/acme"
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
    bc, _ = _build(['environment_type = "prod"\n[deploy]\nimage = "r/x"\n'])
    assert bc.restart_policy == RestartPolicyEnum.unless_stopped


def test_explicit_apps_override_config_apps_frappe_first():
    bc, _ = _build([_CFG], explicit={"apps"}, apps=[AppConfig.from_string("erpnext:version-15")])
    names = [a.name for a in bc.apps_list]
    assert names[0] == "frappe"  # auto-added, first
    assert "erpnext" in names


def test_name_and_root_are_authoritative():
    bc, _ = _build(['name = "ignored"\n[deploy]\nimage = "r/x"\n'])
    assert bc.name == "x.localhost"
    assert bc.root_path == _ROOT


def test_image_runtime_via_flags_resolves_tag():
    bc, _ = _build(
        ['environment_type = "prod"\n'],
        explicit={"runtime", "image"},
        runtime=BenchRuntime.image,
        image="ghcr.io/acme/app:fm-1",
    )
    assert bc.runtime == BenchRuntime.image
    assert bc.deploy.image == "ghcr.io/acme/app"  # tag stripped for [deploy].image
    assert bc.deploy_state.current_tag == "ghcr.io/acme/app:fm-1"


def test_image_runtime_purely_from_config():
    cfg = """
runtime = "image"
[deploy]
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
