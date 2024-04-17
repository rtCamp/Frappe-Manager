import importlib
from copy import deepcopy
from pathlib import Path
from frappe_manager.compose_manager.ComposeFile import ComposeFile
from frappe_manager.compose_project.compose_project import ComposeProject
from frappe_manager.display_manager.DisplayManager import richprint
from frappe_manager.utils.helpers import get_container_name_prefix
from frappe_manager.docker_wrapper.DockerException import DockerException


class BenchWorkers:
    def __init__(self, site_name: str, site_path: Path, verbose: bool = True):
        self.compose_path = site_path / "docker-compose.workers.yml"
        self.config_dir = site_path / "workspace" / "frappe-bench" / "config"
        self.supervisor_config_path = self.config_dir / "supervisor.conf"
        self.site_name = site_name
        self.quiet = not verbose
        self.compose_project = ComposeProject(ComposeFile(self.compose_path,template_name='docker-compose.workers.tmpl'))

    def exists(self):
        return self.compose_path.exists()

    def get_expected_workers(self) -> list[str]:
        richprint.change_head("Getting Workers info")

        workers_supervisor_conf_paths = []

        for file_path in self.config_dir.iterdir():
            file_path_abs = str(file_path.absolute())
            if file_path.is_file():
                if file_path_abs.endswith(".workers.fm.supervisor.conf"):
                    workers_supervisor_conf_paths.append(file_path)

        workers_expected_service_names = []

        for worker_name in workers_supervisor_conf_paths:
            worker_name = worker_name.name
            worker_name = worker_name.replace("frappe-bench-frappe-", "")
            worker_name = worker_name.replace(".workers.fm.supervisor.conf", "")
            workers_expected_service_names.append(worker_name)

        workers_expected_service_names.sort()

        richprint.print("Getting Workers info: Done")

        return workers_expected_service_names

    def is_expected_worker_same_as_template(self) -> bool:
        if not self.compose_project.compose_file_manager.is_template_loaded:
            prev_workers = self.compose_project.compose_file_manager.get_services_list()
            prev_workers.sort()
            expected_workers = self.get_expected_workers()
            return prev_workers == expected_workers
        else:
            return False

    def generate_compose(self):
        richprint.change_head("Generating Workers configuration")

        if not self.compose_path.exists():
            richprint.print("Workers compose not present. Generating...")
        else:
            richprint.print("Workers configuration changed. Recreating compose...")

        # create compose file for workers
        self.compose_project.compose_file_manager.yml = self.compose_project.compose_file_manager.load_template()

        template_worker_config = self.compose_project.compose_file_manager.yml["services"]["worker-name"]

        del self.compose_project.compose_file_manager.yml["services"]["worker-name"]

        workers_expected_service_names = self.get_expected_workers()

        if len(workers_expected_service_names) > 0:
            import os

            for worker in workers_expected_service_names:
                worker_config = deepcopy(template_worker_config)

                # setting environments
                worker_config["environment"]["WAIT_FOR"] = str(worker_config["environment"]["WAIT_FOR"]).replace("{worker-name}", worker)
                worker_config["environment"]["COMMAND"] = str(worker_config["environment"]["COMMAND"]).replace("{worker-name}", worker)
                worker_config["environment"]["USERID"] = os.getuid()
                worker_config["environment"]["USERGROUP"] = os.getgid()

                self.compose_project.compose_file_manager.yml["services"][worker] = worker_config

            self.compose_project.compose_file_manager.set_container_names(get_container_name_prefix(self.site_name))

            fm_version = importlib.metadata.version("frappe-manager")
            self.compose_project.compose_file_manager.set_version(fm_version)

            # set network name
            self.compose_project.compose_file_manager.yml["networks"]["site-network"]["name"] = self.site_name.replace(".", "") + f"-network"
            self.compose_project.compose_file_manager.write_to_file()
        else:
            richprint.error("Workers configuration not found.")

    def stop(self) -> bool:
        """
        The `stop` function stops containers and prints the status of the operation using the `richprint`
        module.
        """
        status_text = "Stopping Workers Containers"
        richprint.change_head(status_text)
        try:
            output = self.compose_project.docker.compose.stop(timeout=10, stream=self.quiet)
            if self.quiet:
                richprint.live_lines(output, padding=(0, 0, 0, 2))
            richprint.print(f"{status_text}: Done")
        except DockerException as e:
            richprint.warning(f"{status_text}: Failed")
