from unittest.mock import MagicMock, patch

from frappe_manager.migration_manager.migration_helpers import (
    MigrationBench,
    MigrationBenches,
    MigrationServicesManager,
)


class TestMigrationBench:
    def test_init_loads_existing_compose_file(self, mock_bench_path):
        with (
            patch("frappe_manager.migration_manager.migration_helpers.ComposeFile") as mock_compose_file,
            patch("frappe_manager.migration_manager.migration_helpers.DockerClient") as mock_docker_client,
        ):
            bench = MigrationBench("test-bench", mock_bench_path)

            assert bench.name == "test-bench"
            assert bench.path == mock_bench_path

            mock_compose_file.assert_any_call(mock_bench_path / "docker-compose.yml", "docker-compose.tmpl")

    def test_init_uses_main_templates_not_base_templates(self, mock_bench_path):
        with (
            patch("frappe_manager.migration_manager.migration_helpers.ComposeFile") as mock_compose_file,
            patch("frappe_manager.migration_manager.migration_helpers.DockerClient"),
        ):
            MigrationBench("test-bench", mock_bench_path)

            call_args = mock_compose_file.call_args_list[0]
            assert "template_dir" not in call_args[1]

    def test_get_db_connection_info_calls_helper(self, mock_bench_path):
        with (
            patch("frappe_manager.migration_manager.migration_helpers.ComposeFile"),
            patch("frappe_manager.migration_manager.migration_helpers.DockerClient"),
            patch("frappe_manager.utils.site.get_bench_db_connection_info") as mock_db_info,
        ):
            mock_db_info.return_value = {"host": "mariadb", "port": 3306}

            bench = MigrationBench("test-bench", mock_bench_path)
            result = bench.get_db_connection_info()

            mock_db_info.assert_called_once_with("test-bench", mock_bench_path)
            assert result == {"host": "mariadb", "port": 3306}

    def test_common_bench_config_set_updates_json(self, mock_bench_path):
        with (
            patch("frappe_manager.migration_manager.migration_helpers.ComposeFile"),
            patch("frappe_manager.migration_manager.migration_helpers.DockerClient"),
        ):
            bench = MigrationBench("test-bench", mock_bench_path)

            new_config = {"test_key": "test_value"}
            result = bench.common_bench_config_set(new_config)

            assert result is True

            config_path = mock_bench_path / "workspace/frappe-bench/sites/common_site_config.json"
            import json

            with open(config_path) as f:
                config = json.load(f)
            assert config["test_key"] == "test_value"
            assert config["db_host"] == "mariadb"

    def test_common_bench_config_set_returns_false_if_file_missing(self, tmp_path):
        with (
            patch("frappe_manager.migration_manager.migration_helpers.ComposeFile"),
            patch("frappe_manager.migration_manager.migration_helpers.DockerClient"),
        ):
            bench_path = tmp_path / "no-config-bench"
            bench_path.mkdir()
            (bench_path / "docker-compose.yml").write_text("version: '3.9'")

            bench = MigrationBench("no-config-bench", bench_path)
            result = bench.common_bench_config_set({"key": "value"})

            assert result is False


class TestMigrationBenches:
    def test_get_all_benches_finds_benches_with_compose_files(self, tmp_path):
        benches_path = tmp_path / "benches"
        benches_path.mkdir()

        bench1 = benches_path / "bench1"
        bench1.mkdir()
        (bench1 / "docker-compose.yml").write_text("version: '3.9'")

        bench2 = benches_path / "bench2"
        bench2.mkdir()
        (bench2 / "docker-compose.yml").write_text("version: '3.9'")

        bench3_no_compose = benches_path / "bench3"
        bench3_no_compose.mkdir()

        migration_benches = MigrationBenches(benches_path)
        result = migration_benches.get_all_benches()

        assert len(result) == 2
        assert "bench1" in result
        assert "bench2" in result
        assert "bench3" not in result

    def test_get_all_benches_excludes_specified_benches(self, tmp_path):
        benches_path = tmp_path / "benches"
        benches_path.mkdir()

        bench1 = benches_path / "bench1"
        bench1.mkdir()
        (bench1 / "docker-compose.yml").write_text("version: '3.9'")

        excluded_bench = benches_path / "excluded"
        excluded_bench.mkdir()
        (excluded_bench / "docker-compose.yml").write_text("version: '3.9'")

        migration_benches = MigrationBenches(benches_path)
        result = migration_benches.get_all_benches(exclude=["excluded"])

        assert len(result) == 1
        assert "bench1" in result
        assert "excluded" not in result

    def test_stop_benches_calls_stop_on_all_benches(self, tmp_path):
        benches_path = tmp_path / "benches"
        benches_path.mkdir()

        bench1 = benches_path / "bench1"
        bench1.mkdir()
        (bench1 / "docker-compose.yml").write_text("version: '3.9'")

        with patch("frappe_manager.migration_manager.migration_helpers.MigrationBench") as mock_bench_class:
            mock_bench_instance = MagicMock()
            mock_bench_class.return_value = mock_bench_instance

            migration_benches = MigrationBenches(benches_path)
            migration_benches.stop_benches(timeout=50)

            mock_bench_instance.docker.compose.stop.assert_called_once_with(timeout=50, stream=False)


class TestMigrationServicesManager:
    def test_init_uses_main_templates_not_base_templates(self, mock_services_path):
        with (
            patch("frappe_manager.migration_manager.migration_helpers.ComposeFile") as mock_compose_file,
            patch("frappe_manager.migration_manager.migration_helpers.DockerClient"),
        ):
            MigrationServicesManager(mock_services_path)

            call_args = mock_compose_file.call_args_list[0]
            assert "template_dir" not in call_args[1]

    def test_init_uses_linux_template_by_default(self, mock_services_path):
        with (
            patch("frappe_manager.migration_manager.migration_helpers.ComposeFile") as mock_compose_file,
            patch("frappe_manager.migration_manager.migration_helpers.DockerClient"),
            patch("frappe_manager.migration_manager.migration_helpers.platform.system", return_value="Linux"),
        ):
            MigrationServicesManager(mock_services_path)

            call_args = mock_compose_file.call_args_list[0]
            assert call_args[0][1] == "docker-compose.services.tmpl"

    def test_init_uses_osx_template_on_darwin(self, mock_services_path):
        with (
            patch("frappe_manager.migration_manager.migration_helpers.ComposeFile") as mock_compose_file,
            patch("frappe_manager.migration_manager.migration_helpers.DockerClient"),
            patch("frappe_manager.migration_manager.migration_helpers.platform.system", return_value="Darwin"),
        ):
            MigrationServicesManager(mock_services_path)

            call_args = mock_compose_file.call_args_list[0]
            assert call_args[0][1] == "docker-compose.services.osx.tmpl"
