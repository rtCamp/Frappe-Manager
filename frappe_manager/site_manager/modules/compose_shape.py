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
* ``RenderContext.deploy_image`` lets deploy/switch/rollback shape a CANDIDATE image
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

import json
import shlex
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from enum import Enum
from ipaddress import ip_address
from pathlib import Path
from typing import Protocol
from urllib.parse import ParseResult, parse_qs, unquote, urlparse

from frappe_manager import BENCH_PYTHON, CONTAINER_BENCH_DIR, CONTAINER_SITES_DIR
from frappe_manager.docker import DockerVolumeMount, DockerVolumeType
from frappe_manager.site_manager.modules import db_tls
from frappe_manager.utils.helpers import ImageRef

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

    deploy_image: candidate app image for deploy/switch/rollback (None = the
    recorded ``deploy_state.current_image``). rolling: rolling-swap render.
    """

    deploy_image: str | None = None
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


def data_binds(sites: Sequence[str]) -> list[VolumeBind]:
    """Image-mode data-only binds: mutable site data, never code/assets.

    One bind per SITE, because the image carries no site directories at all (they are data) and
    a site that is not bind-mounted simply does not exist inside the container: `bench --site X`
    answers "404 Not Found". The sites directory is not mounted wholesale because `assets/` and
    `apps.txt` live beside the site directories and come FROM the image.
    """
    if isinstance(sites, str):
        # A str IS a Sequence[str], so this would iterate CHARACTERS and silently mount
        # `sites/s`, `sites/.`, `sites/l` ... The result is a working compose file describing
        # the wrong thing, which no type checker catches and no import error reveals.
        raise TypeError(f"data_binds takes the bench's sites, not one site name: got {sites!r}")
    sites_rel = "./workspace/frappe-bench/sites"
    return [
        *(VolumeBind(f"{sites_rel}/{site}", f"{CONTAINER_SITES_DIR}/{site}") for site in sites),
        VolumeBind(f"{sites_rel}/common_site_config.json", f"{CONTAINER_SITES_DIR}/common_site_config.json"),
        VolumeBind(f"{sites_rel}/apps.txt", f"{CONTAINER_SITES_DIR}/apps.txt"),
        VolumeBind("./workspace/frappe-bench/logs", f"{CONTAINER_BENCH_DIR}/logs"),
        VolumeBind("./workspace/frappe-bench/config", f"{CONTAINER_BENCH_DIR}/config"),
    ]


def managed_targets(sites: Sequence[str]) -> set[str]:
    """Container paths owned by the mode projection (stripped before re-add)."""
    return {"/workspace", *(b.container for b in data_binds(sites))}


# The one directory a container can hand a file to the host through, in EITHER runtime.
# Mount runtime binds the whole `./workspace`, so anything works there. Image runtime binds
# only the data paths in `data_binds` above, and of those `frappe-bench/logs` is the only
# writable DIRECTORY. A file written anywhere else, `/workspace/.cache` being the one that
# bit, lands in the image's own filesystem: the write succeeds, the host never sees it, and
# the caller reports a failure whose message points at the database rather than the mount.
TRANSIT_DIR_REL = Path("frappe-bench") / "logs"


def container_transit_path(filename: str) -> tuple[Path, Path]:
    """``(container path, path relative to the bench's workspace)`` for a container-to-host file.

    Callers join the second element onto ``<bench>/workspace`` to read what the container wrote.
    Transit only: move the file to where it belongs as soon as it lands, so a dump is never left
    sitting in the log directory.
    """
    rel = TRANSIT_DIR_REL / filename
    return Path("/workspace") / rel, rel


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

    image_ref: str
    sites: tuple[str, ...]

    def image(self, service: str) -> str | None:
        if service == "nginx":
            from frappe_manager.site_manager.modules.bake import BakeManager

            return BakeManager.nginx_image_ref(self.image_ref)
        return self.image_ref

    def binds(self) -> list[VolumeBind]:
        return data_binds(self.sites)


def runtime_shape(config, ctx: RenderContext = DEFAULT_CONTEXT) -> RuntimeShape | None:
    """Select the strategy for ``config`` (+ operation context).

    Returns None when the shape cannot be determined yet (image runtime with no
    image recorded and none supplied) -- callers then leave the skeleton untouched.
    """
    from frappe_manager.site_manager.bench_config import BenchRuntime

    if config.runtime == BenchRuntime.image:
        image_ref = ctx.deploy_image or (config.deploy_state.current_image if config.deploy_state else None)
        # Every recorded site, NOT config.name: the bench name is not a site, and on a bench
        # where they differ the container would mount a directory that does not exist while
        # the real sites stayed invisible.
        return ImageShape(image_ref=image_ref, sites=tuple(config.site_names)) if image_ref else None
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


def apply_specs(compose_file_manager, specs: tuple[ServiceSpec, ...], sites: Sequence[str]) -> None:
    """Project ``specs`` onto a ComposeFile (enabled, env, image, managed binds).

    The single imperative shell over the pure spec model. Idempotent: the
    ``disabled`` profile follows ``spec.enabled`` in both directions, managed bind
    targets are stripped and re-added, and all other mounts pass through untouched.
    A spec with no managed binds (the redis suppression specs) leaves the service's
    volumes alone. Does NOT write the file -- callers batch their own write.
    """
    services = compose_file_manager.get_services_list()
    stripped = managed_targets(sites)
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
            # spec.image is the FULL reference (MountShape.base_image can legitimately be a
            # digest pin, e.g. `fm create --base-image app@sha256:...`; ImageShape.image_ref
            # can carry a registry host:port). A naive `rpartition(":")` happens to reconstruct
            # the same compose "image:" string for any of those, but it labels the split wrong
            # (a digest's hex lands in "tag"), so route it through ImageRef -- the one canonical
            # parser -- and hand `set_all_images` the real name/tag/digest instead of a guess.
            ref = ImageRef.parse(spec.image)
            images[spec.name] = {"name": ref.name, "tag": ref.tag, "digest": ref.digest}
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

    Known limit, deliberate: hosts are compared as STRINGS (after two purely
    syntactic normalisations -- see ``_normalized_redis_host`` -- that cost no
    network call), so this catches a collision only when both URLs name the host
    the same way underneath those. Measured on a live server,
    ``redis://cache-box:6379/0`` and ``redis://10.2.0.19:6379/0`` are accepted even
    when that IP IS ``cache-box``, and two CNAMEs for one server would slip through
    the same way. That residual gap is closed by ``redis_server_identity`` (below),
    which asks the servers who they are from inside the bench container instead of
    comparing hostnames -- see its docstring -- run at create readiness and deploy
    preflight, the two points a collision costs nothing to refuse.

    Resolving the names here was rejected rather than overlooked. These URLs are
    resolved by the bench CONTAINERS on a docker network, not by fm on the host, so
    a name fm resolves may differ from what the container reaches or may not resolve
    for fm at all. A check that consulted the host's resolver would be wrong in a new
    way and would fail outright on a host that cannot see the redis network, which is
    worse than a check with a stated gap. What is decidable without a resolver is
    decided here; the rest is not adjudicated by THIS function.
    """
    if _redis_endpoint(cache) != _redis_endpoint(queue):
        return
    raise ValueError(
        f"redis cache and queue resolve to the same host, port and database index: "
        f"cache={display_redis_url(cache)!r}, queue={display_redis_url(queue)!r}. "
        'A restore calls frappe.cache.delete_keys(""), a mass delete over the cache connection, '
        "so a shared index would destroy the queue along with it. "
        "Give them separate database indexes (for example .../0 for cache and .../1 for queue)."
    )


def display_redis_url(url: str) -> str:
    """The URL as the operator wrote it, with any inline password masked.

    Public (not `_`-prefixed): `create.py`'s scheme refusal (`_refuse_unsupported_redis_scheme`)
    reuses it too, so a masked cache/queue URL reads the same in both refusal messages.
    """
    password = urlparse(url).password
    return url.replace(f":{password}@", ":***@", 1) if password else url


def _redis_endpoint(url: str) -> tuple[str, int, str, int | str]:
    """(host, port, socket path, database index) of a redis URL.

    Normalised for equality only, not for connecting: on ``redis``/``rediss`` the database
    index is resolved exactly the way redis-py's own ``parse_url`` resolves it (see
    ``_tcp_database_index``), not by comparing the path text. On any other scheme the path
    is compared as a literal socket path; that scheme has no fm-supported db argument, so
    the index side of the tuple is fixed at ``"0"``.
    """
    parsed = urlparse(url)
    host = _normalized_redis_host(parsed.hostname or "")
    if parsed.scheme in ("redis", "rediss"):
        return (host, parsed.port or 6379, "", _tcp_database_index(url, parsed))
    index = parse_qs(parsed.query).get("db", [""])[0]
    return (host, parsed.port or 6379, parsed.path, index or "0")


def _normalized_redis_host(hostname: str) -> str:
    """Canonicalise a URL hostname for the collision comparison, using nothing but
    the string itself -- no DNS, no network call.

    ``urlparse().hostname`` already folds case, so ``H.Example`` and ``h.example``
    compare equal without help. Two more purely syntactic cases are closed here:
    a trailing ``.`` (the DNS root label -- ``h.example.`` and ``h.example`` name the
    same host by definition, not by lookup) is stripped, and a literal IP address is
    rewritten to its canonical form via ``ipaddress`` (so a compressed and an
    expanded spelling of one IPv6 address, e.g. ``2001:db8::1`` and
    ``2001:0db8:0000:...:0001``, compare equal). What this cannot and does not
    attempt: telling a hostname and ITS OWN IP apart from a hostname and an
    unrelated one, or two CNAMEs apart from two unrelated names -- both need an
    actual DNS answer, which is exactly the resolver call ``validate_redis_endpoints``
    documents refusing to make.
    """
    if hostname.endswith(".") and hostname != ".":
        hostname = hostname[:-1]
    try:
        return str(ip_address(hostname))
    except ValueError:
        return hostname


_SUPPORTED_REDIS_SCHEMES = ("redis", "rediss")


def unsupported_redis_scheme(url: str) -> str | None:
    """None when ``url``'s scheme is one fm and redis-py both support; otherwise a
    sentence naming what was passed, what fm accepts, and why -- shared by
    ``create.py``'s create-time refusal and ``bench_config.py``'s read-time warning
    for a hand-edited ``bench_config.toml``, so an operator sees the same wording
    from either path.

    ``redis`` and ``rediss`` are the only schemes accepted: they are what redis-py's
    own ``from_url`` connects with. ``unix`` is the third scheme redis-py itself
    supports, but fm's own readiness probe (``bench_site.py``'s
    ``BenchSiteManager._redis_endpoint``) raises for ANY URL with no hostname, and a
    unix socket URL never has one, so accepting ``unix://`` here would only defer
    that same failure by a few seconds, so it is refused alongside everything else.
    """
    scheme = urlparse(url).scheme
    if scheme in _SUPPORTED_REDIS_SCHEMES:
        return None
    shown = f"{scheme}://" if scheme else "(no scheme)"
    return (
        f"{display_redis_url(url)!r} uses {shown}, and fm only accepts redis:// and rediss://. "
        "Anything else is written verbatim into bench_config.toml and then into "
        "common_site_config.json, where nothing downstream can read it: redis-py raises ValueError at "
        "connect time and node-redis (socketio) raises TypeError('Invalid protocol') -- and fm's own "
        "readiness probe does not catch it either, because a sentinel or proxy still answers on its TCP "
        "port, so the failure would surface as a bare traceback mid-create instead of here. Frappe DOES "
        "support sentinel, but only through separate config keys (redis_cache_sentinel_enabled, "
        "redis_cache_sentinels, redis_cache_master_service and friends), never through a URL scheme -- "
        "there is no [redis] shape a sentinel URL could take."
    )


def _tcp_database_index(url: str, parsed: ParseResult) -> int:
    """The logical database index redis-py's own ``parse_url`` (``redis/connection.py``)
    resolves for a ``redis://``/``rediss://`` URL.

    Read straight out of the pinned redis-py (``redis==8.0.1``, ``connection.py:2264``):
    a ``?db=`` query argument wins over the path when both are given (``if url.path and
    "db" not in kwargs``); each is parsed with plain ``int()`` after the path is unquoted
    and stripped of EVERY ``/`` -- redis-py's own ``int(unquote(url.path).replace("/",
    ""))``. That is why a trailing slash (``/1/``) still resolves to 1, the same as
    ``/1``: no path, an empty path (``/``), and a non-integer path (``/abc``) all fall
    through to redis-py's ``try/except (AttributeError, ValueError): pass`` and connect on
    database 0 -- silently, never a raised error. That silent fallback to 0 is exactly what
    let two different-looking, equally-bogus URLs (``/abc`` vs ``/xyz``) compare unequal
    here while landing on the SAME live database. So an index redis-py cannot parse is
    refused outright rather than normalised to 0: normalising would only trade one silent
    collision for a different one the operator never asked to compare against, and refusing
    names the actual typo instead. A malformed ``?db=`` value is refused the same way, even
    though redis-py raises its own (later, at connect time) error for that one rather than
    swallowing it -- catching it here is strictly earlier, not a behaviour change.
    """
    query_db = parse_qs(parsed.query).get("db", [None])[0]
    if query_db is not None:
        raw = query_db
    elif parsed.path:
        raw = unquote(parsed.path).replace("/", "")
    else:
        return 0
    if raw == "":
        return 0
    try:
        return int(raw)
    except ValueError:
        raise ValueError(
            f"redis URL {display_redis_url(url)!r}: database index {raw!r} is not an integer. redis-py "
            "silently ignores a value it cannot parse this way and connects on database 0 "
            "instead of raising, which is exactly the kind of collision this check exists to "
            "catch -- fix the index instead."
        ) from None


# --------------------------------------------------------------------------- redis server identity


class RedisIdentity(Enum):
    """Verdict of ``redis_server_identity``. Three outcomes, not two: collapsing UNKNOWN into
    either SAME or DIFFERENT is exactly the bug this check exists to avoid -- a managed redis
    or a proxy that refuses ``INFO`` must never be read as "definitely different"."""

    SAME = "same"
    DIFFERENT = "different"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class RedisIdentityResult:
    """``identity`` is the verdict; ``detail`` is a short, operator-safe explanation -- a
    server id and a database index, never the source URLs (which may carry a password)."""

    identity: RedisIdentity
    detail: str


# Same contract as ``db_probe.Runner``: one shell command executed inside the bench container,
# combined stdout/stderr handed back as text. A non-zero exit is answered with the output
# rather than an exception -- the identity payload's marker line is either in that text or it
# is not, and either way ``redis_server_identity`` turns "it is not" into UNKNOWN rather than
# propagating a raise. Declared locally rather than imported from ``db_probe``: same shape,
# unrelated concern (redis identity, not the mysql probe).
Runner = Callable[[str], str]

# Prefixes the one line of JSON the container script prints, so it survives being mixed with a
# shell profile banner or a driver warning landing on the same stream.
REDIS_IDENTITY_MARKER = "FM_REDIS_IDENTITY"

# Bounds the container-side connection attempt: an unreachable or slow-to-answer managed
# endpoint must fail fast into UNKNOWN rather than stall the caller (a create or a deploy).
REDIS_IDENTITY_TIMEOUT_SECONDS = 10


def _redis_identity_script(cache: str, queue: str) -> str:
    """Python source for ``BENCH_PYTHON -c``: read-only ``INFO server`` for each URL.

    Each connection attempt is wrapped so ONE bad endpoint (wrong credentials, TLS refused, a
    managed provider that blocks INFO) does not lose the OTHER endpoint's answer -- both are
    always reported, ``None`` standing in for "could not tell". ``info()`` is the only command
    issued, ever: nothing is written to either server.
    """
    urls = json.dumps([cache, queue])
    return (
        "import json\n"
        "from redis import Redis\n"
        f"urls = {urls}\n"
        "def _run_id(url):\n"
        "    try:\n"
        "        client = Redis.from_url(\n"
        "            url,\n"
        f"            socket_connect_timeout={REDIS_IDENTITY_TIMEOUT_SECONDS},\n"
        f"            socket_timeout={REDIS_IDENTITY_TIMEOUT_SECONDS},\n"
        "        )\n"
        "        return client.info('server').get('run_id')\n"
        "    except Exception:\n"
        "        return None\n"
        "run_ids = [_run_id(u) for u in urls]\n"
        f'print("{REDIS_IDENTITY_MARKER} " + json.dumps({{"run_ids": run_ids}}))\n'
    )


# The only interpreter in the bench container with redis-py: Frappe itself depends on it, so it
# lives in the venv alongside pymysql (the same ``BENCH_PYTHON`` used by the database probe,
# for the identical reasoning). The bare ``python`` on PATH is the uv default and carries
# neither driver.
def redis_identity_command(cache: str, queue: str) -> str:
    """``<bench venv python> -c '<script>'`` for the container. Carries no secret beyond what
    the URLs themselves already hold (same as every other exec fm builds this way)."""
    return f"{BENCH_PYTHON} -c {shlex.quote(_redis_identity_script(cache, queue))}"


def _redis_identity_payload(text: str) -> dict | None:
    """The container script's marker line, parsed; ``None`` on anything else (no marker line, a
    line that is not valid JSON, or JSON that is not an object) -- the same shape as
    ``db_probe``'s own stage-two payload parser, deliberately: a container command can return
    partial output, a warning line, or nothing, and every one of those has to fall through to
    UNKNOWN rather than being read as a verdict.
    """
    for line in text.splitlines():
        marker = line.find(REDIS_IDENTITY_MARKER)
        if marker < 0:
            continue
        try:
            parsed = json.loads(line[marker + len(REDIS_IDENTITY_MARKER) :].strip())
        except ValueError:
            return None
        if isinstance(parsed, dict):
            return parsed
    return None


def _redis_identity_run_ids(text: str) -> tuple[str, str] | None:
    """The two ``run_id`` strings out of the container's reply, or ``None`` when the reply is
    unusable in ANY way: no marker line, invalid JSON, the wrong shape, or a missing/empty
    ``run_id`` for either side (an ``INFO`` a managed provider or a proxy restricted). Folded
    into one check so ``redis_server_identity`` has one UNKNOWN branch for all of them rather
    than a several-way fan-out.
    """
    payload = _redis_identity_payload(text)
    if payload is None:
        return None
    run_ids = payload.get("run_ids")
    if not isinstance(run_ids, list) or len(run_ids) != 2:
        return None
    cache_run_id, queue_run_id = run_ids
    if not isinstance(cache_run_id, str) or not isinstance(queue_run_id, str) or not cache_run_id or not queue_run_id:
        return None
    return cache_run_id, queue_run_id


def redis_server_identity(cache: str, queue: str, run: Runner) -> RedisIdentityResult:
    """Whether ``cache`` and ``queue`` are the same LIVE logical redis database, decided from
    inside the bench's frappe container instead of by comparing hostnames.

    ``validate_redis_endpoints`` (above) is the host-side gate: fast, no container needed, and
    blind to exactly one shape -- a hostname and its own IP, or two CNAMEs, naming one server.
    This is the second gate for that shape, run later (create readiness / deploy preflight,
    never at the point of damage -- see the callers), where a container that can actually dial
    both URLs is already available. It asks each server who it is (``INFO server``'s
    ``run_id``, stable for the life of that server process) and pairs the answer with the
    database index each URL resolves to via ``_tcp_database_index`` -- SAME server AND SAME
    index is the collision; same server, different index is the documented, safe shape.

    Three outcomes, not two. UNKNOWN -- a managed redis or a proxy (twemproxy, say) that
    restricts ``INFO``, a container that cannot be reached, a garbled or empty reply, a
    non-zero exec exit -- is returned rather than guessed at. Never raises: every failure mode
    of ``run`` and every unparseable shape of its reply is caught and turned into an UNKNOWN
    result. It is the CALLER's job to treat UNKNOWN as "proceed" -- refusing an operation over
    a question this could not answer would be worse than the residual risk the host-side check
    already covers.
    """
    try:
        cache_index = _tcp_database_index(cache, urlparse(cache))
        queue_index = _tcp_database_index(queue, urlparse(queue))
    except ValueError as exc:
        return RedisIdentityResult(RedisIdentity.UNKNOWN, f"could not resolve a database index: {exc}")

    try:
        text = run(redis_identity_command(cache, queue))
    except Exception as exc:
        return RedisIdentityResult(RedisIdentity.UNKNOWN, f"container exec failed: {exc}")

    run_ids = _redis_identity_run_ids(text)
    if run_ids is None:
        return RedisIdentityResult(
            RedisIdentity.UNKNOWN,
            "no usable identity reply from the bench container: missing, garbled, or INFO restricted -- a "
            "managed provider or a proxy in front of redis may block it",
        )
    cache_run_id, queue_run_id = run_ids

    if cache_run_id == queue_run_id and cache_index == queue_index:
        return RedisIdentityResult(
            RedisIdentity.SAME,
            f"cache and queue are the same live redis server (run_id {cache_run_id}) on database index {cache_index}",
        )
    return RedisIdentityResult(RedisIdentity.DIFFERENT, "different server or different database index")
