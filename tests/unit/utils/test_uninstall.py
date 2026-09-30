"""Unit tests for the `fm self uninstall` teardown planner."""

from pathlib import Path
from unittest.mock import patch

import pytest

from frappe_manager.utils.uninstall import FM_IMAGE_PREFIX, PathEntry, Scope, TeardownPlan, bench_names, plan_teardown


class FakeDocker:
    """Realistic-shaped stand-in for DockerClient: no MagicMock auto-attributes."""

    def __init__(self, containers=(), networks=(), images=(), volumes=(), container_images=None):
        self._containers = list(containers)
        self._networks = list(networks)
        self._images = list(images)
        self._volumes = list(volumes)
        self._container_images = dict(container_images or {})

    def container_names(self, name_prefix: str) -> list[str]:
        return [name for name in self._containers if name.startswith(name_prefix)]

    def network_ls(self) -> list[str]:
        return list(self._networks)

    def volume_ls(self) -> list[str]:
        return list(self._volumes)

    def images(self, label: str | None = None) -> list[dict]:
        # Mirrors `docker images --filter label=...`: the real client narrows server-side, so a
        # stand-in that ignored the filter would let a test pass on an image fm cannot actually
        # recognise.
        if label is None:
            return list(self._images)
        return [image for image in self._images if label in (image.get("Labels") or "")]

    def container_images(self) -> dict[str, str]:
        return dict(self._container_images)


def make_bench(base, name, complete=True):
    bench_dir = base / name
    bench_dir.mkdir()
    if complete:
        (bench_dir / "bench_config.toml").write_text("")
    (bench_dir / "marker.txt").write_text("x")
    return bench_dir


@pytest.mark.unit
class TestBenchNames:
    def test_config_less_directory_reported_as_incomplete_not_bench(self, tmp_path):
        """A directory without bench_config.toml (a failed `fm create`) is incomplete, never a bench."""
        with patch("frappe_manager.utils.uninstall.CLI_BENCHES_DIRECTORY", tmp_path):
            make_bench(tmp_path, "good-bench", complete=True)
            make_bench(tmp_path, "half-built", complete=False)
            complete, incomplete = bench_names()
        assert complete == ["good-bench"]
        assert incomplete == ["half-built"]

    def test_missing_benches_directory_returns_empty(self, tmp_path):
        """A host with no benches directory yet reports no benches and no incomplete entries."""
        with patch("frappe_manager.utils.uninstall.CLI_BENCHES_DIRECTORY", tmp_path / "nope"):
            complete, incomplete = bench_names()
        assert complete == []
        assert incomplete == []


