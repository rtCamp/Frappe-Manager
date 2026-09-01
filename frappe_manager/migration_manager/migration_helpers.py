import json
import platform
from pathlib import Path

from frappe_manager import CLI_SERVICES_DIRECTORY
from frappe_manager.docker import ComposeFile, DockerClient
from frappe_manager.migration_manager.migration_constants import MIGRATION_BENCH_STOP_TIMEOUT_SECONDS
from frappe_manager.output_manager.base import OutputHandler


class MigrationBench:
    def __init__(self, name: str, path: Path, output: OutputHandler | None = None) -> None:
        self.name = name
        self.path = path
        self.output = output
        self.compose_file_manager = ComposeFile(
            self.path / "docker-compose.yml",
            "docker-compose.tmpl",
        )
        self.docker = DockerClient(compose_file_path=self.compose_file_manager.compose_path, output=output)

        self.workers_compose_file_manager = ComposeFile(
            self.path / "docker-compose.workers.yml",
            "docker-compose.workers.tmpl",
        )
        self.workers_docker = DockerClient(
            compose_file_path=self.workers_compose_file_manager.compose_path,
            output=output,
        )

    @property
    def compose(self):
        assert self.docker.compose is not None
        return self.docker.compose

    @property
    def site_names(self) -> list[str]:
        """The sites this bench serves, read straight out of `bench_config.toml`.

        Raw TOML on purpose: a migration runs against whatever shape is on disk, which is by
        definition a shape the current model may refuse, so `BenchConfig` is not usable here.

        Falls back to the bench's own name when no `[sites]` table is recorded. That is the
        pre-decoupling shape every migration up to 0.20.0 was written against, where the bench name
        IS the site name, so the fallback keeps those benches behaving exactly as before. A bench
        that HAS the table may hold several sites, and may hold one whose name is not the bench's,
        which is the case that used to send every caller here looking for `sites/<bench>/` and
        finding nothing.
        """
        config_path = self.path / "bench_config.toml"
        if not config_path.exists():
            return [self.name]

        try:
            import tomlkit

            sites = tomlkit.parse(config_path.read_text()).get("sites") or {}
        except Exception as e:
            if self.output:
                self.output.warning(f"Failed to read [sites] from bench_config.toml: {e}")
            return [self.name]

        return [str(site) for site in sites] or [self.name]

    @property
    def running(self) -> bool:
        services = self.compose_file_manager.get_services_list()
        running_status = self.get_services_running_status()

        if not running_status:
            return False

        for service in services:
            try:
                if not running_status[service] == "running":
                    return False
            except KeyError:
                return False
        return True

    @property
    def workers_running(self) -> bool:
        from frappe_manager.docker import DockerException

        services = self.workers_compose_file_manager.get_services_list()

        try:
            all_statuses = self.workers_docker.compose.get_all_services_status()
            containers = self.workers_compose_file_manager.get_container_names().values()
            running_status = {
                status["Service"]: status["State"] for status in all_statuses if status.get("Name") in containers
            }

            for service in services:
                if running_status.get(service) != "running":
                    return False
            return True
        except (DockerException, KeyError):
            return False

    def get_services_running_status(self) -> dict:
        from frappe_manager.docker import DockerException

        services = self.compose_file_manager.get_services_list()
        containers = self.compose_file_manager.get_container_names().values()

        try:
            all_statuses = self.compose.get_all_services_status()
            return {status["Service"]: status["State"] for status in all_statuses if status.get("Name") in containers}
        except DockerException:
            return {}

    def get_db_connection_info(self):
        from frappe_manager.utils.site import get_bench_db_connection_info

        return get_bench_db_connection_info(self.name, self.path)

    def common_bench_config_set(self, config: dict):
        """
        Sets the values in the common_site_config.json file.
        Args:
            config (dict): A dictionary containing the key-value pairs to be set in the common_site_config.json file.
        Returns:
            bool: True if the values are successfully set, False otherwise.
        """
        common_bench_config_path = self.path / "workspace/frappe-bench/sites/common_site_config.json"

        if not common_bench_config_path.exists():
            return False

        common_site_config = {}

        with open(common_bench_config_path) as f:
            common_site_config = json.load(f)

        try:
            for key, value in config.items():
                common_site_config[key] = value
            with open(common_bench_config_path, "w") as f:
                json.dump(common_site_config, f)
            return True
        except KeyError as e:
            return False


class MigrationBenches:
    def __init__(self, benches_path: Path) -> None:
        self.benches_path = benches_path

    def get_all_benches(self, exclude: list[str] | None = None):
        exclude = exclude or []
        benches: dict[str, Path] = {}
        for dir in self.benches_path.iterdir():
            if dir.is_dir() and dir.parts[-1] not in exclude:
                name = dir.parts[-1]
                dir = dir / "docker-compose.yml"
                if dir.exists():
                    benches[name] = dir
        return benches

    def stop_benches(self, timeout: int = MIGRATION_BENCH_STOP_TIMEOUT_SECONDS):
        compose_list = self.get_all_benches()
        for name, compose_path in compose_list.items():
            bench = MigrationBench(name, compose_path.parent)
            bench.docker.compose.stop(timeout=timeout, stream=False)


class MigrationServicesManager:
    def __init__(self, services_path: Path = CLI_SERVICES_DIRECTORY):
        self.services_path = services_path

        template_name = "docker-compose.services.tmpl"

        if platform.system() == "Darwin":
            template_name = "docker-compose.services.osx.tmpl"

        self.compose_file_manager = ComposeFile(
            self.services_path / "docker-compose.yml",
            template_name,
        )
        self.docker = DockerClient(compose_file_path=self.compose_file_manager.compose_path)

    @property
    def compose(self):
        assert self.docker.compose is not None
        return self.docker.compose
