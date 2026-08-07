"""
Migration for v0.19.1 - move benches onto the v0.19.1 images.

v0.19.1 carries no structural changes. Its only job is to re-tag each bench's
compose images so they pick up the frappe image that ships Chromium's shared
libraries, without which Frappe v16's chrome PDF generator cannot start, and to
recreate already-running containers so they run off the new image immediately.
"""

import re
from pathlib import Path
from typing import Any

from ruamel.yaml import YAML

from frappe_manager.migration_manager.migration_base import MigrationBase
from frappe_manager.migration_manager.migration_helpers import MigrationBench
from frappe_manager.migration_manager.version import Version
from frappe_manager.output_manager.context_managers import spinner

FM_IMAGE_PATTERN = re.compile(r"(ghcr\.io/rtcamp/frappe-manager-[^:]+):v[0-9]+\.[0-9]+\.[0-9]+(?:\.dev[0-9]+)?")


class MigrationV0191(MigrationBase):
    version = Version("0.19.1")

    def migrate_bench(self, bench: MigrationBench):
        """Re-tag fm images across every compose file, then pull and recreate if anything moved."""
        self._images_updated = False

        bench_was_running = bench.running
        workers_were_running = bench.workers_running

        with spinner(self.output, f"Updating image tags for {bench.name}"):  # type: ignore[arg-type]
            for compose_path in bench.managed_compose_paths:
                self._migrate_compose_file(compose_path)

            # Skip the pull when nothing changed, so re-running on an
            # already-correct bench cannot fail on a transient registry error.
            if self._images_updated:
                self._pull_bench_images(bench)
                self._recreate_services(bench, bench_was_running, workers_were_running)

        if not self._images_updated:
            self.output.print(f"{bench.name} already on {self.version.version_string()} images")

        self.output.print(f"Successfully migrated {bench.name} to {self.version.version_string()}")

    def _migrate_compose_file(self, compose_path: Path):
        yaml = YAML(typ="rt")
        yaml.preserve_quotes = True
        yaml.default_flow_style = False

        with compose_path.open() as f:
            compose_data = yaml.load(f)

        if not compose_data or "services" not in compose_data:
            return

        services = compose_data["services"]

        # Skip files with no fm images, so admin-tools is not reformatted and given
        # an x-version key it never had.
        if not any(FM_IMAGE_PATTERN.search(str(svc.get("image", ""))) for svc in services.values()):
            return

        self._update_service_images(services, compose_path.name)

        # Plain semver, no ``v`` prefix, matching v0.19.0.
        compose_data["x-version"] = str(self.version)

        with compose_path.open("w") as f:
            yaml.dump(compose_data, f)

    def _update_service_images(self, services: dict[str, Any], filename: str):
        effective_tag = self._get_image_tag_for_migration()

        for service_name, service_config in services.items():
            if "image" not in service_config:
                continue

            old_image = service_config["image"]
            new_image = FM_IMAGE_PATTERN.sub(rf"\1:{effective_tag}", old_image)

            if new_image == old_image:
                continue

            old_tag_match = re.search(r":v([0-9]+\.[0-9]+\.[0-9]+(?:\.dev[0-9]+)?)", old_image)
            old_tag = old_tag_match.group(1) if old_tag_match else "unknown"

            service_config["image"] = new_image
            self._images_updated = True
            self.output.print(f"{filename}: {service_name} image v{old_tag} -> {effective_tag}")

    def _pull_bench_images(self, bench: MigrationBench):
        from frappe_manager.migration_manager.migration_exections import MigrationExceptionInBench

        self.output.print(f"Pulling updated images ({self._get_image_tag_for_migration()})...", emoji_code="📦")

        result = bench.compose.pull(stream=False)

        if result.exit_code != 0:
            raise MigrationExceptionInBench(f"Failed to pull images for {bench.name}. Docker pull failed.")

        self.output.print("✓ Images ready", emoji_code="✅")

    def _recreate_services(self, bench: MigrationBench, bench_was_running: bool, workers_were_running: bool):
        """Recreate containers that were already up, so they run off the re-tagged image.

        Re-tagging compose alone leaves a running bench on the old image until the
        next stop/start — which is exactly the image missing Chromium's libraries.
        """
        if not (bench_was_running or workers_were_running):
            return

        self.output.print("Recreating containers on the new images...", emoji_code="🔄")

        try:
            # pull="never": _pull_bench_images already fetched the new tags.
            if bench_was_running:
                bench.compose.up(detach=True, pull="never")
                self._ensure_nginx_running(bench)

            if workers_were_running:
                bench.workers_docker.compose.up(detach=True, pull="never")

            self.output.print("✓ Containers recreated", emoji_code="✅")
        except Exception as e:
            self.output.warning(f"Container recreation failed: {e}")
            self.output.warning(f"Run 'fm restart {bench.name}' to start using the new images.")

    def _ensure_nginx_running(self, bench: MigrationBench):
        """Bring nginx back up if the parallel recreate raced it past frappe.

        nginx declares no depends_on and sets ``restart: no``, so a recreate that
        starts it before frappe rejoins the network kills it for good:

            nginx: [emerg] host not found in upstream "frappe:80"

        The bench then answers nothing until the next ``fm start``. That command
        guards the same race in start_bench(), so this mirrors it.
        """
        if bench.get_services_running_status().get("nginx") == "running":
            return

        self.output.print("nginx lost the race with frappe, restarting it", emoji_code="🔄")
        bench.compose.up(services=["nginx"], detach=True, pull="never")