@pytest.mark.unit
class TestScopeFiltering:
    def test_benches_scope_plans_bench_dirs_and_containers_not_services(self, tmp_path):
        """Scope.benches plans bench dirs/containers but never touches the services directory."""
        benches_dir = tmp_path / "sites"
        benches_dir.mkdir()
        services_dir = tmp_path / "services"
        services_dir.mkdir()
        (services_dir / "compose.yaml").write_text("x")
        make_bench(benches_dir, "erp", complete=True)
        docker = FakeDocker(containers=["fm__erp__frappe", "fm_mariadb"])
        with (
            patch("frappe_manager.utils.uninstall.CLI_BENCHES_DIRECTORY", benches_dir),
            patch("frappe_manager.utils.uninstall.CLI_SERVICES_DIRECTORY", services_dir),
            patch("frappe_manager.utils.uninstall.CLI_DIR", tmp_path),
        ):
            plan = plan_teardown(docker, {Scope.benches}, keep_backups=False, include_images=False)
        assert plan.benches == ["erp"]
        assert plan.containers == ["fm__erp__frappe"]
        assert services_dir not in [entry.path for entry in plan.paths]

    def test_services_scope_plans_services_dir_and_globals_not_benches(self, tmp_path):
        """Scope.services plans the services dir and global containers/networks but never benches."""
        benches_dir = tmp_path / "sites"
        benches_dir.mkdir()
        services_dir = tmp_path / "services"
        services_dir.mkdir()
        (services_dir / "compose.yaml").write_text("x")
        make_bench(benches_dir, "erp", complete=True)
        docker = FakeDocker(
            containers=["fm_mariadb", "fm_nginx-proxy"],
            networks=["fm-frontend-network", "fm-backend-network"],
        )
        with (
            patch("frappe_manager.utils.uninstall.CLI_BENCHES_DIRECTORY", benches_dir),
            patch("frappe_manager.utils.uninstall.CLI_SERVICES_DIRECTORY", services_dir),
            patch("frappe_manager.utils.uninstall.CLI_DIR", tmp_path),
        ):
            plan = plan_teardown(docker, {Scope.services}, keep_backups=False, include_images=False)
        assert plan.benches == []
        assert benches_dir / "erp" not in [entry.path for entry in plan.paths]
        assert services_dir in [entry.path for entry in plan.paths]
        assert set(plan.containers) == {"fm_mariadb", "fm_nginx-proxy"}
        assert set(plan.networks) == {"fm-frontend-network", "fm-backend-network"}

    def test_benches_scope_plans_per_bench_networks_and_volumes(self, tmp_path):
        """A bench's own network and named volumes are teardown targets: `docker rm -v` leaves
        named volumes behind, and a stranded network holds a subnet out of docker's address pool."""
        benches_dir = tmp_path / "sites"
        benches_dir.mkdir()
        make_bench(benches_dir, "erp", complete=True)
        docker = FakeDocker(
            containers=["fm__erp__frappe"],
            networks=["fm__erp__site-network", "fm-frontend-network", "bridge"],
            volumes=["fm__erp__fm-sockets", "fm__erp__redis-cache-data", "some-other-project-data"],
        )
        with (
            patch("frappe_manager.utils.uninstall.CLI_BENCHES_DIRECTORY", benches_dir),
            patch("frappe_manager.utils.uninstall.CLI_SERVICES_DIRECTORY", tmp_path / "services"),
            patch("frappe_manager.utils.uninstall.CLI_DIR", tmp_path),
        ):
            plan = plan_teardown(docker, {Scope.benches}, keep_backups=False, include_images=False)
        assert plan.networks == ["fm__erp__site-network"]
        assert plan.volumes == ["fm__erp__fm-sockets", "fm__erp__redis-cache-data"]

    def test_benches_scope_plans_docker_objects_of_a_bench_whose_directory_is_gone(self, tmp_path):
        """Objects are found by prefix scan, not by names built from on-disk benches: a half-deleted
        bench leaves docker objects with nothing on disk left to name them."""
        benches_dir = tmp_path / "sites"
        benches_dir.mkdir()
        docker = FakeDocker(
            networks=["fm__ghost__site-network"],
            volumes=["fm__ghost__fm-sockets"],
        )
        with (
            patch("frappe_manager.utils.uninstall.CLI_BENCHES_DIRECTORY", benches_dir),
            patch("frappe_manager.utils.uninstall.CLI_SERVICES_DIRECTORY", tmp_path / "services"),
            patch("frappe_manager.utils.uninstall.CLI_DIR", tmp_path),
        ):
            plan = plan_teardown(docker, {Scope.benches}, keep_backups=False, include_images=False)
        assert plan.benches == []
        assert plan.networks == ["fm__ghost__site-network"]
        assert plan.volumes == ["fm__ghost__fm-sockets"]
        assert not plan.is_empty()

    def test_services_scope_plans_fms_own_data_volumes_only(self, tmp_path):
        """The macOS services tier keeps its data in named volumes; a third-party volume that
        merely starts with 'fm' is not fm's to delete."""
        services_dir = tmp_path / "services"
        services_dir.mkdir()
        docker = FakeDocker(
            containers=["fm_mariadb"],
            volumes=["fm-mariadb-data", "fm-postgres-data", "fmsomething-else"],
        )
        with (
            patch("frappe_manager.utils.uninstall.CLI_BENCHES_DIRECTORY", tmp_path / "sites"),
            patch("frappe_manager.utils.uninstall.CLI_SERVICES_DIRECTORY", services_dir),
            patch("frappe_manager.utils.uninstall.CLI_DIR", tmp_path),
        ):
            plan = plan_teardown(docker, {Scope.services}, keep_backups=False, include_images=False)
        assert plan.volumes == ["fm-mariadb-data", "fm-postgres-data"]


