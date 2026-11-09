"""Whether the compose fm writes for a bench is actually valid to Docker, and whether the
bind sources it names exist on the host before `compose up` can turn a missing one into a
root-owned DIRECTORY (`sites/apps.txt`, `sites/common_site_config.json` are meant to be
FILES). The unit suite replaces every docker call with a stand-in and has never asked Docker
either question.
"""

import contextlib
import subprocess
from pathlib import Path

import pytest

from frappe_manager.docker.compose_file import ComposeFile
from frappe_manager.site_manager.bench_config import (
    BenchConfig,
    BenchRuntime,
    DatabaseConfig,
    Deployment,
    Deployments,
    FMBenchEnvType,
    RedisConfig,
    RestartPolicyEnum,
    SiteConfig,
)
from frappe_manager.site_manager.modules.bench_docker import BenchDockerOps
from frappe_manager.site_manager.modules.compose_shape import data_binds

pytestmark = [pytest.mark.docker, pytest.mark.timeout(600)]


def _config(
    tmp_path: Path,
    *,
    sites: list[str],
    runtime: BenchRuntime = BenchRuntime.mount,
    environment: FMBenchEnvType = FMBenchEnvType.prod,
    image: str | None = None,
    nginx_image: str | None = None,
    database: dict[str, DatabaseConfig] | None = None,
    redis: RedisConfig | None = None,
) -> BenchConfig:
    """A real `BenchConfig`; `name` is `sites[0]` so the primary site is never ambiguous."""
    deployments = None
    if runtime == BenchRuntime.image:
        deployments = Deployments(
            current=Deployment(
                app_image=image, nginx_image=nginx_image or image, deployed_at="t0", migrate_status="migrated"
            )
        )
    recorded = {name: SiteConfig(database=(database or {}).get(name)) for name in sites}
    return BenchConfig(
        name=sites[0],
        developer_mode=False,
        admin_tools=False,
        environment_type=environment,
        restart_policy=RestartPolicyEnum.unless_stopped,
        root_path=tmp_path / "bench_config.toml",
        runtime=runtime,
        sites=recorded,
        deployments=deployments,
        redis=redis,
    )


def _write_compose(tmp_path: Path, config: BenchConfig, docker_client) -> tuple[Path, BenchDockerOps]:
    """Render the bench's `docker-compose.yml` the way `fm create`/`fm update` do: `export_to_compose_inputs`
    into `BenchDockerOps.generate_compose` (`update.py`'s own regenerate-compose pattern)."""
    compose_path = tmp_path / "docker-compose.yml"
    ops = BenchDockerOps(docker_client, ComposeFile(compose_path), config, tmp_path)
    inputs = config.export_to_compose_inputs()
    inputs.setdefault("environment", {}).setdefault("frappe", {})["FRAPPE_ENV"] = config.environment_type.value
    ops.generate_compose(inputs)
    return compose_path, ops


def _compose_config(compose_path: Path) -> subprocess.CompletedProcess:
    return subprocess.run(  # noqa: S603
        ["docker", "compose", "-f", str(compose_path), "config", "-q"],  # noqa: S607
        capture_output=True,
        text=True,
        check=False,
    )


@pytest.mark.parametrize("environment", [FMBenchEnvType.dev, FMBenchEnvType.prod])
@pytest.mark.parametrize("site_count", [1, 2])
def test_mount_runtime_compose_is_valid_to_docker(tmp_path, docker_client, site_count, environment):
    """Docker itself accepts the mount-runtime compose fm writes, for 1/2 sites and dev/prod."""
    sites = [f"s{i}.localhost" for i in range(site_count)]
    config = _config(tmp_path, sites=sites, environment=environment)
    compose_path, _ = _write_compose(tmp_path, config, docker_client)

    result = _compose_config(compose_path)

    assert result.returncode == 0, f"{compose_path}: {result.stderr}"


@pytest.mark.parametrize("site_count", [1, 2])
def test_image_runtime_compose_is_valid_to_docker(tmp_path, docker_client, site_count):
    """Docker itself accepts the image-runtime compose fm writes (the `data_binds` projection)."""
    sites = [f"s{i}.localhost" for i in range(site_count)]
    config = _config(
        tmp_path,
        sites=sites,
        runtime=BenchRuntime.image,
        image="ghcr.io/acme/erp:v1",
        nginx_image="ghcr.io/acme/erp-nginx:v1",
    )
    compose_path, _ = _write_compose(tmp_path, config, docker_client)

    result = _compose_config(compose_path)

    assert result.returncode == 0, f"{compose_path}: {result.stderr}"


def test_external_database_and_redis_compose_is_valid_to_docker(tmp_path, docker_client):
    """An external database plus split external redis still produces a compose Docker accepts."""
    site = "s0.localhost"
    database = {site: DatabaseConfig(host="db.example.internal", name="app_prod")}
    redis = RedisConfig(cache="redis://cache.example.internal:6379/0", queue="redis://queue.example.internal:6379/0")
    config = _config(tmp_path, sites=[site], database=database, redis=redis)
    compose_path, _ = _write_compose(tmp_path, config, docker_client)

    result = _compose_config(compose_path)

    assert result.returncode == 0, f"{compose_path}: {result.stderr}"


def test_image_runtime_binds_match_data_binds_and_file_sources_predate_compose_up(
    tmp_path, docker_client, fixture_image
):
    """Image-mode binds are exactly `compose_shape.data_binds()`'s sources, and the two FILE
    sources (`sites/apps.txt`, `sites/common_site_config.json`) already exist as files -- never
    directories -- before anything could bring the stack up and have Docker default them to one."""
    sites = ["a.localhost", "b.localhost"]
    config = _config(tmp_path, sites=sites, runtime=BenchRuntime.image, image=fixture_image, nginx_image=fixture_image)
    compose_path, ops = _write_compose(tmp_path, config, docker_client)
    with contextlib.suppress(Exception):
        # `_seed_nginx_conf` copies `/etc/nginx` out of the nginx image; `fixture_image` is a bare
        # bash image with no such path. Irrelevant here: the binds/file-precreation steps under
        # test run and complete BEFORE that unrelated copy is attempted.
        ops.create_compose_dirs()

    cfm = ComposeFile(compose_path)
    raw_volumes = cfm.yml["services"]["frappe"]["volumes"]
    declared_sources = {v.split(":")[0] for v in raw_volumes if v.startswith("./")}
    expected_sources = {b.host for b in data_binds(sites)}
    assert declared_sources == expected_sources

    bench_dir = tmp_path / "workspace" / "frappe-bench"
    assert (bench_dir / "sites" / "apps.txt").is_file()
    assert (bench_dir / "sites" / "common_site_config.json").is_file()

    # The generic Docker fact the two files above are dodging: a bind source that does not
    # pre-exist is created as a root-owned DIRECTORY, not a file, once a container starts with
    # it mounted.
    missing = tmp_path / "never-created"
    subprocess.run(  # noqa: S603
        ["docker", "run", "--rm", "-v", f"{missing}:{missing}", fixture_image, "true"],  # noqa: S607
        capture_output=True,
        check=True,
    )
    assert missing.is_dir()
