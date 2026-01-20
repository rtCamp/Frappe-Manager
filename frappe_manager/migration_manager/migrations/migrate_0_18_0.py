import os
import re
from pathlib import Path

from frappe_manager.display_manager.DisplayManager import richprint
from frappe_manager.docker import DockerClient
from frappe_manager.migration_manager.backup_manager import BackupManager
from frappe_manager.migration_manager.migration_base import MigrationBase
from frappe_manager.migration_manager.migration_exections import (
    MigrationExceptionInBench,
)
from frappe_manager.migration_manager.migration_helpers import (
    MigrationBench,
    MigrationBenches,
    MigrationServicesManager,
)
from frappe_manager.migration_manager.version import Version
from frappe_manager.utils.docker import host_run_cp


def get_container_name_prefix(site_name):
    return 'fm' + "__" + site_name.replace(".", "_")

class MigrationV0180(MigrationBase):
    version = Version("0.18.0")

    def init(self):
        self.cli_dir: Path = Path.home() / "frappe"
        self.benches_dir = self.cli_dir / "sites"
        self.backup_manager = BackupManager(name=str(self.version), benches_dir=self.benches_dir)
        self.benches_manager = MigrationBenches(self.benches_dir)
        self.services_manager: MigrationServicesManager = MigrationServicesManager(
            services_path=self.cli_dir / "services"
        )
        self.pulled_images_list = []

    def migrate_bench(self, bench: MigrationBench):
        bench.compose_project.down_service(volumes=True)

        richprint.change_head("Migrating bench compose")
        
        if bench.compose_project.compose_file_manager.yml.get('services',{}).get('redis-socketio', None):
            del bench.compose_project.compose_file_manager.yml['services']['redis-socketio']
        if bench.compose_project.compose_file_manager.yml.get('volumes',{}).get('redis-socketio-data', None):
            del bench.compose_project.compose_file_manager.yml['volumes']['redis-socketio-data']

        tag_updates = {
            'frappe': self.version.version_string(),
            'socketio': self.version.version_string(),
            'schedule': self.version.version_string(),
            'nginx': self.version.version_string(),
            'redis-cache': '8-alpine',
            'redis-queue': '8-alpine',
        }

        # Pull required images
        for image_name, tag in [
            ('frappe', self.version.version_string()),
            ('nginx', self.version.version_string()),
            ('redis-cache', '8-alpine'),
            ('ghcr.io/rtcamp/frappe-manager-prebake', self.version.version_string()),
        ]:
            if image_name in ['frappe', 'nginx']:
                images_info = bench.compose_project.compose_file_manager.get_all_images()
                full_image_name = images_info.get(image_name, {}).get('name', image_name)
            else:
                full_image_name = image_name
            
            pull_image = f"{full_image_name}:{tag}"
            if pull_image not in self.pulled_images_list:
                richprint.change_head(f"Pulling Image {pull_image}")
                output = DockerClient().pull(container_name=pull_image, stream=True)
                richprint.live_lines(output, padding=(0, 0, 0, 2))
                richprint.print(f"Image pulled [blue]{pull_image}[/blue]")
                self.pulled_images_list.append(pull_image)

        # Use new migrate_images method to update all tags and version atomically
        bench.compose_project.compose_file_manager.migrate_images(
            tag_updates=tag_updates,
            new_version=str(self.version)
        )

        self.migrate_workers_compose(bench)
        self.migrate_pyenv_and_nvm(bench)

    def migrate_pyenv_and_nvm(self, bench: MigrationBench):

        richprint.change_head("Migrating nginx config")

        nginx_default_conf = bench.path / "configs/nginx/conf/conf.d/default.conf"

        if nginx_default_conf.exists():
            nginx_default_conf.unlink()

        output = bench.compose_project.docker.compose.run(
            service="nginx",
            command="-c 'jinja2 -D SITENAME=$SITENAME /config/template.conf > /etc/nginx/conf.d/default.conf'",
            rm=True,
            entrypoint="/bin/bash",
            stream=True,
        )
        richprint.live_lines(output, padding=(0, 0, 0, 2))
        richprint.print(f"Migrated nginx config")


        frappe_prebake_image = f'ghcr.io/rtcamp/frappe-manager-prebake:{self.version.version_string()}'
        frappe_image = f'ghcr.io/rtcamp/frappe-manager-frappe:{self.version.version_string()}'

        zshrc_path = bench.path / "workspace" / ".zshrc"
        zshrc_bak_path = bench.path / "workspace" / ".bak.zshrc"

        ohmyzsh_path = bench.path / "workspace" / ".oh-my-zsh"

        if not ohmyzsh_path.exists():
            richprint.change_head(f"Migrating .oh-my-zsh")
            host_run_cp(
                docker=bench.compose_project.docker,
                image=frappe_image,
                source="/workspace/.oh-my-zsh",
                destination=str(Path(bench.path / "workspace").absolute()),
            )

        zshrc_path.rename(zshrc_bak_path)
        filtered_lines = []
        for line in zshrc_bak_path.read_text().splitlines():
            if not (
                "PYENV_ROOT" in line
                or "pyenv init" in line
                or "NVM_DIR" in line
            ):
                filtered_lines.append(line)
        zshrc_path.write_text("\n".join(filtered_lines) + "\n")
        richprint.print(f"Migrated .zshrc")

        if not bench.compose_project.compose_file_manager.exists():
            richprint.error(f"Failed to migrate {bench.name} compose file.")
            raise MigrationExceptionInBench(f"{bench.compose_project.compose_file_manager.compose_path} not found.")

        # /workspace/.pyenv
        richprint.change_head(f"Configuring Pyenv at /workspace/.pyenv")
        host_run_cp(
            docker=bench.compose_project.docker,
            image=frappe_prebake_image,
            source="/workspace/.pyenv",
            destination=str(Path(bench.path / "workspace").absolute()),
        )
        richprint.print(f"Configured Pyenv at /workspace/.pyenv")

        richprint.change_head(f"Configuring nvm at /workspace/.nvm")
        host_run_cp(
            docker=bench.compose_project.docker,
            image=frappe_prebake_image,
            source="/workspace/.nvm",
            destination=str(Path(bench.path / "workspace").absolute()),
        )
        richprint.print(f"Configured nvm at /workspace/.nvm")

        pyvenv_cfg = bench.path / "workspace" / "frappe-bench" / "env" / "pyvenv.cfg"
        version_info = None

        if pyvenv_cfg.exists():
            with pyvenv_cfg.open("r") as f:
                for line in f:
                    if line.startswith("version"):
                        version_info = line.split("=", 1)[1].strip()
                        break

        if version_info:
            richprint.change_head(f"Migrating bench environment python v{version_info}")
            output = bench.compose_project.docker.compose.run(
                service="frappe",
                command=f'-c "source /etc/bash.bashrc; pyenv install -s {version_info}; pyenv global {version_info};"',
                rm=True,
                entrypoint="/bin/bash",
                stream=True,
                user="frappe",
            )
            richprint.live_lines(output, padding=(0, 0, 0, 2))
            output = bench.compose_project.docker.compose.run(
                service="frappe",
                command=f"-c 'source /etc/bash.bashrc; set -x; cd /workspace/frappe-bench; mv env env.bak; uv venv env --seed --link-mode=copy; for app in $(ls -1 apps); do uv pip install --python /workspace/frappe-bench/env/bin/python -U -e apps/$app || continue; done'",
                rm=True,
                entrypoint="/bin/bash",
                stream=True,
                user="frappe",
            )
            richprint.live_lines(output, padding=(0, 0, 0, 2))
            richprint.print(f"Migrated bench environment python v{version_info}")
        self.split_supervisor_config(bench)

    def migrate_workers_compose(self, bench: MigrationBench):
        if bench.workers_compose_project.compose_file_manager.compose_path.exists():
            richprint.change_head("Migrating workers compose")
            
            # Get all worker service names to update their tags
            workers_image_info = bench.workers_compose_project.compose_file_manager.get_all_images()
            tag_updates = {worker: self.version.version_string() for worker in workers_image_info.keys()}
            
            # Use migrate_images to update all worker tags and version atomically
            bench.workers_compose_project.compose_file_manager.migrate_images(
                tag_updates=tag_updates,
                new_version=str(self.version)
            )

            richprint.print(f"Migrated [blue]{bench.name}[/blue] workers compose file.")


    def split_supervisor_config(self, bench: MigrationBench):
        frappe_bench_dir = bench.path / 'workspace' / 'frappe-bench'
        supervisor_conf_path: Path = frappe_bench_dir / "config" / "supervisor.conf"
        richprint.change_head("Migrating supervisor configs")

        bench_setup_supervisor_command = "bench setup supervisor --skip-redis --skip-supervisord --yes --user frappe"

        output = bench.compose_project.docker.compose.run(
            service="frappe",
            command=f"-c 'source /etc/bash.bashrc; cd /workspace/frappe-bench; {bench_setup_supervisor_command}'",
            rm=True,
            entrypoint="/bin/bash",
            stream=True,
            user="frappe",
        )

        richprint.live_lines(output, padding=(0, 0, 0, 2))

        import configparser
        config = configparser.ConfigParser(allow_no_value=True, strict=False, interpolation=None)
        config.read_string(supervisor_conf_path.read_text())

        handle_symlink_frappe_dir = False

        if frappe_bench_dir.is_symlink():
            handle_symlink_frappe_dir = True

        for section_name in config.sections():
            if "group:" not in section_name:
                section_config = configparser.ConfigParser(interpolation=None)
                section_config.add_section(section_name)
                for key, value in config.items(section_name):
                    if handle_symlink_frappe_dir:
                        to_replace = str(frappe_bench_dir.readlink())

                        if to_replace in value:
                            value = value.replace(to_replace, frappe_bench_dir.name)

                    if "frappe-web" in section_name:
                        if key == "command":
                            value = value.replace("127.0.0.1:80", "0.0.0.0:80")
                            cpu_count = os.cpu_count() or 2
                            workers = (cpu_count * 2) + 1
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

                richprint.print(f"Migrated supervisor conf {section_name} => {file_name}")
