"""Compose shape -- the single source of truth for fm's mode-varying compose fields.

Architecture (functional core, imperative shell):

    BenchConfig + RenderContext --Factory--> ServiceSpec(s) --Renderer--> ComposeFile
                                     |
                              RuntimeShape (Strategy: MountShape | ImageShape)

* ``RuntimeShape`` is the ONLY polymorphic piece: exactly two axes differ between
  the mount and image runtimes -- the code-service image and the workspace binds.
  Everything else in the compose (healthchecks, expose, entrypoints, redis,
  networks) is mode-invariant skeleton owned by the template.
* ``ServiceSpec`` is a pure decision record. Factories are pure functions of
  (config, context) -- no I/O, unit-testable without Docker or YAML.
* ``apply_specs`` is the one imperative function that projects specs onto a
  ComposeFile. It is deterministic and idempotent: managed bind targets are
  stripped and re-added, everything else (fm-sockets, nginx conf binds, CA cert)
  passes through. User customizations belong in ``docker-compose.override.yml``,
  which fm never writes and Docker merges on top.
* ``RenderContext.deploy_tag`` lets deploy/switch/rollback shape a CANDIDATE tag
  without mutating ``deploy_state`` mid-pipeline; ``rolling`` marks the
  blue-green swap (handled by the bench renderer via ``ServiceSpec.rolling``).
* ``ServiceSpec.enabled`` is the seam for a future per-bench ``[services]``
  toggle; always True today. (Disabling a service also needs bench-nginx
  upstream + supervisor changes -- this is the hook, not the whole feature.)
"""

from dataclasses import dataclass
from typing import Protocol

from frappe_manager.docker import DockerVolumeMount, DockerVolumeType

# Registry of fm bench code services and their mode-varying roles.
# rolling: web services scaled 2->1 during the blue-green swap (shed container_name).
BENCH_CODE_SERVICES: dict[str, dict] = {
    "frappe": {"rolling": True},
    "nginx": {"rolling": True},
    "socketio": {"rolling": False},
    "schedule": {"rolling": False},
}


@dataclass(frozen=True)
class RenderContext:
    """Operation context for a projection.

    deploy_tag: candidate app tag for deploy/switch/rollback (None = the
    recorded ``deploy_state.current_tag``). rolling: blue-green swap render.
    """

    deploy_tag: str | None = None
    rolling: bool = False


DEFAULT_CONTEXT = RenderContext()


@dataclass(frozen=True)
class VolumeBind:
    host: str
    container: str


@dataclass(frozen=True)
class ServiceSpec:
    """Per-service decision record: what the mode projection owns for one service.

    image: "repo:tag" to pin (None = keep the skeleton/template default).
    managed_binds: the mode-owned binds (managed targets are stripped + re-added).
    rolling: web service scaled during blue-green (container_name handling).
    enabled: future per-bench service toggle seam; always True today.
    """

    name: str
    image: str | None
    managed_binds: tuple[VolumeBind, ...]
    rolling: bool = False
    enabled: bool = True


def data_binds(site: str) -> list[VolumeBind]:
    """Image-mode data-only binds: mutable site data, never code/assets."""
    sites_rel = "./workspace/frappe-bench/sites"
    return [
        VolumeBind(f"{sites_rel}/{site}", f"/workspace/frappe-bench/sites/{site}"),
        VolumeBind(f"{sites_rel}/common_site_config.json", "/workspace/frappe-bench/sites/common_site_config.json"),
        VolumeBind(f"{sites_rel}/apps.txt", "/workspace/frappe-bench/sites/apps.txt"),
        VolumeBind("./workspace/frappe-bench/logs", "/workspace/frappe-bench/logs"),
        VolumeBind("./workspace/frappe-bench/config", "/workspace/frappe-bench/config"),
    ]


def managed_targets(site: str) -> set[str]:
    """Container paths owned by the mode projection (stripped before re-add)."""
    return {"/workspace", *(b.container for b in data_binds(site))}


# --------------------------------------------------------------------------- strategy


