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
  rolling swap (handled by the bench renderer via ``ServiceSpec.rolling``).
* ``ServiceSpec.enabled`` suppresses a service: ``apply_specs`` writes the
  ``disabled`` compose profile, which ``get_services_list(exclude_disabled=True)``,
  the readiness wait and the running-status checks all honour. Its only user
  today is the pair of per-bench redis containers, switched off when the bench
  points at a redis fm does not own (``[redis]``).
* ``ServiceSpec.env`` is service-level environment the projection owns:
  ``MYSQL_HOME`` for the long-running services when the bench has an external
  database, so the dumps fm does not wrap (a desk "Download Backup" is a
  background job in a worker, and an app can schedule one on the scheduler)
  find the CA bundle through the mariadb client's option file.
"""

from dataclasses import dataclass
from typing import Protocol
from urllib.parse import parse_qs, urlparse

from frappe_manager.docker import DockerVolumeMount, DockerVolumeType
from frappe_manager.site_manager.modules import db_tls

# Registry of fm bench code services and their mode-varying roles.
# rolling: web services scaled 2->1 during the rolling swap (shed container_name).
# db_cli: shells out to the mariadb client on its own (dump-based backups), so it
# needs MYSQL_HOME when the bench has an external database.
BENCH_CODE_SERVICES: dict[str, dict] = {
    "frappe": {"rolling": True, "db_cli": True},
    "nginx": {"rolling": True, "db_cli": False},
    "socketio": {"rolling": False, "db_cli": False},
    "schedule": {"rolling": False, "db_cli": True},
}

# Per-bench redis containers, started only when the bench has no external redis.
BENCH_REDIS_SERVICES: tuple[str, ...] = ("redis-cache", "redis-queue")


@dataclass(frozen=True)
class RenderContext:
    """Operation context for a projection.

    deploy_tag: candidate app tag for deploy/switch/rollback (None = the
    recorded ``deploy_state.current_tag``). rolling: rolling-swap render.
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
    managed_binds: the mode-owned binds (managed targets are stripped + re-added);
    empty means the projection owns no volumes for this service and leaves them.
    rolling: web service scaled during the rolling swap (container_name handling).
    enabled: False writes the ``disabled`` compose profile, so fm never starts the
    service (external redis). True clears it again.
    env: service-level environment to merge in (MYSQL_HOME for external db).
    """

    name: str
    image: str | None
    managed_binds: tuple[VolumeBind, ...]
    rolling: bool = False
    enabled: bool = True
    env: tuple[tuple[str, str], ...] = ()


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


def default_code_image() -> str:
    """Stock fm frappe image for the running fm version (matches the template render)."""
    import importlib.metadata

    return f"ghcr.io/rtcamp/frappe-manager-frappe:v{importlib.metadata.version('frappe-manager')}"


def default_nginx_image() -> str:
    """Stock fm nginx image for the running fm version (matches the template render)."""
    import importlib.metadata

    return f"ghcr.io/rtcamp/frappe-manager-nginx:v{importlib.metadata.version('frappe-manager')}"


@dataclass(frozen=True)
class MountShape:
    """Mount runtime: live-mounted workspace; stock fm images (or base_image override).

    Images are pinned EXPLICITLY (not left to the template default) so a runtime
    flip (image -> mount) re-points services off the app image -- an existing
    compose has no "template default" to fall back to.
    """

    base_image: str | None = None

    def image(self, service: str) -> str | None:
        if service == "nginx":
            return default_nginx_image()
        return self.base_image or default_code_image()

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


def db_cli_env(config) -> tuple[tuple[str, str], ...]:
    """``MYSQL_HOME`` for services that shell out to the mariadb client, if needed.

    Empty unless the bench has an external database. Frappe's ``get_command``
    builds every ``mariadb``/``mariadb-dump`` invocation from user, host, port and
    password alone and never reads ``db_ssl_*``, so the client finds its TLS
    material only through ``MYSQL_HOME=<dir>``, which makes it read ``<dir>/my.cnf``.
    These services are long-running and serve every site in the bench, so they get
    the bench-level bundle rather than any one site's file.
    """
    # "Any site on an external database", not "this bench has a [database] table": the check is
    # about whether the mariadb client in these bench-wide services will ever need TLS material.
    if not any(site.database for site in (config.sites or {}).values()):
        return ()
    return (("MYSQL_HOME", db_tls.bench_mysql_home()),)


def redis_service_specs(config) -> tuple[ServiceSpec, ...]:
    """Enabled state of the two per-bench redis containers.

    ``[redis]`` means the bench talks to a redis fm does not own, so fm must not
    start its own. Absent means today's behaviour, and the specs then carry
    ``enabled=True`` so dropping ``[redis]`` clears the profile again.
    """
    enabled = config.redis is None
    return tuple(ServiceSpec(name=name, image=None, managed_binds=(), enabled=enabled) for name in BENCH_REDIS_SERVICES)


def bench_service_specs(config, ctx: RenderContext = DEFAULT_CONTEXT) -> tuple[ServiceSpec, ...]:
    """Specs for the bench compose services. Pure function of (config, ctx).

    The code services carry the runtime shape; the redis services carry only their
    enabled state, so every writer of the bench compose (create, update, deploy
    re-pin) agrees on whether fm starts redis.
    """
    shape = runtime_shape(config, ctx)
    if shape is None:
        return ()
    db_env = db_cli_env(config)
    return tuple(
        ServiceSpec(
            name=name,
            image=shape.image(name),
            managed_binds=tuple(shape.binds()),
            rolling=meta["rolling"],
            env=db_env if meta["db_cli"] else (),
        )
        for name, meta in BENCH_CODE_SERVICES.items()
    ) + redis_service_specs(config)


def worker_service_specs(
    config, worker_names: list[str], ctx: RenderContext = DEFAULT_CONTEXT
) -> tuple[ServiceSpec, ...]:
    """Specs for the workers compose services. Pure function of (config, ctx).

    Every worker runs backup jobs (a desk "Download Backup" is one), so they all
    carry ``MYSQL_HOME`` when the bench has an external database.
    """
    shape = runtime_shape(config, ctx)
    if shape is None:
        return ()
    db_env = db_cli_env(config)
    return tuple(
        ServiceSpec(name=name, image=shape.image(name), managed_binds=tuple(shape.binds()), env=db_env)
        for name in worker_names
    )


# --------------------------------------------------------------------------- renderer


def bind_strings(spec: ServiceSpec) -> list[str]:
    """Managed binds as raw compose strings (template-dict rendering path)."""
    return [f"{b.host}:{b.container}" for b in spec.managed_binds]


def apply_specs(compose_file_manager, specs: tuple[ServiceSpec, ...], site: str) -> None:
    """Project ``specs`` onto a ComposeFile (enabled, env, image, managed binds).

    The single imperative shell over the pure spec model. Idempotent: the
    ``disabled`` profile follows ``spec.enabled`` in both directions, managed bind
    targets are stripped and re-added, and all other mounts pass through untouched.
    A spec with no managed binds (the redis suppression specs) leaves the service's
    volumes alone. Does NOT write the file -- callers batch their own write.
    """
    services = compose_file_manager.get_services_list()
    stripped = managed_targets(site)
    images: dict = {}
    for spec in specs:
        if spec.name not in services:
            continue
        compose_file_manager.set_service_disabled(spec.name, not spec.enabled)
        if not spec.enabled:
            continue
        if spec.env:
            compose_file_manager.set_envs(spec.name, dict(spec.env), append=True)
        if spec.image:
            repo, _, tagpart = spec.image.rpartition(":")
            images[spec.name] = {"name": repo, "tag": tagpart}
        if not spec.managed_binds:
            continue
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


# --------------------------------------------------------------------------- redis urls


def validate_redis_endpoints(cache: str, queue: str) -> None:
    """Refuse a ``[redis]`` pair whose cache and queue are the same logical database.

    Different indexes on one server is the documented shape and is fine; the same
    index is not. Raises ValueError, so a pydantic validator or a CLI check can
    surface it directly.
    """
    if _redis_endpoint(cache) != _redis_endpoint(queue):
        return
    raise ValueError(
        f"redis cache and queue resolve to the same host, port and database index: "
        f"cache={_display(cache)!r}, queue={_display(queue)!r}. "
        'A restore calls frappe.cache.delete_keys(""), a mass delete over the cache connection, '
        "so a shared index would destroy the queue along with it. "
        "Give them separate database indexes (for example .../0 for cache and .../1 for queue)."
    )


def _display(url: str) -> str:
    """The URL as the operator wrote it, with any inline password masked."""
    password = urlparse(url).password
    return url.replace(f":{password}@", ":***@", 1) if password else url


def _redis_endpoint(url: str) -> tuple[str, int, str, str]:
    """(host, port, socket path, database index) of a redis URL.

    Normalised for equality only, not for connecting: on ``redis``/``rediss`` the
    path is the database index, on a unix socket URL it is the socket and the
    index comes from ``?db=``.
    """
    parsed = urlparse(url)
    tcp = parsed.scheme in ("redis", "rediss")
    index = parse_qs(parsed.query).get("db", [""])[0]
    if not index:
        index = parsed.path.strip("/") if tcp else "0"
    return (parsed.hostname or "", parsed.port or 6379, "" if tcp else parsed.path, index or "0")