@pytest.mark.unit
class TestKeepBackups:
    def test_keep_backups_excludes_backups_but_keeps_rest_of_host_tier(self, tmp_path):
        """keep_backups=True omits the backups dir from the plan while planning other host paths."""
        cli_dir = tmp_path / "frappe"
        cli_dir.mkdir()
        benches_dir = cli_dir / "sites"
        benches_dir.mkdir()
        services_dir = cli_dir / "services"
        services_dir.mkdir()
        backups_dir = cli_dir / "backups"
        backups_dir.mkdir()
        (backups_dir / "session.tar").write_text("x")
        config_file = cli_dir / "fm_config.toml"
        config_file.write_text("x")
        cache_dir = tmp_path / "cache"
        cache_dir.mkdir()
        with (
            patch("frappe_manager.utils.uninstall.CLI_DIR", cli_dir),
            patch("frappe_manager.utils.uninstall.CLI_BENCHES_DIRECTORY", benches_dir),
            patch("frappe_manager.utils.uninstall.CLI_SERVICES_DIRECTORY", services_dir),
            patch("frappe_manager.utils.uninstall.CLI_CACHE_PATH", cache_dir),
        ):
            plan = plan_teardown(None, {Scope.host}, keep_backups=True, include_images=False)
        planned_paths = [entry.path for entry in plan.paths]
        assert backups_dir not in planned_paths
        assert config_file in planned_paths

    def test_keep_backups_false_includes_backups_in_host_tier(self, tmp_path):
        """keep_backups=False includes the backups directory among the planned host paths."""
        cli_dir = tmp_path / "frappe"
        cli_dir.mkdir()
        benches_dir = cli_dir / "sites"
        benches_dir.mkdir()
        services_dir = cli_dir / "services"
        services_dir.mkdir()
        backups_dir = cli_dir / "backups"
        backups_dir.mkdir()
        (backups_dir / "session.tar").write_text("x")
        cache_dir = tmp_path / "cache"
        cache_dir.mkdir()
        with (
            patch("frappe_manager.utils.uninstall.CLI_DIR", cli_dir),
            patch("frappe_manager.utils.uninstall.CLI_BENCHES_DIRECTORY", benches_dir),
            patch("frappe_manager.utils.uninstall.CLI_SERVICES_DIRECTORY", services_dir),
            patch("frappe_manager.utils.uninstall.CLI_CACHE_PATH", cache_dir),
        ):
            plan = plan_teardown(None, {Scope.host}, keep_backups=False, include_images=False)
        assert backups_dir in [entry.path for entry in plan.paths]


@pytest.mark.unit
class TestImageFiltering:
    def test_a_baked_image_is_found_by_its_label_not_its_name(self, tmp_path):
        """`fm bake --app-image local/anything:v1` produces an image whose repository and tag the
        operator chose, so nothing in the reference identifies it as fm's; once the bench that
        built it is gone it runs no container either. Every baked image therefore survived an
        uninstall that reported it had removed what fm built. The label is the only handle."""
        docker = FakeDocker(
            images=[
                {"Repository": "local/anything", "Tag": "v1", "Labels": "fm.baked=true"},
                {"Repository": "someone/else", "Tag": "v9", "Labels": ""},
            ]
        )
        with (
            patch("frappe_manager.utils.uninstall.CLI_DIR", tmp_path),
            patch("frappe_manager.utils.uninstall.CLI_BENCHES_DIRECTORY", tmp_path / "sites"),
            patch("frappe_manager.utils.uninstall.CLI_SERVICES_DIRECTORY", tmp_path / "services"),
            patch("frappe_manager.utils.uninstall.CLI_CACHE_PATH", tmp_path / "cache"),
        ):
            plan = plan_teardown(docker, {Scope.host}, keep_backups=False, include_images=True)

        assert "local/anything:v1" in plan.images
        assert "someone/else:v9" not in plan.images

    def test_include_images_false_yields_empty_image_list(self, tmp_path):
        """include_images=False never lists any image, even when fm images exist."""
        docker = FakeDocker(images=[{"Repository": f"{FM_IMAGE_PREFIX}/frappe", "Tag": "1.0"}])
        with (
            patch("frappe_manager.utils.uninstall.CLI_DIR", tmp_path),
            patch("frappe_manager.utils.uninstall.CLI_BENCHES_DIRECTORY", tmp_path / "sites"),
            patch("frappe_manager.utils.uninstall.CLI_SERVICES_DIRECTORY", tmp_path / "services"),
            patch("frappe_manager.utils.uninstall.CLI_CACHE_PATH", tmp_path / "cache"),
        ):
            plan = plan_teardown(docker, {Scope.host}, keep_backups=False, include_images=True)
        assert plan.images != []

        with (
            patch("frappe_manager.utils.uninstall.CLI_DIR", tmp_path),
            patch("frappe_manager.utils.uninstall.CLI_BENCHES_DIRECTORY", tmp_path / "sites"),
            patch("frappe_manager.utils.uninstall.CLI_SERVICES_DIRECTORY", tmp_path / "services"),
            patch("frappe_manager.utils.uninstall.CLI_CACHE_PATH", tmp_path / "cache"),
        ):
            plan_off = plan_teardown(docker, {Scope.host}, keep_backups=False, include_images=False)
        assert plan_off.images == []

    def test_stock_images_fm_pulls_are_planned_alongside_fms_own(self, tmp_path):
        """fm knows its images rather than guessing from the name: the stock set its compose
        templates name is pulled by fm and goes with it, not just the ghcr.io/rtcamp ones."""
        docker = FakeDocker(
            images=[
                {"Repository": f"{FM_IMAGE_PREFIX}/frappe", "Tag": "1.0"},
                {"Repository": "mariadb", "Tag": "11.8"},
                {"Repository": "someone-else/app", "Tag": "latest"},
            ]
        )
        with (
            patch("frappe_manager.utils.uninstall.CLI_DIR", tmp_path),
            patch("frappe_manager.utils.uninstall.CLI_BENCHES_DIRECTORY", tmp_path / "sites"),
            patch("frappe_manager.utils.uninstall.CLI_SERVICES_DIRECTORY", tmp_path / "services"),
            patch("frappe_manager.utils.uninstall.CLI_CACHE_PATH", tmp_path / "cache"),
            patch("frappe_manager.utils.site.get_all_docker_images", return_value={"mariadb": {"name": "mariadb", "tag": "11.8"}}),
        ):
            plan = plan_teardown(docker, {Scope.host}, keep_backups=False, include_images=True)
        assert plan.images == [f"{FM_IMAGE_PREFIX}/frappe:1.0", "mariadb:11.8"]
        assert "someone-else/app:latest" not in plan.images

    def test_an_image_another_projects_container_runs_is_kept_and_named(self, tmp_path):
        """`docker rmi -f` untags an image out from under a stopped container, so a host running
        its own redis must not lose it; the holder is reported so the decision is checkable."""
        docker = FakeDocker(
            containers=["fm__erp__frappe", "someones-cache"],
            images=[
                {"Repository": "redis", "Tag": "8-alpine"},
                {"Repository": "mariadb", "Tag": "11.8"},
            ],
            container_images={"fm__erp__frappe": "mariadb:11.8", "someones-cache": "redis:8-alpine"},
        )
        stock = {
            "redis": {"name": "redis", "tag": "8-alpine"},
            "mariadb": {"name": "mariadb", "tag": "11.8"},
        }
        with (
            patch("frappe_manager.utils.uninstall.CLI_DIR", tmp_path),
            patch("frappe_manager.utils.uninstall.CLI_BENCHES_DIRECTORY", tmp_path / "sites"),
            patch("frappe_manager.utils.uninstall.CLI_SERVICES_DIRECTORY", tmp_path / "services"),
            patch("frappe_manager.utils.uninstall.CLI_CACHE_PATH", tmp_path / "cache"),
            patch("frappe_manager.utils.site.get_all_docker_images", return_value=stock),
        ):
            plan = plan_teardown(docker, {Scope.benches, Scope.host}, keep_backups=False, include_images=True)
        assert plan.images == ["mariadb:11.8"]
        assert plan.images_in_use == [("redis:8-alpine", "someones-cache")]