class RuntimeShape(Protocol):
    """The two mode-varying axes of a code service."""

    def image(self, service: str) -> str | None: ...

    def binds(self) -> list[VolumeBind]: ...


@dataclass(frozen=True)
class MountShape:
    """Mount runtime: live-mounted workspace; base image (or override)."""

    base_image: str | None = None

    def image(self, service: str) -> str | None:
        # nginx keeps the stock fm nginx image; base_image overrides code services only.
        return None if service == "nginx" else self.base_image

    def binds(self) -> list[VolumeBind]:
        return [VolumeBind("./workspace", "/workspace")]


@dataclass(frozen=True)
class ImageShape:
    """Image runtime: immutable app image; data-only binds."""

    tag: str
    site: str

    def image(self, service: str) -> str | None:
        if service == "nginx":
            from frappe_manager.site_manager.modules.bake import BakeManager

            return BakeManager.nginx_image_tag(self.tag)
        return self.tag

    def binds(self) -> list[VolumeBind]:
        return data_binds(self.site)


def runtime_shape(config, ctx: RenderContext = DEFAULT_CONTEXT) -> RuntimeShape | None:
    """Select the strategy for ``config`` (+ operation context).

    Returns None when the shape cannot be determined yet (image runtime with no
    tag recorded and none supplied) -- callers then leave the skeleton untouched.
    """
    from frappe_manager.site_manager.bench_config import BenchRuntime

    if config.runtime == BenchRuntime.image:
        tag = ctx.deploy_tag or (config.deploy_state.current_tag if config.deploy_state else None)
        return ImageShape(tag=tag, site=config.name) if tag else None
    return MountShape(base_image=config.base_image)


# --------------------------------------------------------------------------- factory


def bench_service_specs(config, ctx: RenderContext = DEFAULT_CONTEXT) -> tuple[ServiceSpec, ...]:
    """Specs for the bench compose code services. Pure function of (config, ctx)."""
    shape = runtime_shape(config, ctx)
    if shape is None:
        return ()
    return tuple(
        ServiceSpec(
            name=name,
            image=shape.image(name),
            managed_binds=tuple(shape.binds()),
            rolling=meta["rolling"],
        )
        for name, meta in BENCH_CODE_SERVICES.items()
    )


def worker_service_specs(config, worker_names: list[str], ctx: RenderContext = DEFAULT_CONTEXT) -> tuple[ServiceSpec, ...]:
    """Specs for the workers compose services. Pure function of (config, ctx)."""
    shape = runtime_shape(config, ctx)
    if shape is None:
        return ()
    return tuple(
        ServiceSpec(name=name, image=shape.image(name), managed_binds=tuple(shape.binds()))
        for name in worker_names
    )


# --------------------------------------------------------------------------- renderer


def bind_strings(spec: ServiceSpec) -> list[str]:
    """Managed binds as raw compose strings (template-dict rendering path)."""
    return [f"{b.host}:{b.container}" for b in spec.managed_binds]


def apply_specs(compose_file_manager, specs: tuple[ServiceSpec, ...], site: str) -> None:
    """Project ``specs`` onto a ComposeFile (image + managed volume binds).

    The single imperative shell over the pure spec model. Idempotent: managed
    targets are stripped and re-added; all other mounts pass through untouched.
    Does NOT write the file -- callers batch their own write.
    """
    services = compose_file_manager.get_services_list()
    stripped = managed_targets(site)
    images: dict = {}
    for spec in specs:
        if spec.name not in services or not spec.enabled:
            continue
        if spec.image:
            repo, _, tagpart = spec.image.rpartition(":")
            images[spec.name] = {"name": repo, "tag": tagpart}
        existing = compose_file_manager.get_service_volumes(spec.name)
        kept = [v for v in existing if str(v.container) not in stripped]
        binds = [
            DockerVolumeMount(
                host=b.host,
                container=b.container,
                type=DockerVolumeType.bind,
                compose_path=compose_file_manager.compose_path,
            )
            for b in spec.managed_binds
        ]
        compose_file_manager.set_service_volumes(spec.name, kept + binds)
    if images:
        compose_file_manager.set_all_images(images)
