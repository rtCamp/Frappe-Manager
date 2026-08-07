"""
Migration for v0.19.1 - move benches onto the v0.19.1 images.

v0.19.1 carries no structural changes. Its only job is to re-tag each bench's
compose images so they pick up the frappe image that ships Chromium's shared
libraries, without which Frappe v16's chrome PDF generator cannot start.
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

    def bench_basic_backup(self, bench: MigrationBench):
        """Back up every compose file this migration rewrites.

        The parent only backs up docker-compose.yml, but the worker and
        admin-tools compose files reference the same images and are rewritten
        here too, so they have to be recoverable for the rollback in
        MigrationBase.migrate_benches() to be complete.
        """
        super().bench_basic_backup(bench)

        if self.migration_executor.skip_backup or bench.name in self.migration_executor.skip_backup_for:
            return

        for compose_path in self._compose_files(bench):
            if compose_path.name == "docker-compose.yml":
                continue  # already handled by the parent
            self.backup_manager.backup(compose_path, bench_name=bench.name)
            self.output.print(f"Backed up {compose_path.name}")

    def migrate_bench(self, bench: MigrationBench):
        """Re-tag fm images across every compose file, then pull if anything moved."""
        self._images_updated = False

        with spinner(self.output, f"Updating image tags for {bench.name}"):  # type: ignore[arg-type]
            for compose_path in self._compose_files(bench):
                self._migrate_compose_file(compose_path)

            # Skip the pull when nothing changed, so re-running on an
            # already-correct bench cannot fail on a transient registry error.
            if self._images_updated:
                self._pull_bench_images(bench)

        if not self._images_updated:
            self.output.print(f"{bench.name} already on {self.version.version_string()} images")

        self.output.print(f"Successfully migrated {bench.name} to {self.version.version_string()}")

    def _compose_files(self, bench: MigrationBench) -> list[Path]:
        """Every compose file in the bench, so a new one cannot be silently missed."""
        return sorted(p for p in bench.path.glob("docker-compose*.yml") if p.is_file())

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
