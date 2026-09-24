"""Planning for `fm self uninstall`: everything fm put on this host, and what removes it.

Planning is separated from execution so the printed plan and the deletions are the same list,
the way `fm prune` and `fm delete` already work. A teardown that discovers its targets while
deleting them cannot show an operator what they are about to lose.
"""

from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path

from frappe_manager import (
    CLI_BENCH_CONFIG_FILE_NAME,
    CLI_BENCHES_DIRECTORY,
    CLI_CACHE_PATH,
    CLI_DIR,
    CLI_SERVICES_DIRECTORY,
)
from frappe_manager.ssl_manager.trust_store_manager import TrustStoreEntry, TrustStoreManager
from frappe_manager.utils.prune import dir_size

# Only fm's OWN images. mariadb, redis, nginx-proxy, mailpit and adminer are public images this
# host may well be using for something else, and an uninstall that deletes another project's base
# image is an uninstall nobody runs twice. They are left behind deliberately.
FM_IMAGE_PREFIX = "ghcr.io/rtcamp/frappe-manager"

GLOBAL_CONTAINERS = ("fm_mariadb", "fm_nginx-proxy")
GLOBAL_NETWORKS = ("fm-frontend-network", "fm-backend-network")

# Every per-bench object is named `fm__<bench>__…` (utils/helpers.py:get_container_name_prefix).
BENCH_OBJECT_PREFIX = "fm__"


class Scope(str, Enum):
    benches = "benches"
    services = "services"
    host = "host"
    trust = "trust"


@dataclass(frozen=True)
class PathEntry:
    path: Path
    size: int


@dataclass
class TeardownPlan:
    benches: list[str] = field(default_factory=list)
    incomplete: list[str] = field(default_factory=list)
    containers: list[str] = field(default_factory=list)
    networks: list[str] = field(default_factory=list)
    images: list[str] = field(default_factory=list)
    paths: list[PathEntry] = field(default_factory=list)
    trust: list[TrustStoreEntry] = field(default_factory=list)

    @property
    def total_size(self) -> int:
        return sum(entry.size for entry in self.paths)

    def is_empty(self) -> bool:
        return not any((self.containers, self.networks, self.images, self.paths, self.trust))


def bench_names() -> tuple[list[str], list[str]]:
    """(benches, incomplete): directories with a config, and directories without one.

    A directory with no bench_config.toml is what a failed `fm create` leaves; it is still fm's
    to remove, but it is not a bench and must not be counted as one in the plan.
    """
    if not CLI_BENCHES_DIRECTORY.exists():
        return [], []

    complete, incomplete = [], []
    for path in sorted(CLI_BENCHES_DIRECTORY.iterdir()):
        if not path.is_dir():
            continue
        (complete if (path / CLI_BENCH_CONFIG_FILE_NAME).exists() else incomplete).append(path.name)
    return complete, incomplete


def _host_paths(keep_backups: bool) -> list[Path]:
    """Everything under CLI_DIR that is neither a bench nor the services tier, plus the cache."""
    paths = []
    for path in sorted(CLI_DIR.iterdir()) if CLI_DIR.exists() else []:
        if path in (CLI_BENCHES_DIRECTORY, CLI_SERVICES_DIRECTORY):
            continue
        if keep_backups and path.name == "backups":
            continue
        paths.append(path)
    if CLI_CACHE_PATH.exists():
        paths.append(CLI_CACHE_PATH)
    return paths


def plan_teardown(docker, scopes: set[Scope], *, keep_backups: bool, include_images: bool) -> TeardownPlan:
    """What a teardown of `scopes` would remove, read from disk and from docker.

    `docker` is a DockerClient; None when the daemon could not be reached, in which case the
    docker half of the plan is empty and the filesystem half still stands. Losing the daemon must
    not make the command refuse: the files are the larger, more private half of what fm leaves.
    """
    plan = TeardownPlan()
    benches, incomplete = bench_names()

    if Scope.benches in scopes:
        plan.benches = benches
        plan.incomplete = incomplete
        if docker is not None:
            plan.containers += docker.container_names(BENCH_OBJECT_PREFIX)
        plan.paths += [PathEntry(CLI_BENCHES_DIRECTORY / name, dir_size(CLI_BENCHES_DIRECTORY / name)) for name in benches + incomplete]

    if Scope.services in scopes:
        if docker is not None:
            existing = set(docker.container_names("fm_"))
            plan.containers += [name for name in GLOBAL_CONTAINERS if name in existing]
            networks = set(docker.network_ls())
            plan.networks += [name for name in GLOBAL_NETWORKS if name in networks]
        if CLI_SERVICES_DIRECTORY.exists():
            plan.paths.append(PathEntry(CLI_SERVICES_DIRECTORY, dir_size(CLI_SERVICES_DIRECTORY)))

    if Scope.host in scopes:
        plan.paths += [PathEntry(path, dir_size(path) if path.is_dir() else path.stat().st_size) for path in _host_paths(keep_backups)]
        if include_images and docker is not None:
            plan.images = sorted(_fm_images(docker))

    if Scope.trust in scopes:
        plan.trust = TrustStoreManager().find()

    return plan


def _fm_images(docker) -> set[str]:
    images = set()
    for image in docker.images():
        repository = image.get("Repository", "")
        tag = image.get("Tag", "")
        if repository.startswith(FM_IMAGE_PREFIX) and tag and tag != "<none>":
            images.add(f"{repository}:{tag}")
    return images
