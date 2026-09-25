"""Contract tests for the image deploy-state round-trip on BenchConfig.

`deployments` mirrors `schema`: it is exported via `export_to_toml`
(model_dump) and re-parsed explicitly by `import_from_toml`. These tests assert
that current/previous records and the deploy history survive the round-trip.
"""

from pathlib import Path

import pytest
from pydantic import ValidationError

from frappe_manager.site_manager.bench_config import (
    BenchConfig,
    BenchRuntime,
    Deployment,
    Deployments,
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


def test_deployments_defaults_to_none(tmp_path):
    bc = _image_bench(tmp_path / "bench_config.toml")
    assert bc.deployments is None


def test_deployments_roundtrip(tmp_path):
    path = tmp_path / "bench_config.toml"
    bc = _image_bench(path)
    bc.deployments = Deployments(
        current=Deployment(
            app_image="local/x:20260721-abc", deployed_at="2026-07-21T10:00:00+00:00", migrate_status="skipped"
        ),
        previous=Deployment(
            app_image="local/x:20260720-def", deployed_at="2026-07-20T09:00:00+00:00", migrate_status="migrated"
        ),
        last_at="2026-07-21T10:00:00+00:00",
        history=[
            Deployment(
                app_image="local/x:20260720-def", deployed_at="2026-07-20T09:00:00+00:00", migrate_status="migrated"
            ),
            Deployment(
                app_image="local/x:20260721-abc", deployed_at="2026-07-21T10:00:00+00:00", migrate_status="skipped"
            ),
        ],
    )

    bc.export_to_toml(path)

    reloaded = BenchConfig.import_from_toml(path)
    assert reloaded.deployments is not None
    assert reloaded.deployments.current.app_image == "local/x:20260721-abc"
    assert reloaded.deployments.previous.app_image == "local/x:20260720-def"
    assert reloaded.deployments.last_at == "2026-07-21T10:00:00+00:00"
    assert [e.app_image for e in reloaded.deployments.history] == [
        "local/x:20260720-def",
        "local/x:20260721-abc",
    ]
    assert reloaded.deployments.history[1].migrate_status == "skipped"


def test_deployments_absent_roundtrip(tmp_path):
    # A bench without deployments must round-trip with deployments None.
    path = tmp_path / "bench_config.toml"
    bc = _image_bench(path)
    bc.export_to_toml(path)
    reloaded = BenchConfig.import_from_toml(path)
    assert reloaded.deployments is None


def test_deployments_backups_roundtrip(tmp_path):
    # The pre-migrate dump paths recorded during deploy must survive the round-trip
    # (they are what `fm switch --restore-db` consumes). One entry per SITE: a bench
    # serving several sites dumps every schema, and a rollback restores all of them.
    path = tmp_path / "bench_config.toml"
    bc = _image_bench(path)
    bc.deployments = Deployments(
        current=Deployment(
            app_image="local/x:t2",
            deployed_at="2026-07-21T10:00:00+00:00",
            migrate_status="migrated",
            backups={
                "x.localhost": "/benches/x/backups/deploy-20260721/db-fm_x.sql",
                "shop.x.localhost": "/benches/x/backups/deploy-20260721/db-fm_shop_x.sql",
            },
        ),
        previous=Deployment(app_image="local/x:t1", deployed_at="2026-07-20T09:00:00+00:00", migrate_status="skipped"),
        history=[
            Deployment(app_image="local/x:t1", deployed_at="2026-07-20T09:00:00+00:00", migrate_status="skipped"),
            Deployment(
                app_image="local/x:t2",
                deployed_at="2026-07-21T10:00:00+00:00",
                migrate_status="migrated",
                backups={
                    "x.localhost": "/benches/x/backups/deploy-20260721/db-fm_x.sql",
                    "shop.x.localhost": "/benches/x/backups/deploy-20260721/db-fm_shop_x.sql",
                },
            ),
        ],
    )
    bc.export_to_toml(path)
    reloaded = BenchConfig.import_from_toml(path)
    assert reloaded.deployments.history[0].backups == {}  # old entries tolerate absence
    assert reloaded.deployments.history[1].backups == {
        "x.localhost": "/benches/x/backups/deploy-20260721/db-fm_x.sql",
        "shop.x.localhost": "/benches/x/backups/deploy-20260721/db-fm_shop_x.sql",
    }


def test_deployment_backups_rejects_non_string_dump_paths():
    # `backups` is declared dict[str, str] on purpose: the values are host dump paths that
    # get written straight into bench_config.toml, and tomlkit cannot serialise a PosixPath.
    # `_record` does str(path) for exactly this reason; the model REJECTS (never coerces) a
    # Path, so a future caller that forgets the str() fails loudly at record time instead of
    # writing a bench config that no later `fm` run can read back.
    with pytest.raises(ValidationError) as excinfo:
        Deployment(
            app_image="local/x:t1",
            deployed_at="2026-07-21T10:00:00+00:00",
            migrate_status="migrated",
            backups={"x.localhost": Path("/benches/x/backups/deploy-20260721/db-fm_x.sql")},
        )
    assert excinfo.value.errors()[0]["type"] == "string_type"

    # Same for any other non-string dump value.
    with pytest.raises(ValidationError):
        Deployment(
            app_image="local/x:t1",
            deployed_at="2026-07-21T10:00:00+00:00",
            migrate_status="migrated",
            backups={"x.localhost": 5},
        )


def test_deployment_backups_rejects_non_string_site_keys():
    # The keys are SITE names. A non-string key would become a TOML table name that no
    # site lookup in `fm switch --restore-db` could ever match.
    with pytest.raises(ValidationError) as excinfo:
        Deployment(
            app_image="local/x:t1",
            deployed_at="2026-07-21T10:00:00+00:00",
            migrate_status="migrated",
            backups={1: "/benches/x/backups/deploy-20260721/db-fm_x.sql"},
        )
    assert excinfo.value.errors()[0]["type"] == "string_type"


def test_deployment_backups_rejects_non_mapping():
    # backups is a per-site mapping, never a bare path or a sequence of pairs.
    with pytest.raises(ValidationError) as excinfo:
        Deployment(
            app_image="local/x:t1",
            deployed_at="2026-07-21T10:00:00+00:00",
            migrate_status="migrated",
            backups="/benches/x/backups/deploy-20260721/db-fm_x.sql",
        )
    assert excinfo.value.errors()[0]["type"] == "dict_type"


class TestSwitchResolvers:
    """`fm switch` target + dump resolution (pure helpers in commands/deploy.py)."""

    def _state(self):
        return Deployments(
            current=Deployment(
                app_image="local/x:t3",
                deployed_at="d3",
                migrate_status="migrated",
                backups={"x.localhost": "/b/db.sql", "shop.x.localhost": "/b/db-shop.sql"},
            ),
            previous=Deployment(app_image="local/x:t2", deployed_at="d2", migrate_status="skipped"),
            history=[
                Deployment(app_image="local/x:t2", deployed_at="d2", migrate_status="skipped"),
                Deployment(
                    app_image="local/x:t3",
                    deployed_at="d3",
                    migrate_status="migrated",
                    backups={"x.localhost": "/b/db.sql", "shop.x.localhost": "/b/db-shop.sql"},
                ),
            ],
        )

    def test_explicit_image_wins(self):
        from frappe_manager.commands.deploy import _resolve_switch_image

        assert _resolve_switch_image(self._state(), "local/x:t9", False) == ("local/x:t9", None)

    def test_previous_resolves_recorded_image(self):
        from frappe_manager.commands.deploy import _resolve_switch_image

        assert _resolve_switch_image(self._state(), None, True) == ("local/x:t2", None)

    def test_image_and_previous_conflict(self):
        from frappe_manager.commands.deploy import _resolve_switch_image

        target, error = _resolve_switch_image(self._state(), "local/x:t9", True)
        assert target is None
        assert "not both" in error

    def test_previous_without_history_errors(self):
        from frappe_manager.commands.deploy import _resolve_switch_image

        target, error = _resolve_switch_image(None, None, True)
        assert target is None
        assert "No previous image recorded" in error

    def test_neither_image_nor_previous_errors(self):
        from frappe_manager.commands.deploy import _resolve_switch_image

        target, error = _resolve_switch_image(self._state(), None, False)
        assert target is None
        assert "Missing target" in error

    def test_a_target_with_no_version_is_refused_before_any_docker_call(self):
        """``fm switch mybench v15`` used to sail through with no validation and die on a
        generic ``docker pull`` error naming the bare tag as if it were a repository. It is
        refused up front, in docker's own words: what was passed is a REPOSITORY, and an image
        reference is a repository plus a version."""
        from frappe_manager.commands.deploy import _resolve_switch_image

        target, error = _resolve_switch_image(self._state(), "v15", False)
        assert target is None
        assert "not an image reference" in error
        assert "names no version" in error

    def test_the_refusal_does_not_guess_which_half_is_missing(self):
        """A single bare word is as likely a tag passed alone as a Docker Hub repository
        (``nginx`` is a real one), so the message states what was given and covers both,
        rather than sending the reader after the wrong half of their command."""
        from frappe_manager.commands.deploy import _resolve_switch_image

        _, error = _resolve_switch_image(self._state(), "v15", False)
        assert "If you passed a tag on its own" in error

    def test_a_registry_host_port_without_a_tag_is_still_refused(self):
        """A registry host:port (``localhost:5000/repo``) is not a tag: the colon has to come
        after the last ``/``. Confirms the resolver reuses ``has_explicit_tag`` rather than a
        naive ``':' in value`` check."""
        from frappe_manager.commands.deploy import _resolve_switch_image

        target, error = _resolve_switch_image(self._state(), "localhost:5000/repo", False)
        assert target is None
        assert "not an image reference" in error

    def test_a_digest_reference_is_a_valid_switch_target(self):
        """A digest was refused while the companion image was DERIVED from the app image's name,
        because no second image's digest is derivable from another's. The companion is read from
        the recorded pair now, so the reason is spent -- and a digest is the reference production
        wants, since a tag can be moved under a running bench and a digest cannot."""
        from frappe_manager.commands.deploy import _resolve_switch_image

        digest_ref = "ghcr.io/acme/mybench@sha256:" + "a" * 64
        target, error = _resolve_switch_image(self._state(), digest_ref, False)
        assert error is None
        assert target == digest_ref

    def test_a_reference_carrying_both_a_tag_and_a_digest_is_accepted(self):
        """Docker allows `repo:tag@sha256:...`, and the digest wins; fm must not refuse a shape
        docker itself resolves."""
        from frappe_manager.commands.deploy import _resolve_switch_image

        both = "ghcr.io/acme/mybench:v1@sha256:" + "a" * 64
        target, error = _resolve_switch_image(self._state(), both, False)
        assert error is None
        assert target == both

    def test_backups_found_for_current_deploy(self):
        # Every site's dump, not just the primary's: a rollback that restored one schema
        # would leave the rest migrated against the code being rolled back under them.
        from frappe_manager.commands.deploy import _find_current_deploy_backups

        dumps, error = _find_current_deploy_backups(self._state())
        assert error is None
        assert dumps == {"x.localhost": "/b/db.sql", "shop.x.localhost": "/b/db-shop.sql"}

    def test_backup_missing_for_current_deploy_errors(self):
        from frappe_manager.commands.deploy import _find_current_deploy_backups

        state = self._state()
        state.current.backups = {}
        dumps, error = _find_current_deploy_backups(state)
        assert dumps == {}
        assert "No DB backup recorded for the current deploy (local/x:t3)" in error

    def test_no_current_deploy_errors(self):
        from frappe_manager.commands.deploy import _find_current_deploy_backups

        dumps, error = _find_current_deploy_backups(None)
        assert dumps == {}
        assert "No current deploy recorded" in error

    def test_the_same_image_deployed_twice_is_no_longer_ambiguous(self):
        """The exact defect full records fix: history can hold two rows for the same image (a
        rollback, then forward again), which used to force `_find_current_deploy_backups` to
        SEARCH history and guess which row was "current" by taking the last match. Reading
        `current.backups` directly has no search and no guess to get wrong."""
        from frappe_manager.commands.deploy import _find_current_deploy_backups

        state = Deployments(
            current=Deployment(
                app_image="local/x:t1",
                deployed_at="d3",
                migrate_status="rollback",
                backups={"x.localhost": "/b/third.sql"},
            ),
            history=[
                Deployment(
                    app_image="local/x:t1",
                    deployed_at="d1",
                    migrate_status="migrated",
                    backups={"x.localhost": "/b/first.sql"},
                ),
                Deployment(app_image="local/x:t2", deployed_at="d2", migrate_status="migrated"),
                Deployment(
                    app_image="local/x:t1",
                    deployed_at="d3",
                    migrate_status="rollback",
                    backups={"x.localhost": "/b/third.sql"},
                ),
            ],
        )
        dumps, error = _find_current_deploy_backups(state)
        assert error is None
        assert dumps == {"x.localhost": "/b/third.sql"}


class TestReleasePrunePlanner:
    """Retention + artifact-safety contracts (pure fns in deploy_orchestrator)."""

    def _hist(self, *tags, backups=None):
        backups = backups or {}
        return [
            Deployment(app_image=t, deployed_at=f"d{i}", migrate_status="skipped", backups=backups.get(i) or {})
            for i, t in enumerate(tags)
        ]

    def test_rows_keep_newest_n(self):
        from frappe_manager.site_manager.modules.deploy_orchestrator import plan_release_prune

        kept, pruned = plan_release_prune(self._hist("a", "b", "c", "d", "e"), 2)
        assert [e.app_image for e in kept] == ["d", "e"]
        assert [e.app_image for e in pruned] == ["a", "b", "c"]

    def test_rows_keep_clamped_to_at_least_one(self):
        from frappe_manager.site_manager.modules.deploy_orchestrator import plan_release_prune

        kept, pruned = plan_release_prune(self._hist("a", "b"), 0)
        assert [e.app_image for e in kept] == ["b"]

    def test_rows_short_history_prunes_nothing(self):
        from frappe_manager.site_manager.modules.deploy_orchestrator import plan_release_prune

        kept, pruned = plan_release_prune(self._hist("a", "b"), 7)
        assert len(kept) == 2
        assert pruned == []

    def test_pingpong_rows_prune_even_when_images_protected(self):
        # The 33-entry ping-pong bench: rows go, artifacts stay.
        from frappe_manager.site_manager.modules.deploy_orchestrator import (
            plan_artifact_removal,
            plan_release_prune,
        )

        history = self._hist("x", "y", "x", "y", "x")
        kept, pruned = plan_release_prune(history, 2)
        assert len(pruned) == 3  # rows DO prune
        backups, images = plan_artifact_removal(kept, pruned, {"x", "y"})
        assert images == []  # protected images never rmi'd
        assert backups == []

    def test_unreferenced_image_is_removable_protected_is_not(self):
        from frappe_manager.site_manager.modules.deploy_orchestrator import (
            plan_artifact_removal,
            plan_release_prune,
        )

        kept, pruned = plan_release_prune(self._hist("old1", "old2", "cur"), 1)
        backups, images = plan_artifact_removal(kept, pruned, {"cur", "old2"})  # old2 = previous
        assert images == ["old1"]

    def test_backup_survives_while_a_kept_row_references_it(self):
        from frappe_manager.site_manager.modules.deploy_orchestrator import (
            plan_artifact_removal,
            plan_release_prune,
        )

        history = self._hist(
            "a",
            "b",
            "c",
            backups={
                0: {"x.localhost": "/b/one.sql"},
                1: {"x.localhost": "/b/shared.sql"},
                2: {"x.localhost": "/b/shared.sql"},
            },
        )
        kept, pruned = plan_release_prune(history, 1)
        backups, _tags = plan_artifact_removal(kept, pruned, {"c"})
        assert backups == ["/b/one.sql"]  # shared.sql referenced by kept row -> safe

    def test_every_site_dump_on_a_pruned_row_is_removable(self):
        # A multi-site row records one dump per schema; pruning the row has to free ALL of
        # them, or a bench that switches often keeps every non-primary site's dump forever.
        from frappe_manager.site_manager.modules.deploy_orchestrator import (
            plan_artifact_removal,
            plan_release_prune,
        )

        history = self._hist(
            "a",
            "b",
            backups={
                0: {"x.localhost": "/b/a-x.sql", "shop.x.localhost": "/b/a-shop.sql"},
                1: {"x.localhost": "/b/b-x.sql", "shop.x.localhost": "/b/b-shop.sql"},
            },
        )
        kept, pruned = plan_release_prune(history, 1)
        backups, _tags = plan_artifact_removal(kept, pruned, {"b"})
        assert backups == ["/b/a-shop.sql", "/b/a-x.sql"]

    def test_a_kept_row_protects_a_path_a_pruned_row_files_under_another_site(self):
        # The safety check is per PATH, not per site key: two sites can name the same dump
        # (a shared schema, or a row rewritten by migration), and one live reference is enough.
        from frappe_manager.site_manager.modules.deploy_orchestrator import (
            plan_artifact_removal,
            plan_release_prune,
        )

        history = self._hist(
            "a",
            "b",
            backups={
                0: {"shop.x.localhost": "/b/shared.sql", "x.localhost": "/b/only-old.sql"},
                1: {"x.localhost": "/b/shared.sql"},
            },
        )
        kept, pruned = plan_release_prune(history, 1)
        backups, _tags = plan_artifact_removal(kept, pruned, {"b"})
        assert backups == ["/b/only-old.sql"]