@pytest.mark.unit
class TestDockerUnreachable:
    def test_none_docker_still_plans_filesystem_with_empty_docker_half(self, tmp_path):
        """docker=None yields the filesystem plan plus empty containers/networks/images, no raise."""
        benches_dir = tmp_path / "sites"
        benches_dir.mkdir()
        make_bench(benches_dir, "erp", complete=True)
        services_dir = tmp_path / "services"
        services_dir.mkdir()
        (services_dir / "compose.yaml").write_text("x")
        with (
            patch("frappe_manager.utils.uninstall.CLI_DIR", tmp_path),
            patch("frappe_manager.utils.uninstall.CLI_BENCHES_DIRECTORY", benches_dir),
            patch("frappe_manager.utils.uninstall.CLI_SERVICES_DIRECTORY", services_dir),
            patch("frappe_manager.utils.uninstall.CLI_CACHE_PATH", tmp_path / "cache"),
        ):
            scopes = {Scope.benches, Scope.services, Scope.host}
            plan = plan_teardown(None, scopes, keep_backups=False, include_images=True)
        assert plan.benches == ["erp"]
        assert services_dir in [entry.path for entry in plan.paths]
        assert plan.containers == []
        assert plan.networks == []
        assert plan.images == []


@pytest.mark.unit
class TestTeardownPlanShape:
    def test_is_empty_true_only_when_nothing_would_be_touched(self):
        """is_empty() is True only when containers, networks, images, paths, and trust are all empty."""
        assert TeardownPlan().is_empty() is True
        assert TeardownPlan(containers=["fm_mariadb"]).is_empty() is False
        assert TeardownPlan(paths=[PathEntry(path=Path("/tmp/x"), size=0)]).is_empty() is False

    def test_total_size_sums_path_entry_sizes(self):
        """total_size is the sum of each planned path's recorded size, ignoring unrelated fields."""
        plan = TeardownPlan(paths=[PathEntry(path=Path("/a"), size=100), PathEntry(path=Path("/b"), size=250)])
        assert plan.total_size == 350
