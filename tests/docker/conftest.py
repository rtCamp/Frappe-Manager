"""Fixtures for the tier that talks to a real Docker daemon.

Everything here is skipped when no daemon answers, so a developer without Docker still
gets a green `just test`. These tests exist because the unit suite replaces every docker
call with a stand-in: it can pin the ORDER of fm's phases but never what landed on disk.
"""

import shutil
import subprocess

import pytest

from frappe_manager.docker.docker_client import DockerClient
from frappe_manager.output_manager import set_global_output_handler
from frappe_manager.output_manager.rich_output import RichOutputHandler

FIXTURE_IMAGE = "fm-test-fixture:local"

# `bash:5` and not alpine: TempContainer starts every temp container with
# `--entrypoint bash`, so an image without bash cannot be copied out of at all.
FIXTURE_DOCKERFILE = """\
FROM bash:5
RUN mkdir -p /workspace/frappe-bench/sites/assets/css \\
 && mkdir -p /workspace/frappe-bench/apps/frappe/.git \\
 && mkdir -p /workspace/frappe-bench/env/bin \\
 && mkdir -p /workspace/frappe-bench/.uv \\
 && mkdir -p /workspace/frappe-bench/.fnm/aliases \\
 && printf 'frappe\\nerpnext\\n' > /workspace/frappe-bench/sites/apps.txt \\
 && printf '{}' > /workspace/frappe-bench/sites/common_site_config.json \\
 && printf 'body{}' > /workspace/frappe-bench/sites/assets/css/app.css \\
 && printf 'ref: refs/heads/main' > /workspace/frappe-bench/apps/frappe/.git/HEAD \\
 && printf 'setup' > /workspace/frappe-bench/apps/frappe/setup.py \\
 && printf '#!/bin/sh' > /workspace/frappe-bench/env/bin/python \\
 && printf 'cpython-3.13.1' > /workspace/frappe-bench/.uv/python-default \\
 && printf 'v22.11.0' > /workspace/frappe-bench/.fnm/aliases/default
"""


def _daemon_available() -> bool:
    if shutil.which("docker") is None:
        return False
    probe = subprocess.run(
        ["docker", "version", "--format", "{{.Server.Version}}"],  # noqa: S607
        capture_output=True,
        text=True,
        check=False,
    )
    return probe.returncode == 0 and bool(probe.stdout.strip())


def pytest_collection_modifyitems(config, items):
    """Skip this directory when no daemon answers; the `docker` marker itself is declared
    module-level in each test file, because `-m "not docker"` is applied during collection
    and a marker added from here can lose that race."""
    if _daemon_available():
        return
    skip = pytest.mark.skip(reason="no Docker daemon")
    for item in items:
        if "tests/docker" in str(item.fspath).replace("\\", "/"):
            item.add_marker(skip)


@pytest.fixture(autouse=True)
def _global_output():
    """`host_run_cp` and friends call `get_global_output_handler()` unconditionally."""
    set_global_output_handler(RichOutputHandler())
    yield
    set_global_output_handler(None)


@pytest.fixture(scope="session")
def docker_client() -> DockerClient:
    return DockerClient()


@pytest.fixture(scope="session")
def fixture_image() -> str:
    """A ~15 MB stand-in for a baked app image, carrying the paths fm extracts.

    Built once per session; kept afterwards so repeat runs cost nothing.
    """
    build = subprocess.run(  # noqa: S603
        ["docker", "build", "-q", "-t", FIXTURE_IMAGE, "-"],  # noqa: S607
        input=FIXTURE_DOCKERFILE,
        capture_output=True,
        text=True,
        check=False,
    )
    if build.returncode != 0:
        pytest.skip(f"cannot build the fixture image: {build.stderr.strip()[:300]}")
    return FIXTURE_IMAGE


@pytest.fixture
def containers_before() -> list[str]:
    """Container names present before the test, for leak assertions."""
    out = subprocess.run(
        ["docker", "ps", "-a", "--format", "{{.Names}}"],  # noqa: S607
        capture_output=True,
        text=True,
        check=False,
    )
    return sorted(n for n in out.stdout.splitlines() if n.strip())


def current_containers() -> list[str]:
    out = subprocess.run(
        ["docker", "ps", "-a", "--format", "{{.Names}}"],  # noqa: S607
        capture_output=True,
        text=True,
        check=False,
    )
    return sorted(n for n in out.stdout.splitlines() if n.strip())
