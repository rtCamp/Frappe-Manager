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
    FM_IMAGE_PREFIX,
)
from frappe_manager.ssl_manager.trust_store_manager import TrustStoreEntry, TrustStoreManager
from frappe_manager.utils.prune import dir_size

# fm's images are knowable, not guessable: the compose TEMPLATES name every image it pulls
# (utils/site.py:get_all_docker_images, the same set the first-install prefetch warms), and the
# containers on this host name the tags actually in use, which is what catches an image an older
# fm pulled under a tag this version no longer mentions. Anything still backing a container fm is
# NOT removing is left alone and reported: that container belongs to something else on this host.

GLOBAL_CONTAINERS = ("fm_mariadb", "fm_postgres", "fm_nginx-proxy")
GLOBAL_NETWORKS = ("fm-frontend-network", "fm-backend-network")
# Only the macOS services template uses named volumes; the Linux one bind-mounts under
# CLI_SERVICES_DIRECTORY, where the path half of the plan already covers it.
GLOBAL_VOLUMES = ("fm-mariadb-data", "fm-postgres-data")

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
    volumes: list[str] = field(default_factory=list)
    images: list[str] = field(default_factory=list)
    images_in_use: list[tuple[str, str]] = field(default_factory=list)
    paths: list[PathEntry] = field(default_factory=list)
    trust: list[TrustStoreEntry] = field(default_factory=list)

    @property
    def total_size(self) -> int:
        return sum(entry.size for entry in self.paths)

    def is_empty(self) -> bool:
        return not any((self.containers, self.networks, self.volumes, self.images, self.paths, self.trust))


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


def _bench_paths(benches: list[str], incomplete: list[str]) -> list[PathEntry]:
    """Each bench directory, then the sites directory itself.

    The root is a target in its own right, not the sum of its benches: `bench_names` skips
    non-directories, so a stray file beside the benches is planned by nobody, and leaving an
    empty `sites/` behind also keeps CLI_DIR non-empty, which is what stopped fm's home from
    being removed at the end of a full teardown. Its size is the residue, so the totals still add up.
    """
    if not CLI_BENCHES_DIRECTORY.exists():
        return []

    entries = [PathEntry(CLI_BENCHES_DIRECTORY / name, dir_size(CLI_BENCHES_DIRECTORY / name)) for name in benches + incomplete]
    residue = dir_size(CLI_BENCHES_DIRECTORY) - sum(entry.size for entry in entries)
    entries.append(PathEntry(CLI_BENCHES_DIRECTORY, max(residue, 0)))
    return entries


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
            # Prefix scan, not a name built from `benches`: a bench whose directory is already
            # gone still owns its network and volumes, and those are what strand docker's address
            # pool and disk with nothing left on disk to name them.
            plan.networks += sorted(n for n in docker.network_ls() if n.startswith(BENCH_OBJECT_PREFIX))
            plan.volumes += sorted(v for v in docker.volume_ls() if v.startswith(BENCH_OBJECT_PREFIX))
        plan.paths += _bench_paths(benches, incomplete)

    if Scope.services in scopes:
        if docker is not None:
            existing = set(docker.container_names("fm_"))
            plan.containers += [name for name in GLOBAL_CONTAINERS if name in existing]
            networks = set(docker.network_ls())
            plan.networks += [name for name in GLOBAL_NETWORKS if name in networks]
            volumes = set(docker.volume_ls())
            plan.volumes += [name for name in GLOBAL_VOLUMES if name in volumes]
        if CLI_SERVICES_DIRECTORY.exists():
            plan.paths.append(PathEntry(CLI_SERVICES_DIRECTORY, dir_size(CLI_SERVICES_DIRECTORY)))

    if Scope.host in scopes:
        plan.paths += [PathEntry(path, dir_size(path) if path.is_dir() else path.stat().st_size) for path in _host_paths(keep_backups)]
        if include_images and docker is not None:
            candidates = _fm_images(docker)
            held = _images_held_by_foreign_containers(docker, candidates, set(plan.containers))
            plan.images = sorted(candidates - held.keys())
            plan.images_in_use = sorted(held.items())

    if Scope.trust in scopes:
        plan.trust = TrustStoreManager().find()

    return plan


def _fm_images(docker) -> set[str]:
    """Every image on this host that fm pulled or built, whatever pulled it first.

    Three sources, unioned then intersected with what is actually present: fm's own repositories
    at any tag, the stock set the templates name, and the image each fm container actually runs.
    """
    from frappe_manager.utils.site import get_all_docker_images

    wanted = {f"{info['name']}:{info['tag']}" for info in get_all_docker_images().values()}
    wanted |= {
        image
        for name, image in docker.container_images().items()
        if name.startswith(BENCH_OBJECT_PREFIX) or name in GLOBAL_CONTAINERS
    }

    present = set()
    for image in docker.images():
        repository = image.get("Repository", "")
        tag = image.get("Tag", "")
        if not tag or tag == "<none>":
            continue
        reference = f"{repository}:{tag}"
        if repository.startswith(FM_IMAGE_PREFIX) or reference in wanted:
            present.add(reference)
    return present


def _images_held_by_foreign_containers(docker, images: set[str], removing: set[str]) -> dict[str, str]:
    """{image: container} for images some OTHER project's container runs.

    `docker rmi -f` untags an image out from under a stopped container, so a host running its own
    `redis:8-alpine` would silently lose it. Ownership is decided by whether fm is removing the
    container, not by the image's name.
    """
    held = {}
    for name, image in docker.container_images().items():
        if image in images and name not in removing:
            held.setdefault(image, name)
    return held
