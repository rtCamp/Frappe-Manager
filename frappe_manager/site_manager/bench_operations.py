from collections.abc import Iterable
import os
import re
from pathlib import Path
from typing import Dict, List, Optional, Tuple
from frappe_manager import CLI_DEFAULT_DELIMETER, STABLE_APP_BRANCH_MAPPING_LIST
from frappe_manager.compose_project.compose_project import ComposeProject
from frappe_manager.docker_wrapper.DockerException import DockerException
from frappe_manager.docker_wrapper.subprocess_output import SubprocessOutput
from frappe_manager.site_manager.bench_config import FMBenchEnvType
from frappe_manager.site_manager.site import Site
from frappe_manager.site_manager.site_exceptions import (
    BenchFrappeServiceSupervisorNotRunning,
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
        self.bench_cli_cmd = ["/usr/local/bin/bench"]
        self.frappe_bench_dir: Path = self.bench.path / "workspace" / "frappe-bench"

    def init_bench(self):
        """Initialize bench without creating sites"""
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

    def create_bench_site(self, site: Site):
        """Create a new site in the bench"""
        new_site_command = self.bench_cli_cmd + ["new-site"]
        new_site_command += ["--db-root-password", self.bench.services.database_manager.database_server_info.password]
        new_site_command += ["--db-name", site.get_expected_db_name()]
        new_site_command += ["--db-host", self.bench.services.database_manager.database_server_info.host]
        new_site_command += ["--admin-password", self.bench.bench_config.admin_pass]
        new_site_command += ["--db-port", str(self.bench.services.database_manager.database_server_info.port)]
        new_site_command += ["--verbose", "--mariadb-user-host-login-scope","%"]
        new_site_command += [site.name]

        new_site_command = " ".join(new_site_command)

        self.container_run(new_site_command, raise_exception_obj=BenchOperationBenchSiteCreateFailed(site.name))

        self.container_run(
            " ".join(self.bench_cli_cmd + [f"use {site.name}"]),
            raise_exception_obj=BenchOperationException(
                site.name, f"Failed to set {site.name} as default site."
            ),
        )

        self.container_run(
            " ".join(self.bench_cli_cmd + [f"--site {site.name} scheduler enable"]),
            raise_exception_obj=BenchOperationException(
                site.name, f"Failed to enable {site.name}'s scheduler."
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
            self.split_supervisor_config()
            richprint.print("Configured supervisor configs")

    def switch_bench_env(self, timeout: int = 30, interval: int = 1):
        """Switch bench environment between dev and prod modes"""
        if not self.is_supervisord_running():
            raise BenchFrappeServiceSupervisorNotRunning(self.bench.name)

        socket_path = f"/fm-sockets/frappe.sock"

        # Wait for supervisor socket file to be created in container
        for _ in range(timeout):
            try:
                self.container_run(f"test -e {socket_path}", raise_exception_obj=BenchOperationException(
                    self.bench.name,
                    message=f'Failed to check supervisor socket exists'
                ))
                break
            except DockerException as e:
                print('--->')
                print(e)
                time.sleep(interval)
        else:
            raise BenchOperationException(
                self.bench.name,
                message=f'Supervisor socket for frappe service not created after {timeout} seconds'
            )

        supervisorctl_command = f"supervisorctl -s unix:///{socket_path} "

        if self.bench.bench_config.environment_type == FMBenchEnvType.dev:
            richprint.change_head(f"Configuring and starting {self.bench.bench_config.environment_type.value} services")

            stop_command = supervisorctl_command + "stop all"
            self.container_run(stop_command)

            unlink_command = 'rm -rf /opt/user/conf.d/web.fm.supervisor.conf'
            self.container_run(unlink_command)

            link_command = 'ln -sfn /opt/user/frappe-dev.conf /opt/user/conf.d/frappe-dev.conf'
            self.container_run(link_command)

            reread_command = supervisorctl_command + "reread"
            self.container_run(reread_command)

            update_command = supervisorctl_command + "update"
            self.container_run(update_command)

            start_command = supervisorctl_command + "start all"
            self.container_run(start_command)

            richprint.print(f"Configured and Started {self.bench.bench_config.environment_type.value} services.")

        elif self.bench.bench_config.environment_type == FMBenchEnvType.prod:
            richprint.change_head(f"Configuring and starting {self.bench.bench_config.environment_type.value} services")

            stop_command = supervisorctl_command + "stop all"
            self.container_run(stop_command)

            unlink_command = 'rm -rf /opt/user/conf.d/frappe-dev.conf'
            self.container_run(unlink_command)

            link_command = (
                'ln -sfn /workspace/frappe-bench/config/web.fm.supervisor.conf /opt/user/conf.d/web.fm.supervisor.conf'
            )
            self.container_run(link_command)

            reread_command = supervisorctl_command + "reread"
            self.container_run(reread_command)

            update_command = supervisorctl_command + "update"
            self.container_run(update_command)

            start_command = supervisorctl_command + "start all"
            self.container_run(start_command)

            richprint.print(f"Configured and Started {self.bench.bench_config.environment_type.value} services.")

    def split_supervisor_config(self):
        import configparser

        supervisor_conf_path: Path = self.frappe_bench_dir / "config" / "supervisor.conf"
        config = configparser.ConfigParser(allow_no_value=True, strict=False, interpolation=None)
        config.read_string(supervisor_conf_path.read_text())

        handle_symlink_frappe_dir = False

        if self.frappe_bench_dir.is_symlink():
            handle_symlink_frappe_dir = True

        for section_name in config.sections():
            if "group:" not in section_name:
                section_config = configparser.ConfigParser(interpolation=None)
                section_config.add_section(section_name)
                for key, value in config.items(section_name):
                    if handle_symlink_frappe_dir:
                        to_replace = str(self.frappe_bench_dir.readlink())

                        if to_replace in value:
                            value = value.replace(to_replace, self.frappe_bench_dir.name)

                    if "frappe-web" in section_name:
                        if key == "command":
                            # Replace localhost binding with all interfaces
                            value = value.replace("127.0.0.1:80", "0.0.0.0:80")
                            
                            # Calculate optimal workers based on CPU count
                            workers = (os.cpu_count() * 2) + 1
                            
                            # Replace worker count using regex
                            value = re.sub(r'-w\s+\d+', f'-w {workers}', value)
                            
                    section_config.set(section_name, key, value)

                section_name_delimeter = '-frappe-'

                if '-node-' in section_name:
                    section_name_delimeter = '-node-'

                file_name_prefix = section_name.split(section_name_delimeter)

                file_name_prefix = file_name_prefix[-1]
                file_name = file_name_prefix + ".fm.supervisor.conf"

                if "worker" in section_name:
                    file_name = file_name_prefix + ".workers.fm.supervisor.conf"

                new_file: Path = supervisor_conf_path.parent / file_name

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

    def bench_install_apps_site(self, site: Site):
        """Install all apps for a given site"""
        for app in self.get_current_apps_list():
            richprint.change_head(f"Installing app {app.name} in site {site.name}")
            self.bench_install_app_site(site, app.name)
            richprint.print(f"Installed app {app.name} in site {site.name}")

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

    def bench_install_app_site(self, site: Site, app: str):
        """Install an app for a specific site"""

        app_install_site_command = self.bench_cli_cmd + ["--site", site.name]
        app_install_site_command += ["install-app", app]
        app_install_site_command = " ".join(app_install_site_command)

        self.container_run(
            app_install_site_command,
            raise_exception_obj=BenchOperationBenchAppInSiteFailed(site.name, app_name=app),
        )

    def is_bench_site_exists(self, site: Site) -> bool:
        """Check if a site exists in the bench"""
        return site.exists

    def wait_for_required_service(self, host: str, port: int, timeout: int = 120):
        return self.container_run(
            f"wait-for-it -t {timeout} {host}:{port}",
            raise_exception_obj=BenchOperationWaitForRequiredServiceFailed(
                bench_name=self.bench.name, host=host, port=port, timeout=timeout
            ),
            capture_output=True,
        )

    def is_supervisord_running(self, interval: int = 2, timeout: int = 30) -> bool:
        """
        Check if supervisord is running in the frappe service container
        
        Args:
            interval: Time to wait between retries
            timeout: Total time to wait before giving up
            
        Returns:
            bool: True if supervisord is running, False otherwise
        """
        for _ in range(timeout):
            try:
                status_command = 'supervisorctl -c /opt/user/supervisord.conf status all'
                output = self.bench.compose_project.docker.compose.exec(
                    'frappe', 
                    status_command, 
                    user='frappe', 
                    stream=False
                )
                return True
            except DockerException as e:
                if any('frappe-bench' in s for s in e.output.combined):
                    return True
                time.sleep(interval)
                continue
        return False

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
            raise BenchOperationRequiredDockerImagesNotAvailable(self.bench.name, 'fm self update-images')

    def reset_bench_site(self, site: Site, admin_password: str):
        """Reset a specific site"""

        global_db_info = self.bench.services.database_manager.database_server_info

        reset_bench_site_command = self.bench_cli_cmd + ["--site", site.name]
        reset_bench_site_command += ['reinstall', '--admin-password', admin_password]
        reset_bench_site_command += ['--db-root-username', global_db_info.user]
        reset_bench_site_command += ['--db-root-password', global_db_info.password]
        reset_bench_site_command += ['--yes']

        reset_bench_site_command = " ".join(reset_bench_site_command)

        self.container_run(
            reset_bench_site_command,
            raise_exception_obj=BenchOperationException(
                site.name, message=f'Failed to reset site {site.name}.'
            ),
        )
