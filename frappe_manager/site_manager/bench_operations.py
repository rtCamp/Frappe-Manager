from collections.abc import Iterable
import re
from pathlib import Path
from typing import Dict, List, Optional, Tuple
from frappe_manager import CLI_DEFAULT_DELIMETER, STABLE_APP_BRANCH_MAPPING_LIST
from frappe_manager.compose_project.compose_project import ComposeProject
from frappe_manager.docker_wrapper.DockerException import DockerException
from frappe_manager.docker_wrapper.subprocess_output import SubprocessOutput
from frappe_manager.site_manager.site_exceptions import (
    BenchOperationBenchAppInSiteFailed,
    BenchOperationBenchBuildFailed,
    BenchOperationBenchInstallAppInPythonEnvFailed,
    BenchOperationBenchRemoveAppFromPythonEnvFailed,
    BenchOperationBenchSiteCreateFailed,
    BenchOperationException,
    BenchOperationFrappeBranchChangeFailed,
    BenchOperationRequiredDockerImagesNotAvailable,
    BenchOperationWaitForRequiredServiceFailed,
)
from frappe_manager.display_manager.DisplayManager import richprint
from frappe_manager.utils.docker import parameters_to_options
from frappe_manager.utils.site import get_all_docker_images


class BenchOperations:
    def __init__(self, bench) -> None:
        self.bench = bench
        self.bench_cli_cmd = ['/opt/user/.bin/bench_orig']
        self.frappe_bench_dir: Path = self.bench.path / "workspace" / "frappe-bench"

    def create_fm_bench(self):
        richprint.change_head("Configuring common_site_config.json")
        common_site_config_data = self.bench.bench_config.get_commmon_site_config_data(
            self.bench.services.database_manager.database_server_info
        )
        self.bench.set_common_bench_config(common_site_config_data)
        richprint.print("Configured common_site_config.json")

        richprint.change_head("Configuring frappe server")
        self.setup_frappe_server_config()
        richprint.print("Configured frappe server")

        self.setup_supervisor(force=True)

        self.change_frappeverse_prebaked_app_branch(app="frappe", branch=self.bench.bench_config.frappe_branch)

        self.is_required_services_available()

        self.bench_install_apps(self.bench.bench_config.apps_list)

        self.container_run(
            "rm -rf /workspace/frappe-bench/archived",
            BenchOperationException(self.bench.name, "Failed to remove /workspace/frappe-bench/archived directory."),
        )

        richprint.change_head(f"Creating bench site {self.bench.name}")
        self.create_bench_site()
        richprint.print(f"Created bench site {self.bench.name}")

        self.bench_install_apps_site()

        self.bench.set_bench_site_config({'admin_password': self.bench.bench_config.admin_pass})

    def create_bench_site(self):
        new_site_command = self.bench_cli_cmd + ["new-site"]
        new_site_command += ["--db-root-password", self.bench.services.database_manager.database_server_info.password]
        new_site_command += ["--db-name", self.bench.bench_config.db_name]
        new_site_command += ["--db-host", self.bench.services.database_manager.database_server_info.host]
        new_site_command += ["--admin-password", self.bench.bench_config.admin_pass]
        new_site_command += ["--db-port", str(self.bench.services.database_manager.database_server_info.port)]
        new_site_command += ["--verbose", "--mariadb-user-host-login-scope","%"]
        new_site_command += [self.bench.name]

        new_site_command = " ".join(new_site_command)

        self.container_run(new_site_command, raise_exception_obj=BenchOperationBenchSiteCreateFailed(self.bench.name))

        self.container_run(
            " ".join(self.bench_cli_cmd + [f"use {self.bench.name}"]),
            raise_exception_obj=BenchOperationException(
                self.bench.name, f"Failed to set {self.bench.name} as default site."
            ),
        )

        self.container_run(
            " ".join(self.bench_cli_cmd + [f"--site {self.bench.name} scheduler enable"]),
            raise_exception_obj=BenchOperationException(
                self.bench.name, f"Failed to enable {self.bench.name}'s scheduler."
            ),
        )

    def is_required_services_available(self):
        richprint.change_head("Checking if required services are available.")
        required_services = {
            self.bench.services.database_manager.database_server_info.host: self.bench.services.database_manager.database_server_info.port,
            f"{self.bench.bench_config.container_name_prefix}{CLI_DEFAULT_DELIMETER}redis-cache": 6379,
            f"{self.bench.bench_config.container_name_prefix}{CLI_DEFAULT_DELIMETER}redis-queue": 6379,
            f"{self.bench.bench_config.container_name_prefix}{CLI_DEFAULT_DELIMETER}redis-socketio": 6379,
        }
        for service, port in required_services.items():
            output: SubprocessOutput = self.wait_for_required_service(host=service, port=port)
            if output.combined:
                command_output = output.combined[-1].replace('wait-for-it: ', '')
                service_name = command_output.split(' ')[0]
                simplfied_service_name = service_name.split(":")[0]
                simplfied_service_name = simplfied_service_name.split(CLI_DEFAULT_DELIMETER)[-1]
                richprint.print(command_output.replace(service_name, simplfied_service_name), highlight=False)

    def container_run(
        self,
        command: str,
        raise_exception_obj: Optional[BenchOperationException] = None,
        capture_output: bool = False,
        user: str = "frappe",
        workdir="/workspace/frappe-bench",
        service: str = 'frappe',
        compose_project_obj: Optional[ComposeProject] = None,
    ):
        if compose_project_obj:
            compose_project: ComposeProject = compose_project_obj
        else:
            compose_project: ComposeProject = self.bench.compose_project

        try:
            if capture_output:
                output: SubprocessOutput = compose_project.docker.compose.exec(
                    service=service, command=command, user=user, workdir=workdir, stream=not capture_output
                )
                return output
            else:
                output: Iterable[Tuple[str, bytes]] = compose_project.docker.compose.exec(
                    service=service, command=command, workdir=workdir, user=user, stream=not capture_output
                )
                richprint.live_lines(output)

        except DockerException as e:
            if raise_exception_obj:
                raise_exception_obj.set_output(e.output)
                raise raise_exception_obj
            raise e

    def change_frappeverse_prebaked_app_branch(self, app: str, branch: str):
        richprint.change_head(f"Configuring {app} app's branch -> {branch}")

        if not branch == STABLE_APP_BRANCH_MAPPING_LIST[app]:
            richprint.change_head(
                f"Changing prebaked {app} app's branch {STABLE_APP_BRANCH_MAPPING_LIST[app]} -> {self.bench.bench_config.frappe_branch}"
            )
            change_frappe_branch_command = self.bench_cli_cmd + [f"get-app --overwrite --branch {branch} frappe"]
            change_frappe_branch_command = " ".join(change_frappe_branch_command)

            exception = BenchOperationFrappeBranchChangeFailed(
                bench_name=self.bench.name, app=app, branch=self.bench.bench_config.frappe_branch
            )

            self.container_run(command=change_frappe_branch_command, raise_exception_obj=exception)

        richprint.print(f"Configured {app} app's branch -> {self.bench.bench_config.frappe_branch}")

    def setup_supervisor(self, force: bool = False):
        config_dir_path: Path = self.frappe_bench_dir / "config"
        supervisor_conf_path: Path = config_dir_path / "supervisor.conf"

        richprint.change_head("Checking supervisor configuration")
        if not supervisor_conf_path.exists() or force:
            richprint.change_head("Configuring supervisor configs")

            bench_setup_supervisor_command = self.bench_cli_cmd + [
                "setup supervisor --skip-redis --skip-supervisord --yes --user frappe"
            ]

            bench_setup_supervisor_command = " ".join(bench_setup_supervisor_command)
            bench_setup_supervisor_exception = BenchOperationException(
                self.bench.name, "Failed to configure supervisor."
            )
            self.container_run(bench_setup_supervisor_command, bench_setup_supervisor_exception)
            #self.split_supervisor_config(blue_green_deployment=getattr(self.bench.bench_config, 'blue_green_workers', False))
            self.split_supervisor_config(blue_green_deployment=True)
            richprint.print("Configured supervisor configs")

    def split_supervisor_config(self, blue_green_deployment: bool = False):
        import configparser

        supervisor_conf_path: Path = self.frappe_bench_dir / "config" / "supervisor.conf"
        config = configparser.ConfigParser(allow_no_value=True, strict=False, interpolation=None)
        config.read_string(supervisor_conf_path.read_text())

        handle_symlink_frappe_dir = False

        if self.frappe_bench_dir.is_symlink():
            handle_symlink_frappe_dir = True
            symlink_target = str(self.frappe_bench_dir.readlink())
            symlink_name = self.frappe_bench_dir.name

        for section_name in config.sections():
            if "group:" not in section_name:
                # Determine output file name based on original section name
                section_name_delimeter = '-frappe-'
                if '-node-' in section_name:
                    section_name_delimeter = '-node-'

                file_name_prefix = section_name.split(section_name_delimeter)[-1]
                file_name = file_name_prefix + ".fm.supervisor.conf"
                if "worker" in section_name:
                    file_name = file_name_prefix + ".workers.fm.supervisor.conf"

                new_file: Path = supervisor_conf_path.parent / file_name

                section_config = configparser.ConfigParser(interpolation=None)
                section_config.add_section(section_name)
                for key, original_value in config.items(section_name):
                    value = original_value

                    if key == "command":
                        if "frappe-web" in section_name:
                            value = value.replace("127.0.0.1:80", "0.0.0.0:80")

                    if handle_symlink_frappe_dir:
                        if symlink_target in value:
                            value = value.replace(symlink_target, symlink_name)

                    section_config.set(section_name, key, value)

                with open(new_file, "w") as section_file:
                    section_config.write(section_file)

                self.bench.logger.info(f"Split supervisor conf {section_name} => {file_name}")

    def setup_frappe_server_config(self):
        bench_serve_help_output: Optional[SubprocessOutput] = self.container_run(
            " ".join(self.bench_cli_cmd + ["serve --help"]), capture_output=True
        )
        bench_dev_server_script_output = self.container_run("cat /opt/user/bench-dev-server", capture_output=True)
        import re

        if "host" in " ".join(bench_serve_help_output.combined):
            new_bench_dev_server_script = re.sub(
                r"--port \d+", "--host 0.0.0.0 --port 80", " ".join(bench_dev_server_script_output.combined)
            )
        else:
            new_bench_dev_server_script = re.sub(
                r"--port \d+", "--port 80", " ".join(bench_dev_server_script_output.combined)
            )

        self.container_run(f'echo "{new_bench_dev_server_script}" > /opt/user/bench-dev-server.sh')
        self.container_run("chmod +x /opt/user/bench-dev-server.sh", user='root')

    def bench_install_apps(self, apps_lists, already_installed_apps: Dict = STABLE_APP_BRANCH_MAPPING_LIST):
        to_install_apps = [x["app"] for x in apps_lists]

        for app, branch in already_installed_apps.items():
            if app == 'frappe':
                continue

            if app not in to_install_apps:
                richprint.change_head(f"Removing prebaked app {app} from python env.")
                self.bench_rm_app_env(app)
                richprint.print(f"Removed prebaked app {app}")

        for app_info in apps_lists:
            app = app_info["app"]
            branch = app_info["branch"]

            status_txt = f"Building and Installing app {app} in env."

            if branch:
                status_txt = f"Building and Installing app {app} -> {branch}."

            richprint.change_head(status_txt)

            if app in already_installed_apps.keys():
                if already_installed_apps[app] == branch:
                    richprint.print(f"Skipped installation of prebaked app [blue]{app} -> {branch}[/blue].")
                    continue

                if not branch:
                    branch = already_installed_apps[app]

            self.bench_install_app_env(app, branch)

            richprint.print(f"Builded and Installed app [blue]{app}{' -> ' + branch if branch else ''}[/blue] in env.")

    def get_current_apps_list(self):
        """Return apps which are available in apps directory"""

        apps_dir = self.frappe_bench_dir / 'apps'
        apps_dirs: List[Path] = [item for item in apps_dir.iterdir() if item.is_dir()]
        return apps_dirs

    def bench_install_apps_site(self):
        for app in self.get_current_apps_list():
            richprint.change_head(f"Installing app {app.name} in site.")
            self.bench_install_app_site(app.name)
            richprint.print(f"Installed app {app.name} in site.")

    def bench_build(self, app_list: Optional[List[str]] = None):
        build_cmd = self.bench_cli_cmd + ["build"]

        if app_list is not None:
            for app in app_list:
                build_cmd += ["--app"] + [app]

        build_exception = BenchOperationBenchBuildFailed(bench_name=self.bench.name, apps=app_list)

        build_cmd = " ".join(build_cmd)
        self.container_run(build_cmd, build_exception)

    def bench_install_app_env(
        self, app: str, branch: Optional[str] = None, overwrite: bool = True, skip_assets: bool = False
    ):
        parameters: Dict = locals()

        remove_parameters = ["app"]
        app_install_env_command = self.bench_cli_cmd + ["get-app"]
        app_install_env_command += parameters_to_options(parameters, exclude=remove_parameters)
        app_install_env_command += [app]

        app_install_env_command = " ".join(app_install_env_command)
        app_install_exception = BenchOperationBenchInstallAppInPythonEnvFailed(bench_name=self.bench.name, app_name=app)

        self.container_run(
            app_install_env_command,
            raise_exception_obj=app_install_exception,
        )

    def bench_rm_app_env(self, app: str, no_backup: bool = True, force: bool = True):
        parameters: dict = locals()

        remove_parameters = ["app"]
        app_rm_env_command = self.bench_cli_cmd + ["rm"]
        app_rm_env_command += parameters_to_options(parameters, exclude=remove_parameters)
        app_rm_env_command += [app]

        app_rm_env_command = " ".join(app_rm_env_command)

        self.container_run(
            app_rm_env_command,
            raise_exception_obj=BenchOperationBenchRemoveAppFromPythonEnvFailed(
                bench_name=self.bench.name, app_name=app
            ),
        )

    def bench_install_app_site(self, app: str):
        app_install_site_command = self.bench_cli_cmd + ["--site", self.bench.name]
        app_install_site_command += ["install-app", app]
        app_install_site_command = " ".join(app_install_site_command)

        self.container_run(
            app_install_site_command,
            raise_exception_obj=BenchOperationBenchAppInSiteFailed(bench_name=self.bench.name, app_name=app),
        )

    def is_bench_site_exists(self, bench_site_name: str):
        site_path: Path = self.frappe_bench_dir / "sites" / bench_site_name
        return site_path.exists()

    def wait_for_required_service(self, host: str, port: int, timeout: int = 120):
        return self.container_run(
            f"wait-for-it -t {timeout} {host}:{port}",
            raise_exception_obj=BenchOperationWaitForRequiredServiceFailed(
                bench_name=self.bench.name, host=host, port=port, timeout=timeout
            ),
            capture_output=True,
        )

    def check_required_docker_images_available(self):
        richprint.change_head("Checking required docker images availability")
        fm_images = get_all_docker_images()
        system_available_images = self.bench.compose_project.docker.images()

        not_available_images = []

        for key, value in fm_images.items():
            name = value['name']
            tag = value['tag']

            found = False

            for item in system_available_images:
                if item.get('Repository') == name and item.get('Tag') == tag:
                    found = True
                    break

            if not found:
                image = f"{name}:{tag}"
                not_available_images.append(image)

        # remove duplicates
        not_available_images = list(dict.fromkeys(not_available_images))

        if not_available_images:
            for image in not_available_images:
                richprint.error(f"Docker image '{image}' is not available locally")
            raise BenchOperationRequiredDockerImagesNotAvailable(self.bench.name, 'fm self update images')

    def reset_bench_site(self, admin_password: str):
        global_db_info = self.bench.services.database_manager.database_server_info
        reset_bench_site_command = self.bench_cli_cmd + ["--site", self.bench.name]
        reset_bench_site_command += ['reinstall', '--admin-password', admin_password]
        reset_bench_site_command += ['--db-root-username', global_db_info.user]
        reset_bench_site_command += ['--db-root-password', global_db_info.password]
        reset_bench_site_command += ['--yes']

        reset_bench_site_command = " ".join(reset_bench_site_command)

        self.container_run(
            reset_bench_site_command,
            raise_exception_obj=BenchOperationException(
                bench_name=self.bench.name, message=f'Failed to reset bench site {self.bench.name}.'
            ),
        )
