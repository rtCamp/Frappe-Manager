"""
Container CA trust for outbound HTTPS.

A dev certificate (fm's own local CA) or a `--custom --ca` certificate needs its CA in the trust
store of every container that makes a server-side HTTPS request to the bench's own domain
(PDF/print, OAuth, get_url fetches) -- otherwise the request fails with an unknown-CA error,
because neither chains to a public root the way a Let's Encrypt certificate does.

A bench can need more than one such CA at once: one domain on `--dev`, another imported with
`--custom --ca`. `NODE_EXTRA_CA_CERTS` and `REQUESTS_CA_BUNDLE` each take exactly one path, so
trusting two sources means combining them into one bundle -- but only when two or more are
actually needed. The overwhelmingly common case, a bench with only a dev certificate, mounts its
one CA file exactly as it always has: `resolve_ca_trust` returns that file's own path unchanged,
and writes nothing, whenever there is only one source to trust.
"""

from pathlib import Path
from typing import TYPE_CHECKING

from frappe_manager.site_manager.bench_config import BenchConfig
from frappe_manager.ssl_manager import SUPPORTED_SSL_TYPES

if TYPE_CHECKING:
    from frappe_manager.docker import DockerVolumeMount

# Container-internal mount point. Deliberately not named "-dev-": once a bundle can also carry
# (or consist entirely of) a --custom certificate's CA, a dev-specific name would be actively
# misleading to whoever next debugs an outbound-trust failure on a bench with no dev certificate.
CONTAINER_CA_PATH = "/etc/ssl/certs/fm-trusted-ca.pem"

# The path this feature mounted CA trust at before the bundle/merge rename. Kept only so an
# existing bench's compose can be cleaned up on its next regen; nothing ever writes this again.
_LEGACY_CONTAINER_CA_PATH = "/etc/ssl/certs/fm-dev-ca.pem"

_MANAGED_CONTAINER_CA_PATHS = (CONTAINER_CA_PATH, _LEGACY_CONTAINER_CA_PATH)

# Node's and Python-`requests`' single-bundle-path env vars, so a caller can pop both by name
# without duplicating the pair at every call site.
CA_TRUST_ENV_VARS = ("NODE_EXTRA_CA_CERTS", "REQUESTS_CA_BUNDLE")

_BUNDLE_RELATIVE_PATH = Path("config") / "fm-trusted-ca-bundle.pem"


def collect_ca_sources(bench_config: BenchConfig, services_dir: Path) -> list[Path]:
    """Every CA file this bench's containers should trust, filtered to ones that exist on disk.

    `services_dir` is the caller's own `CLI_SERVICES_DIRECTORY` (passed in, not imported here) so
    that patching it in a test patches exactly what this function reads -- it is looked up fresh on
    every call from module globals the caller controls, the same as `bench_docker.py`'s and
    `bench_workers.py`'s own use of it before this helper existed.
    """
    sources: list[Path] = []

    dev_ca = services_dir / "nginx-proxy" / "ssl" / "dev" / "ca" / "rootCA.pem"
    if any(cert.ssl_type == SUPPORTED_SSL_TYPES.dev for cert in bench_config.ssl_certificates) and dev_ca.exists():
        sources.append(dev_ca)

    for cert in bench_config.ssl_certificates:
        if cert.ssl_type == SUPPORTED_SSL_TYPES.custom:
            candidate = services_dir / "nginx-proxy" / "ssl" / "custom" / cert.domain / "ca.pem"
            if candidate.exists():
                sources.append(candidate)

    return sources


def resolve_ca_trust(bench_path: Path, bench_config: BenchConfig, services_dir: Path) -> Path | None:
    """The single host file this bench's containers should mount for outbound CA trust.

    None when the bench trusts nothing extra (no dev certificate, no `--custom --ca` certificate)
    -- callers must not mount anything or set the trust env vars in that case. One source: that
    source's own path, unchanged. Two or more: concatenated into a bench-local bundle, rewritten
    on every call so a removed or rotated source drops out of it.
    """
    sources = collect_ca_sources(bench_config, services_dir)
    if not sources:
        return None
    if len(sources) == 1:
        return sources[0]

    bundle_path = bench_path / _BUNDLE_RELATIVE_PATH
    bundle_path.parent.mkdir(parents=True, exist_ok=True)
    bundle_path.write_bytes(b"".join(source.read_bytes() for source in sources))
    return bundle_path


def strip_managed_ca_volumes(volumes: list["DockerVolumeMount"]) -> list["DockerVolumeMount"]:
    """Drop any fm-managed CA-trust mount -- current name or the pre-rename legacy one -- from a
    service's volume list.

    Must run before every append, not just once, and regardless of whether a fresh mount is about
    to replace it: `BenchDockerOps.generate_compose` mutates the compose file loaded from disk
    rather than rebuilding it from the template (unlike `BenchWorkers.generate_compose`, which
    reloads the raw template on every call and is naturally immune to this), so an unconditional
    append on every regen grows a fresh duplicate mount forever, and an upgraded bench's old
    `fm-dev-ca.pem` entry is never removed at all -- confirmed by regenerating the same compose
    three times over and watching the volume list grow.
    """
    return [v for v in volumes if str(v.container) not in _MANAGED_CONTAINER_CA_PATHS]
