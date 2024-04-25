from frappe_manager.migration_manager.migration_base import MigrationBase
from frappe_manager.docker_wrapper.DockerClient import DockerClient
from frappe_manager.migration_manager.migration_exections import MigrationExceptionInBench
from frappe_manager.migration_manager.migration_helpers import MigrationBench, MigrationBenches
from frappe_manager.display_manager.DisplayManager import richprint
from frappe_manager.migration_manager.version import Version
from frappe_manager import CLI_DIR
from frappe_manager.utils.helpers import get_container_name_prefix


class MigrationV0120(MigrationBase):
    version = Version("0.12.0")

    def __init__(self):
        super().init()
        self.benches_dir = CLI_DIR / "sites"
        self.benches_manager = MigrationBenches(self.benches_dir)

    def up(self):
        richprint.print(f"Started", prefix=f"[bold]v{str(self.version)}:[/bold] ")
        self.logger.info("-" * 40)

        # take backup of each of the bench docker compose
        self.benches_manager.stop_benches()

        benches = self.benches_manager.get_all_benches()

        # Pulling latest image
        self.image_info = {"tag": self.version.version_string(), "name": "ghcr.io/rtcamp/frappe-manager-frappe"}
        pull_image = f"{self.image_info['name']}:{self.image_info['tag']}"

        richprint.change_head(f"Pulling Image {pull_image}")
        output = DockerClient().pull(container_name=pull_image, stream=True)
        richprint.live_lines(output, padding=(0, 0, 0, 2))
        richprint.print(f"Image pulled [blue]{pull_image}[/blue]")

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
            bench.path / "workspace" / "frappe-bench" / "sites" / "common_site_config.json",
            bench_name=bench.name,
        )

        bench.compose_project.down_service(volumes=False)
        self.migrate_bench_compose(bench)
        self.migrate_workers_compose(bench)

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
            richprint.print(f"{status_msg} {compose_version} -> {self.version}: Failed ")
            raise MigrationExceptionInBench(f"{bench.compose_project.compose_file_manager.compose_path} not found.")

        images_info = bench.compose_project.compose_file_manager.get_all_images()

        # for all services
        images_info["frappe"] = self.image_info
        images_info["socketio"] = self.image_info
        images_info["schedule"] = self.image_info

        compose_yml = bench.compose_project.compose_file_manager.yml
        # remove restart: from all the services
        for service in compose_yml["services"]:
            try:
                del compose_yml["services"][service]["restart"]
            except KeyError as e:
                self.logger.error(f"{bench.name}: Not able to delete restart: always attribute from compose file.{e}")
                pass

        richprint.print("Removed [blue]restart: always[/blue]")

        bench.compose_project.compose_file_manager.set_version(str(self.version))
        bench.compose_project.compose_file_manager.set_all_images(images_info)
        bench.compose_project.compose_file_manager.write_to_file()
        richprint.print(f"{status_msg} {compose_version} -> {self.version}: Done")

    def migrate_workers_compose(self, bench: MigrationBench):
        if bench.workers_compose_project.compose_file_manager.compose_path.exists():
            richprint.print("Migrating workers compose")
            # workers image set
            workers_info = bench.workers_compose_project.compose_file_manager.get_all_images()

            for worker in workers_info.keys():
                workers_info[worker] = self.image_info

            worker_compose_yml = bench.workers_compose_project.compose_file_manager.yml
            for service in worker_compose_yml["services"]:
                try:
                    del worker_compose_yml["services"][service]["restart"]
                except KeyError as e:
                    self.logger.error(
                        f"{bench.name} worker: Not able to delete restart: always attribute from compose file.{e}"
                    )
                    pass

            bench.workers_compose_project.compose_file_manager.set_top_networks_name(
                "site-network", get_container_name_prefix(bench.name)
            )
            bench.workers_compose_project.compose_file_manager.set_all_images(workers_info)

            bench.workers_compose_project.compose_file_manager.set_container_names(
                get_container_name_prefix(bench.name)
            )
            bench.workers_compose_project.compose_file_manager.write_to_file()
            richprint.print("Migrating workers compose: Done")
