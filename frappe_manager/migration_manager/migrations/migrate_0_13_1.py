import json
from frappe_manager.migration_manager.migration_base import MigrationBase
from frappe_manager.migration_manager.migration_exections import MigrationExceptionInBench
from frappe_manager.migration_manager.migration_helpers import (
    MigrationBench,
    MigrationBenches,
)
from frappe_manager.display_manager.DisplayManager import richprint
from frappe_manager.migration_manager.version import Version
from frappe_manager import CLI_DIR
from frappe_manager.utils.helpers import capture_and_format_exception, get_container_name_prefix
from frappe_manager.docker_wrapper.DockerClient import DockerClient


class MigrationV0131(MigrationBase):
    version = Version("0.13.1")

    def __init__(self):
        super().init()
        self.benches_dir = CLI_DIR / "sites"
        self.benches_manager = MigrationBenches(self.benches_dir)

    def up(self):
        richprint.print(f"Started", prefix=f"[bold]v{str(self.version)}:[/bold] ")
        self.logger.info("-" * 40)

        self.benches_manager.stop_benches()

        benches = self.benches_manager.get_all_benches()

        # migrate each bench
        main_error = False

        # migrate each bench
        for bench_name, bench_path in benches.items():
            bench = MigrationBench(name=bench_name, path=bench_path.parent)

            if bench.name in self.migration_executor.migrate_benches.keys():
                bench_info = self.migration_executor.migrate_benches[bench.name]
                if bench_info['exception']:
                    richprint.print(f"Skipping migration for failed bench{bench.name}.")
                    main_error = True
                    continue

            self.migration_executor.set_bench_data(bench, migration_version=self.version)
            try:
                self.migrate_bench(bench)
            except Exception as e:
                exception_str = capture_and_format_exception()
                self.logger.error(f"{bench.name} [ EXCEPTION TRACEBACK ]:\n {exception_str}")
                richprint.update_live()
                main_error = True
                self.migration_executor.set_bench_data(bench, e, self.version)
                self.undo_bench_migrate(bench)
                bench.compose_project.down_service(volumes=False, timeout=5)

        if main_error:
            raise MigrationExceptionInBench('')

        richprint.print(f"Successfull", prefix=f"[bold]v{str(self.version)}:[/bold] ")
        self.logger.info("-" * 40)

    def migrate_bench(self, bench: MigrationBench):
        richprint.print(f"Migrating bench {bench.name}", prefix=f"[bold]v{str(self.version)}:[/bold] ")

        # backup docker compose.yml
        self.backup_manager.backup(bench.path / "docker-compose.yml", bench_name=bench.name)

        # backup common_site_config.json
        bench_common_site_config = bench.path / "workspace" / "frappe-bench" / "sites" / "common_site_config.json"
        self.backup_manager.backup(bench_common_site_config, bench_name=bench.name)
        common_site_config_json = json.loads(bench_common_site_config.read_bytes())

        if 'mail_server' in common_site_config_json:
            common_site_config_json['mail_server'] = get_container_name_prefix(bench.name)
            bench_common_site_config.write_text(json.dumps(common_site_config_json))

        bench.compose_project.down_service(volumes=False)
        self.migrate_bench_compose(bench)

    def down(self):
        # richprint.print(f"Started",prefix=f"[ Migration v{str(self.version)} ][ROLLBACK] : ")
        richprint.print(f"Started", prefix=f"[bold]v{str(self.version)} [ROLLBACK]:[/bold] ")
        self.logger.info("-" * 40)

        # undo each bench
        for bench, exception in self.migration_executor.migrate_benches.items():
            if not exception:
                self.undo_bench_migrate(bench)

        for backup in self.backup_manager.backups:
            self.backup_manager.restore(backup, force=True)

        richprint.print(f"Successfull", prefix=f"[bold]v{str(self.version)} [ROLLBACK]:[/bold] ")
        self.logger.info("-" * 40)

    def undo_bench_migrate(self, bench: MigrationBench):
        for backup in self.backup_manager.backups:
            if backup.bench == bench.name:
                self.backup_manager.restore(backup, force=True)

        self.logger.info(f"Undo successfull for bench: {bench.name}")

    def migrate_bench_compose(self, bench: MigrationBench):
        status_msg = "Migrating bench compose"
        richprint.change_head(status_msg)

        compose_version = bench.compose_project.compose_file_manager.get_version()

        if not bench.compose_project.compose_file_manager.exists():
            richprint.print(f"{status_msg} {compose_version} -> {self.version.version}: Failed ")
            raise MigrationExceptionInBench(f"{bench.compose_project.compose_file_manager.compose_path} not found.")

        # generate bench config for bench and save it
        status_msg = "Migrating bench compose"
        richprint.change_head(status_msg)

        images_info = bench.compose_project.compose_file_manager.get_all_images()

        # change image frappe, socketio, schedule
        nginx_image_info = images_info['nginx']
        nginx_image_info['tag'] = self.version.version_string()

        images_info['nginx'] = nginx_image_info

        pull_image = f"{nginx_image_info['name']}:{nginx_image_info['tag']}"
        richprint.change_head(f"Pulling Image {pull_image}")
        output = DockerClient().pull(container_name=pull_image, stream=True)
        richprint.live_lines(output, padding=(0, 0, 0, 2))
        richprint.print(f"Image pulled [blue]{pull_image}[/blue]")

        bench.compose_project.compose_file_manager.set_all_images(images_info)
        bench.compose_project.compose_file_manager.set_version(str(self.version))
        bench.compose_project.compose_file_manager.write_to_file()

        richprint.print(f"{status_msg} {compose_version} -> {self.version.version}: Done")
