import json
from pathlib import Path
import platform
from typing import Dict
from frappe_manager import CLI_SERVICES_DIRECTORY
from frappe_manager.compose_manager.ComposeFile import ComposeFile

from frappe_manager.compose_project.compose_project import ComposeProject
from frappe_manager.utils.site import get_bench_db_connection_info


class MigrationBench:
    def __init__(self, name: str, path: Path) -> None:
        self.name = name
        self.path = path
        self.compose_project = ComposeProject(
            ComposeFile(
                self.path / 'docker-compose.yml',
                'docker-compose.tmpl',
                template_dir='migration_manager/base_templates',
            )
        )
        self.workers_compose_project = ComposeProject(
            ComposeFile(self.path / 'docker-compose.workers.yml', 'docker-compose.workers.tmpl')
        )

    def get_db_connection_info(self):
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

        with open(common_bench_config_path, "r") as f:
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

    def get_all_benches(self, exclude=[]):
        # get list of all sites
        benches: Dict[str, Path] = {}
        for dir in self.benches_path.iterdir():
            if dir.is_dir() and dir.parts[-1] not in exclude:
                name = dir.parts[-1]
                dir = dir / "docker-compose.yml"
                if dir.exists():
                    benches[name] = dir
        return benches

    def stop_benches(self, timeout: int = 100):
        compose_list = self.get_all_benches()
        for name, compose_path in compose_list.items():
            bench = MigrationBench(name, compose_path.parent)
            bench.compose_project.stop_service(timeout=timeout)


class MigrationServicesManager:
    def __init__(self, services_path: Path = CLI_SERVICES_DIRECTORY):
        self.services_path = services_path

        template_name = 'docker-compose.services.tmpl'

        if platform.system() == "Darwin":
            template_name = 'docker-compose.services.osx.tmpl'

        self.compose_project = ComposeProject(
            ComposeFile(
                self.services_path / 'docker-compose.yml',
                template_name,
                template_dir='migration_manager/base_templates',
            )
        )
