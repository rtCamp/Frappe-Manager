"""Contract tests for the image deploy-state round-trip on BenchConfig.

`deploy_state` mirrors `migration_state`: it is exported via `export_to_toml`
(model_dump) and re-parsed explicitly by `import_from_toml`. These tests assert
that current/previous tags and the deploy history survive the round-trip.
"""

from frappe_manager.site_manager.bench_config import (
    BenchConfig,
    BenchRuntime,
    DeployState,
    DeployStateEntry,
    FMBenchEnvType,
)


def _image_bench(path):
    return BenchConfig(
        name="x.localhost",
        developer_mode=False,
        admin_tools=False,
        environment_type=FMBenchEnvType.prod,
        root_path=path,
        runtime=BenchRuntime.image,
        image="local/x",
    )


def test_deploy_state_defaults_to_none(tmp_path):
    bc = _image_bench(tmp_path / "bench_config.toml")
    assert bc.deploy_state is None


def test_deploy_state_roundtrip(tmp_path):
    path = tmp_path / "bench_config.toml"
    bc = _image_bench(path)
    bc.deploy_state = DeployState(
        current_tag="local/x:20260721-abc",
        previous_tag="local/x:20260720-def",
        last_deploy_at="2026-07-21T10:00:00+00:00",
        history=[
            DeployStateEntry(tag="local/x:20260720-def", deployed_at="2026-07-20T09:00:00+00:00", migrate_status="migrated"),
            DeployStateEntry(tag="local/x:20260721-abc", deployed_at="2026-07-21T10:00:00+00:00", migrate_status="skipped"),
        ],
    )

    assert bc.export_to_toml(path) is True

    reloaded = BenchConfig.import_from_toml(path)
    assert reloaded.deploy_state is not None
    assert reloaded.deploy_state.current_tag == "local/x:20260721-abc"
    assert reloaded.deploy_state.previous_tag == "local/x:20260720-def"
    assert reloaded.deploy_state.last_deploy_at == "2026-07-21T10:00:00+00:00"
    assert [e.tag for e in reloaded.deploy_state.history] == [
        "local/x:20260720-def",
        "local/x:20260721-abc",
    ]
    assert reloaded.deploy_state.history[1].migrate_status == "skipped"


def test_deploy_state_absent_roundtrip(tmp_path):
    # A bench without deploy_state must round-trip with deploy_state None.
    path = tmp_path / "bench_config.toml"
    bc = _image_bench(path)
    assert bc.export_to_toml(path) is True
    reloaded = BenchConfig.import_from_toml(path)
    assert reloaded.deploy_state is None
