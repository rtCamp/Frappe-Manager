from frappe_manager.migration_manager.migration_base import MigrationBase
from frappe_manager.migration_manager.migration_exections import MigrationExceptionInBench
from frappe_manager.migration_manager.migration_helpers import MigrationBench, MigrationBenches
from frappe_manager.display_manager.DisplayManager import richprint
from frappe_manager.migration_manager.version import Version
from frappe_manager import CLI_DIR, CLI_SERVICES_DIRECTORY

class MigrationV0110(MigrationBase):
    version = Version("0.11.0")

    def __init__(self):
        super().init()
        self.benches_dir = CLI_DIR / "sites"
        self.services_path = CLI_SERVICES_DIRECTORY
        self.benches_manager = MigrationBenches(self.benches_dir)

    def up(self):
        richprint.print(f"Started", prefix=f"[bold]v{str(self.version)}:[/bold] ")
        self.logger.info("-" * 40)

        benches = self.benches_manager.get_all_benches()

        main_error = False

        # migrate each bench
        for bench_name, bench_path in benches.items():
            bench = MigrationBench(name=bench_name, path=bench_path.parent)

            if bench.name in self.migration_executor.migrate_benches.keys():
                bench_info =  self.migration_executor.migrate_benches[bench.name]
                if bench_info['exception']:
                    richprint.print(f"Skipping migration for failed bench{bench.name}.")
                    main_error = True
                    continue

            self.migration_executor.set_bench_data(bench,migration_version=self.version)
            try:
                self.migrate_bench(bench)
            except Exception as e:
                import traceback
                traceback_str = traceback.format_exc()
                self.logger.error(f"{bench.name} [ EXCEPTION TRACEBACK ]:\n {traceback_str}")
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
        self.backup_manager.backup(
            bench.path
            / "workspace"
            / "frappe-bench"
            / "sites"
            / "common_site_config.json",
            bench_name=bench.name,
        )

        bench.compose_project.down_service(volumes=False)
        self.migrate_bench_compose(bench)

    def down(self):
        # richprint.print(f"Started",prefix=f"[ Migration v{str(self.version)} ][ROLLBACK] : ")
        richprint.print(
            f"Started", prefix=f"[bold]v{str(self.version)} [ROLLBACK]:[/bold] "
        )
        self.logger.info("-" * 40)

        # undo each bench
        for bench, exception in  self.migration_executor.migrate_benches.items():
            if not exception:
                self.undo_bench_migrate(bench)

        for backup in self.backup_manager.backups:
            self.backup_manager.restore(backup, force=True)

        richprint.print(
            f"Successfull", prefix=f"[bold]v{str(self.version)} [ROLLBACK]:[/bold] "
        )
        self.logger.info("-" * 40)

    def undo_bench_migrate(self, bench: MigrationBench):

        for backup in self.backup_manager.backups:
            if backup.bench == bench.name:
                self.backup_manager.restore(backup, force=True)

        self.logger.info(f'Undo successfull for bench: {bench.name}')

    def migrate_bench_compose(self, bench: MigrationBench):

        status_msg = 'Migrating bench compose'
        richprint.change_head(status_msg)

        compose_version = bench.compose_project.compose_file_manager.get_version()

        if not bench.compose_project.compose_file_manager.exists():
            richprint.print(f"{status_msg} {compose_version} -> {self.version}: Failed ")
            raise MigrationExceptionInBench(f"{bench.compose_project.compose_file_manager.compose_path} not found.")

        # change image tag to the latest
        # in this migration only tag of frappe container is changed
        images_info = bench.compose_project.compose_file_manager.get_all_images()
        image_info = images_info['frappe']

        # get v0.11.0 frappe image
        image_info['tag'] = self.version.version_string()
        image_info['name'] = 'ghcr.io/rtcamp/frappe-manager-frappe'

        output = bench.compose_project.docker.pull(container_name=f"{image_info['name']}:{image_info['tag']}", stream=True)
        richprint.live_lines(output, padding=(0, 0, 0, 2))

        bench.compose_project.compose_file_manager.set_all_images(images_info)

        bench.compose_project.compose_file_manager.set_version(str(self.version))
        bench.compose_project.compose_file_manager.write_to_file()

        richprint.print(f"{status_msg} {compose_version} -> {self.version}: Done")
