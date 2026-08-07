"""
Tests for the v0.19.1 migration (image re-tagging).

v0.19.1 has no structural changes: it only re-tags the fm images in each
bench's generated compose files and stamps ``x-version``. These tests drive
``MigrationV0191`` against real compose files on disk and only mock Docker.
"""

from unittest.mock import MagicMock, Mock, patch

import pytest
from ruamel.yaml import YAML

from frappe_manager.migration_manager.migration_exections import MigrationExceptionInBench
from frappe_manager.migration_manager.migration_helpers import MigrationBench
from frappe_manager.migration_manager.migrations.migrate_0_19_1 import MigrationV0191

FRAPPE_IMAGE = "ghcr.io/rtcamp/frappe-manager-frappe"
NGINX_IMAGE = "ghcr.io/rtcamp/frappe-manager-nginx"
REDIS_IMAGE = "redis:8-alpine"

COMPOSE_MAIN_V0190 = f"""x-version: 0.19.0
services:
  frappe:
    image: {FRAPPE_IMAGE}:v0.19.0
    restart: always
  nginx:
    image: {NGINX_IMAGE}:v0.19.0
    restart: always
  socketio:
    image: {FRAPPE_IMAGE}:v0.19.0
    restart: always
  schedule:
    image: {FRAPPE_IMAGE}:v0.19.0
    restart: always
  redis-cache:
    image: {REDIS_IMAGE}
    restart: always
"""

COMPOSE_WORKERS_V0190 = f"""x-version: 0.19.0
services:
  bench-default-worker:
    image: {FRAPPE_IMAGE}:v0.19.0
    restart: always
  bench-long-worker:
    image: {FRAPPE_IMAGE}:v0.19.0
    restart: always
"""

COMPOSE_ADMIN_TOOLS = """services:
  adminer:
    image: adminer:4
    restart: always
  mailpit:
    image: axllent/mailpit:v1.22
    restart: always
"""

COMPOSE_OVERRIDE = """services:
  frappe:
    environment:
      MY_OWN_KNOB: "1"
"""


def load_yaml(path):
    return YAML(typ="safe").load(path.read_text())


def service_images(path) -> dict[str, str]:
    return {name: config["image"] for name, config in load_yaml(path)["services"].items() if "image" in config}


@pytest.fixture
def make_bench(tmp_path):
    """Build a MigrationBench over real compose files, with only Docker mocked."""

    def _make(name="test-bench", workers=True, admin_tools=True, override=False):
        bench_path = tmp_path / "sites" / name
        bench_path.mkdir(parents=True, exist_ok=True)

        (bench_path / "docker-compose.yml").write_text(COMPOSE_MAIN_V0190)
        if workers:
            (bench_path / "docker-compose.workers.yml").write_text(COMPOSE_WORKERS_V0190)
        if admin_tools:
            (bench_path / "docker-compose.admin-tools.yml").write_text(COMPOSE_ADMIN_TOOLS)
        if override:
            (bench_path / "docker-compose.override.yml").write_text(COMPOSE_OVERRIDE)

        def make_compose_file_manager(compose_path, *args, **kwargs):
            manager = MagicMock()
            manager.compose_path = compose_path
            manager.get_services_list.return_value = ["frappe"]
            manager.get_container_names.return_value = {}
            return manager

        with (
            patch(
                "frappe_manager.migration_manager.migration_helpers.DockerClient",
                side_effect=lambda *args, **kwargs: MagicMock(),
            ),
            patch(
                "frappe_manager.migration_manager.migration_helpers.ComposeFile",
                side_effect=make_compose_file_manager,
            ),
        ):
            bench = MigrationBench(name=name, path=bench_path)

        # Nothing is up: keeps `running`/`workers_running` deterministic so the
        # migration never tries to recreate containers.
        bench.docker.compose.get_all_services_status.return_value = []
        bench.workers_docker.compose.get_all_services_status.return_value = []
        bench.docker.compose.pull.return_value = Mock(exit_code=0)

        return bench

    return _make


@pytest.fixture
def make_migration():
    """Build MigrationV0191 with the fm version / installed image tag pinned."""

    def _make(fm_version="0.19.1", image_tag="v0.19.1"):
        with (
            patch("frappe_manager.utils.helpers.get_current_fm_version", return_value=fm_version),
            patch("frappe_manager.utils.helpers.get_docker_image_tag", return_value=image_tag),
        ):
            migration = MigrationV0191()

        migration.migration_executor = Mock()
        return migration

    return _make


