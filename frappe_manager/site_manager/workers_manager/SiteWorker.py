from copy import deepcopy
from pathlib import Path
from typing import List, TYPE_CHECKING
from frappe_manager.compose_manager.ComposeFile import ComposeFile
from frappe_manager.compose_project.compose_project import ComposeProject
from frappe_manager.display_manager.DisplayManager import richprint
from frappe_manager.site_manager.site_exceptions import BenchWorkersSupervisorConfigurtionNotFoundError
from frappe_manager.utils.helpers import get_container_name_prefix, get_current_fm_version

if TYPE_CHECKING:
    from frappe_manager.site_manager.site import Bench


class BenchWorkers:
    def __init__(self, bench: 'Bench', verbose: bool = True):
        self.bench = bench
        self.compose_path = self.bench.path / "docker-compose.workers.yml"
        self.config_dir = self.bench.path / "workspace" / "frappe-bench" / "config"
        self.supervisor_config_path = self.config_dir / "supervisor.conf"
        self.quiet = not verbose
        self.compose_project = ComposeProject(
            ComposeFile(self.compose_path, template_name='docker-compose.workers.tmpl')
        )

    def get_expected_workers(self) -> List[str]:
        richprint.change_head("Checking workers info.")

        workers_supervisor_conf_paths = []

        for file_path in self.config_dir.iterdir():
            file_path_abs = str(file_path.absolute())
            if file_path.is_file():
                if file_path_abs.endswith(".workers.fm.supervisor.conf"):
                    workers_supervisor_conf_paths.append(file_path)

        if len(workers_supervisor_conf_paths) == 0:
            raise BenchWorkersSupervisorConfigurtionNotFoundError(self.bench.name, self.config_dir)

        workers_expected_service_names = []

        for worker_name in workers_supervisor_conf_paths:
            worker_name = worker_name.name
            worker_name = worker_name.replace("frappe-bench-frappe-", "")
            worker_name = worker_name.replace(".workers.fm.supervisor.conf", "")
            workers_expected_service_names.append(worker_name)

        workers_expected_service_names.sort()

        return workers_expected_service_names

    def is_new_workers_added(self) -> bool:
        if not self.compose_project.compose_file_manager.is_template_loaded:
            prev_workers = self.compose_project.compose_file_manager.get_services_list()
            prev_workers.sort()
            expected_workers = self.get_expected_workers()
            # get custom workers from common_site_config.json
            # common_site_config_data = self.bench.get_common_bench_config()

            # if 'workers' in common_site_config_data:
            #     custom_workers: List[str] = common_site_config_data['workers'].keys()
            #     for worker in custom_workers:
            #         worker = f'{worker}-worker'
            #         if worker not in prev_workers:
            #             return False
            return prev_workers == expected_workers
        else:
            return False

    def generate_compose(self):
        if not self.compose_path.exists():
            richprint.print("Workers compose not present. Generating...")
        else:
            richprint.print("Workers configuration changed. Recreating compose...")

        # create compose file for workers
        self.compose_project.compose_file_manager.yml = self.compose_project.compose_file_manager.load_template()

        template_worker_config = self.compose_project.compose_file_manager.yml["services"]["worker-name"]

        del self.compose_project.compose_file_manager.yml["services"]["worker-name"]

        workers_expected_service_names = self.get_expected_workers()

        import os

        for worker in workers_expected_service_names:
            worker_config = deepcopy(template_worker_config)

            # setting environments
            worker_config["environment"]["WAIT_FOR"] = str(worker_config["environment"]["WAIT_FOR"]).replace(
                "{worker-name}", worker
            )
            worker_config["environment"]["COMMAND"] = str(worker_config["environment"]["COMMAND"]).replace(
                "{worker-name}", worker
            )
            worker_config["environment"]["USERID"] = os.getuid()
            worker_config["environment"]["USERGROUP"] = os.getgid()

            self.compose_project.compose_file_manager.yml["services"][worker] = worker_config

        self.compose_project.compose_file_manager.set_container_names(get_container_name_prefix(self.bench.name))

        self.compose_project.compose_file_manager.set_version(get_current_fm_version())

        # set network name
        self.compose_project.compose_file_manager.yml["networks"]["site-network"]["name"] = (
            self.bench.name.replace(".", "") + f"-network"
        )
        self.compose_project.compose_file_manager.write_to_file()
