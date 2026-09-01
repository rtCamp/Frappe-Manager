"""Contract tests for `fm create --config`.

Precedence: explicit CLI flags > --config overlays > create defaults. It is the ORDER of the
overlay merge, not a per-field assignment, which is what stops any single field from being
forgotten. The merged result is imported into a BenchConfig; the top-level image identity lives on
`image`, the switch pipeline under `[switch]`, and image runtime records `[deploy_state].current_tag`.

Everything goes through `bench_config_from_inputs`, the one seam `fm create` uses between its
parameters and `create_bench`.
"""

import inspect
from pathlib import Path

import pytest
import typer

from frappe_manager.commands.create import (
    _FLAG_TO_CONFIG,
    _flag_overlay,
    bench_config_from_inputs,
    create,
    record_site,
)
from frappe_manager.site_manager.bench_config import (
    AppConfig,
    BenchConfig,
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


def _build(config, *, base_image=None, **flags):
    """Build through the real seam. Whatever appears in `flags` is what the user passed.

    There is no separate `explicit` argument any more, and that is the point: passing a flag and
    marking it explicit used to be two steps, so a flag could be honoured by the builder while the
    command never marked it, and it was silently dropped.
    """
    return bench_config_from_inputs(
        config=list(config),
        flag_overlay=_flag_overlay(set(flags), flags),
        benchname="x.localhost",
        root_path=_ROOT,
        base_image=base_image,
        db_name="fm_x_deadbeef",
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
    bc, _ = _build([_CFG], environment=FMBenchEnvType.dev)
    assert bc.environment_type == FMBenchEnvType.dev
    assert bc.developer_mode is True
    assert bc.admin_tools is True


def test_explicit_restart_overrides_config():
    bc, _ = _build([_CFG], restart=RestartPolicyEnum.no)
    assert bc.restart_policy == RestartPolicyEnum.no


def test_restart_default_from_env_when_unset():
    # Neither config nor flag sets restart -> BenchConfig validator derives from env.
    bc, _ = _build(['environment = "prod"\nimage = "r/x"\n'])
    assert bc.restart_policy == RestartPolicyEnum.unless_stopped


def test_explicit_apps_override_config_apps_frappe_first():
    bc, _ = _build([_CFG], apps=[AppConfig.from_string("erpnext:version-15")])
    names = [a.name for a in bc.apps_list]
    assert names[0] == "frappe"  # auto-added, first
    assert "erpnext" in names
    assert "frappe" not in names[1:], "the config's own frappe entry must be replaced, not duplicated"


def test_name_and_root_are_authoritative():
    bc, _ = _build(['name = "ignored"\nimage = "r/x"\n'])
    assert bc.name == "x.localhost"
    assert bc.root_path == _ROOT


def test_image_runtime_via_flags_resolves_tag():
    bc, _ = _build(
        ['environment = "prod"\n'],
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
    # --runtime image + --apps must raise (apps are baked into the image).
    with pytest.raises(typer.BadParameter, match="image runtime carries its own"):
        _build(
            [],
            runtime=BenchRuntime.image,
            base_image="ghcr.io/acme/app:v1",
            apps=[AppConfig.from_string("erpnext:version-15")],
        )


def test_an_explicit_seed_image_overrides_the_config():
    """`fm create --config x.toml --seed-image repo:tag` silently dropped the seed: it was only read
    on the no---config branch, so the bench cloned and installed its apps from scratch instead of
    seeding from the baked image. --config's own help promises explicit flags win."""
    bc, _ = _build([_CFG], seed_image="ghcr.io/acme/app:seed-1")

    assert bc.seed_image == "ghcr.io/acme/app:seed-1"


def test_a_seed_image_only_in_the_config_is_still_honoured():
    bc, _ = _build(['seed_image = "ghcr.io/acme/app:from-cfg"\n'])

    assert bc.seed_image == "ghcr.io/acme/app:from-cfg"


def test_an_unversioned_seed_image_is_refused():
    """A floating tag would make the seeded workspace unreproducible, so an explicit tag is
    demanded. That check ran only on the flag path before."""
    with pytest.raises(typer.BadParameter, match="explicit ':tag'"):
        _build([], seed_image="ghcr.io/acme/app")


# ------------------------------------------------------------ the wiring invariants
#
# Two halves, and a flag needs both to work: its name must match a real create parameter (or it is
# never recognised as passed) and its TOML key must be one `import_from_toml` reads (or the value is
# merged and then thrown away). `--seed-image` shipped broken because the old mechanism needed a
# hand-written line per field and one was missing; these check the replacement generically, so a
# flag added later cannot be half-wired.

# Two distinguishable values per mapped flag, plus any companion flag needed to make the difference
# observable: developer_mode is forced on in a dev environment, and a runtime change is only
# meaningful with an image to point at.
_PROBES: dict[str, tuple[object, object]] = {
    "admin_pass": ("pass-one", "pass-two"),
    # No `alias_domains` row: it left `_FLAG_TO_CONFIG` when aliases moved onto the site, because its
    # key path is `[sites."<site>"].alias_domains` and depends on the site name, which this static
    # map cannot express. `record_site` applies the flag instead; pinned by the test at the bottom.
    "apps": ([AppConfig.from_string("erpnext")], [AppConfig.from_string("hrms")]),
    "developer_mode": (False, True),
    "environment": (FMBenchEnvType.dev, FMBenchEnvType.prod),
    "github_token": ("ghp_one", "ghp_two"),
    "newrelic": (False, True),
    "newrelic_license_key": ("nr-one", "nr-two"),
    "node_version": ("18", "20"),
    "python_version": ("3.12", "3.13"),
    "restart": (RestartPolicyEnum.no, RestartPolicyEnum.always),
    "runtime": (BenchRuntime.mount, BenchRuntime.image),
    "seed_image": ("ghcr.io/acme/seed:one", "ghcr.io/acme/seed:two"),
}

_COMPANIONS: dict[str, dict[str, object]] = {
    "developer_mode": {"environment": FMBenchEnvType.prod},
    "runtime": {"base_image": "ghcr.io/acme/app:v1"},
}


def test_every_mapped_flag_is_a_real_create_parameter():
    """A mapped name that is not a parameter is never seen as passed, so the flag does nothing."""
    declared = set(inspect.signature(create).parameters)
    unknown = sorted(set(_FLAG_TO_CONFIG) - declared)
    assert unknown == [], f"_FLAG_TO_CONFIG names that are not create parameters: {unknown}"


def test_every_mapped_flag_changes_the_resulting_config():
    """A mapped TOML key that `import_from_toml` does not read is merged and then discarded."""
    assert set(_PROBES) == set(_FLAG_TO_CONFIG), (
        "every mapped flag needs a probe here (or the mapping should go); "
        f"missing {sorted(set(_FLAG_TO_CONFIG) - set(_PROBES))}, "
        f"stale {sorted(set(_PROBES) - set(_FLAG_TO_CONFIG))}"
    )

    for name, (low, high) in _PROBES.items():
        companions = _COMPANIONS.get(name, {})
        first, _ = _build([], **{**companions, name: low})
        second, _ = _build([], **{**companions, name: high})
        assert isinstance(first, BenchConfig)
        assert first != second, f"'{name}' is mapped in _FLAG_TO_CONFIG but changing it changed nothing"


def test_alias_domains_is_a_real_flag_that_deliberately_is_not_mapped():
    """`--alias-domains` looks exactly like a mapped flag -- a config value under another name --
    and must nonetheless stay OUT of `_FLAG_TO_CONFIG`. Aliases moved onto the site, so the key it
    writes is `[sites."<site>"].alias_domains`, whose path depends on the site name. A row in the
    static map could only write a top-level `alias_domains`, a field `BenchConfig` no longer has.

    So both halves of the wiring live elsewhere, and this pins them: the flag is still a real
    `create` parameter, and `record_site` is what puts its value on the addressed site -- from where
    `domains` and `get_site_mappings()` pick it up, attributed to that site and no other.
    """
    assert "alias_domains" in inspect.signature(create).parameters
    assert "alias_domains" not in _FLAG_TO_CONFIG, (
        "alias_domains is back in _FLAG_TO_CONFIG; it can only write a top-level key BenchConfig rejects"
    )

    bc, _ = _build([])
    # `fm create x.localhost --alias-domains www.example.com`: the alias belongs to x.localhost.
    bc.sites = record_site(bc.sites, "x.localhost", None, ["www.example.com"])

    assert bc.sites["x.localhost"].alias_domains == ["www.example.com"]
    assert bc.domains == ["x.localhost", "www.example.com"]
    assert bc.get_site_mappings() == {"x.localhost": "x.localhost", "www.example.com": "x.localhost"}