class TestMigrationV0191ComposeRetag:
    """Compose files are re-tagged and stamped; foreign images are left alone."""

    def test_retags_fm_images_and_stamps_version_in_main_and_workers(self, make_migration, make_bench):
        migration = make_migration()
        bench = make_bench()

        migration.migrate_bench(bench)

        main = bench.path / "docker-compose.yml"
        assert service_images(main) == {
            "frappe": f"{FRAPPE_IMAGE}:v0.19.1",
            "nginx": f"{NGINX_IMAGE}:v0.19.1",
            "socketio": f"{FRAPPE_IMAGE}:v0.19.1",
            "schedule": f"{FRAPPE_IMAGE}:v0.19.1",
            "redis-cache": REDIS_IMAGE,
        }, "every fm image should move to v0.19.1 and redis must be untouched"
        assert str(load_yaml(main)["x-version"]) == "0.19.1"

        workers = bench.path / "docker-compose.workers.yml"
        assert service_images(workers) == {
            "bench-default-worker": f"{FRAPPE_IMAGE}:v0.19.1",
            "bench-long-worker": f"{FRAPPE_IMAGE}:v0.19.1",
        }
        assert str(load_yaml(workers)["x-version"]) == "0.19.1"

    def test_compose_without_fm_images_is_untouched(self, make_migration, make_bench):
        migration = make_migration()
        bench = make_bench()
        admin_tools = bench.path / "docker-compose.admin-tools.yml"
        before = admin_tools.read_bytes()

        migration.migrate_bench(bench)

        after = admin_tools.read_bytes()
        assert after == before, "admin-tools has no fm images, so it must not be rewritten"
        assert "x-version" not in after.decode(), "no x-version key may be introduced"

    def test_pulls_once_and_second_run_is_a_no_op(self, make_migration, make_bench):
        migration = make_migration()
        bench = make_bench()

        migration.migrate_bench(bench)
        assert bench.compose.pull.call_count == 1, "first run changes images, so it must pull"

        after_first_run = (bench.path / "docker-compose.yml").read_bytes()
        bench.compose.pull.reset_mock()

        migration.migrate_bench(bench)

        assert bench.compose.pull.call_count == 0, "nothing changed, so the second run must not pull"
        assert (bench.path / "docker-compose.yml").read_bytes() == after_first_run


class TestMigrationV0191ImageTagSelection:
    """Regression guard for issue #452: stable releases tag with their own version."""

    @pytest.mark.parametrize(
        ("fm_version", "installed_tag"),
        [
            ("0.19.1", "v0.19.0"),  # stale installed tag must not win
            ("0.20.5", "v0.20.5"),  # a newer running fm must not win either
        ],
    )
    def test_stable_release_tags_with_migration_version(self, make_migration, make_bench, fm_version, installed_tag):
        migration = make_migration(fm_version=fm_version, image_tag=installed_tag)
        bench = make_bench()

        assert migration.is_dev_environment is False
        assert migration._get_image_tag_for_migration() == "v0.19.1"

        migration.migrate_bench(bench)

        images = service_images(bench.path / "docker-compose.yml")
        assert images["frappe"] == f"{FRAPPE_IMAGE}:v0.19.1"
        assert images["nginx"] == f"{NGINX_IMAGE}:v0.19.1"

    def test_dev_release_tags_with_installed_image_tag(self, make_migration, make_bench):
        migration = make_migration(fm_version="0.20.0.dev0", image_tag="v0.20.0.dev0")
        bench = make_bench()

        assert migration.is_dev_environment is True
        assert migration._get_image_tag_for_migration() == "v0.20.0.dev0"

        migration.migrate_bench(bench)

        main = bench.path / "docker-compose.yml"
        images = service_images(main)
        assert images["frappe"] == f"{FRAPPE_IMAGE}:v0.20.0.dev0"
        assert images["nginx"] == f"{NGINX_IMAGE}:v0.20.0.dev0"
        assert images["redis-cache"] == REDIS_IMAGE
        assert str(load_yaml(main)["x-version"]) == "0.19.1", "x-version tracks the migration, not the image tag"


class TestMigrationV0191PullFailure:
    def test_non_zero_pull_exit_code_raises(self, make_migration, make_bench):
        migration = make_migration()
        bench = make_bench()
        bench.compose.pull.return_value = Mock(exit_code=1)

        with pytest.raises(MigrationExceptionInBench, match="test-bench"):
            migration._pull_bench_images(bench)

    def test_zero_pull_exit_code_does_not_raise(self, make_migration, make_bench):
        migration = make_migration()
        bench = make_bench()
        bench.compose.pull.return_value = Mock(exit_code=0)

        migration._pull_bench_images(bench)

        bench.compose.pull.assert_called_once_with(stream=False)


class TestManagedComposePaths:
    def test_returns_generated_files_in_declared_order_and_ignores_override(self, make_bench):
        bench = make_bench(override=True)

        assert bench.managed_compose_paths == [
            bench.path / "docker-compose.yml",
            bench.path / "docker-compose.workers.yml",
            bench.path / "docker-compose.admin-tools.yml",
        ]

    def test_skips_generated_files_that_are_absent(self, make_bench):
        bench = make_bench(workers=False, admin_tools=False, override=True)

        assert bench.managed_compose_paths == [bench.path / "docker-compose.yml"]
