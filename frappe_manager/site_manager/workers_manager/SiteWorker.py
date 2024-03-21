import importlib
import json
from copy import deepcopy

from frappe_manager.site_manager.site_exceptions import SiteWorkerNotStart
from frappe_manager.compose_manager.ComposeFile import ComposeFile
from frappe_manager.display_manager.DisplayManager import richprint
from frappe_manager.utils.helpers import get_container_name_prefix
from frappe_manager.docker_wrapper.DockerClient import DockerClient
from frappe_manager.docker_wrapper.DockerException import DockerException


class SiteWorkers:
    def __init__(self, site_path, site_name, quiet: bool = True):
        self.compose_path = site_path / "docker-compose.workers.yml"
        self.config_dir = site_path / "workspace" / "frappe-bench" / "config"
        self.supervisor_config_path = self.config_dir / "supervisor.conf"
        self.site_name = site_name
        self.quiet = quiet
        self.init()

    def init(self):
        self.composefile = ComposeFile(self.compose_path, template_name="docker-compose.workers.tmpl")
        self.docker = DockerClient(compose_file_path=self.composefile.compose_path)

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
        if not self.composefile.is_template_loaded:
            prev_workers = self.composefile.get_services_list()
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
        self.composefile.yml = self.composefile.load_template()

        template_worker_config = self.composefile.yml["services"]["worker-name"]

        del self.composefile.yml["services"]["worker-name"]

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

                self.composefile.yml["services"][worker] = worker_config

            self.composefile.set_container_names(get_container_name_prefix(self.site_name))

            fm_version = importlib.metadata.version("frappe-manager")
            self.composefile.set_version(fm_version)

            # set network name
            self.composefile.yml["networks"]["site-network"]["name"] = self.site_name.replace(".", "") + f"-network"
            self.composefile.write_to_file()
        else:
            richprint.error("Workers configuration not found.")

    def start(self, force=False):
        status_text = "Starting Workers Containers"
        richprint.change_head(status_text)
        try:
            output = self.docker.compose.up(detach=True, pull="never", force_recreate=force, stream=self.quiet)
            if self.quiet:
                richprint.live_lines(output, padding=(0, 0, 0, 2))
            richprint.print(f"{status_text}: Done")
        except DockerException as e:
            richprint.error(f"{status_text}: Failed Error: {e}")
            raise e

    def stop(self) -> bool:
        """
        The `stop` function stops containers and prints the status of the operation using the `richprint`
        module.
        """
        status_text = "Stopping Workers Containers"
        richprint.change_head(status_text)
        try:
            output = self.docker.compose.stop(timeout=10, stream=self.quiet)
            if self.quiet:
                richprint.live_lines(output, padding=(0, 0, 0, 2))
            richprint.print(f"{status_text}: Done")
        except DockerException as e:
            richprint.warning(f"{status_text}: Failed")

    def get_services_running_status(self) -> dict:
        services = self.composefile.get_services_list()
        containers = self.composefile.get_container_names().values()

        services_status = {}
        try:
            output = self.docker.compose.ps(service=services, format="json", all=True, stream=True)
            status: list = []
            for source, line in output:
                if source == "stdout":
                    current_status = json.loads(line.decode())
                    if type(current_status) == dict:
                        status.append(current_status)
                    else:
                        status += current_status

            # this is done to exclude docker runs using docker compose run command
            for container in status:
                if container["Name"] in containers:
                    services_status[container["Service"]] = container["State"]
            return services_status
        except DockerException as e:
            return {}

    def running(self) -> bool:
        """
        The `running` function checks if all the services defined in a Docker Compose file are running.
        :return: a boolean value. If the number of running containers is greater than or equal to the number
        of services listed in the compose file, it returns True. Otherwise, it returns False.
        """
        services = self.composefile.get_services_list()
        running_status = self.get_services_running_status()

        if running_status:
            for service in services:
                try:
                    if not running_status[service] == "running":
                        return False
                except KeyError:
                    return False
        else:
            return False
        return True
