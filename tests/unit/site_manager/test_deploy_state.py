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


def test_deploy_state_backup_roundtrip(tmp_path):
    # The pre-migrate dump path recorded during deploy must survive the round-trip
    # (it is what `fm rollback --restore-db` consumes).
    path = tmp_path / "bench_config.toml"
    bc = _image_bench(path)
    bc.deploy_state = DeployState(
        current_tag="local/x:t2",
        previous_tag="local/x:t1",
        history=[
            DeployStateEntry(tag="local/x:t1", deployed_at="2026-07-20T09:00:00+00:00", migrate_status="skipped"),
            DeployStateEntry(
                tag="local/x:t2",
                deployed_at="2026-07-21T10:00:00+00:00",
                migrate_status="migrated",
                backup="/benches/x/backups/deploy-20260721/db-fm_x.sql",
            ),
        ],
    )
    assert bc.export_to_toml(path) is True
    reloaded = BenchConfig.import_from_toml(path)
    assert reloaded.deploy_state.history[0].backup is None  # old entries tolerate absence
    assert reloaded.deploy_state.history[1].backup == "/benches/x/backups/deploy-20260721/db-fm_x.sql"


class TestSwitchResolvers:
    """`fm switch` target + dump resolution (pure helpers in commands/deploy.py)."""

    def _state(self):
        return DeployState(
            current_tag="local/x:t3",
            previous_tag="local/x:t2",
            history=[
                DeployStateEntry(tag="local/x:t2", deployed_at="d2", migrate_status="skipped"),
                DeployStateEntry(tag="local/x:t3", deployed_at="d3", migrate_status="migrated", backup="/b/db.sql"),
            ],
        )

    def test_explicit_tag_wins(self):
        from frappe_manager.commands.deploy import _resolve_switch_tag

        assert _resolve_switch_tag(self._state(), "local/x:t9", False) == ("local/x:t9", None)

    def test_previous_resolves_recorded_tag(self):
        from frappe_manager.commands.deploy import _resolve_switch_tag

        assert _resolve_switch_tag(self._state(), None, True) == ("local/x:t2", None)

    def test_tag_and_previous_conflict(self):
        from frappe_manager.commands.deploy import _resolve_switch_tag

        target, error = _resolve_switch_tag(self._state(), "local/x:t9", True)
        assert target is None
        assert "not both" in error

    def test_previous_without_history_errors(self):
        from frappe_manager.commands.deploy import _resolve_switch_tag

        target, error = _resolve_switch_tag(None, None, True)
        assert target is None
        assert "No previous image tag recorded" in error

    def test_neither_tag_nor_previous_errors(self):
        from frappe_manager.commands.deploy import _resolve_switch_tag

        target, error = _resolve_switch_tag(self._state(), None, False)
        assert target is None
        assert "Missing target" in error

    def test_backup_found_for_current_deploy(self):
        from frappe_manager.commands.deploy import _find_current_deploy_backup

        dump, error = _find_current_deploy_backup(self._state())
        assert (dump, error) == ("/b/db.sql", None)

    def test_backup_missing_for_current_deploy_errors(self):
        from frappe_manager.commands.deploy import _find_current_deploy_backup

        state = self._state()
        state.history[1].backup = None
        dump, error = _find_current_deploy_backup(state)
        assert dump is None
        assert "No DB backup recorded for the current deploy (local/x:t3)" in error

    def test_no_current_deploy_errors(self):
        from frappe_manager.commands.deploy import _find_current_deploy_backup

        dump, error = _find_current_deploy_backup(None)
        assert dump is None
        assert "No current deploy recorded" in error
