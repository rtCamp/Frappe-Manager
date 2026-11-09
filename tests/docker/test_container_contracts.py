"""Two contracts that need a real daemon to prove: a readiness probe that can actually pass, and
a temporary container that never outlives its use.
"""

import subprocess
import time

import pytest
from conftest import current_containers

from frappe_manager.site_manager.modules.compose_shape import default_nginx_image

pytestmark = [pytest.mark.docker, pytest.mark.timeout(600)]

# `BenchAdminTools.wait_till_services_started` (bench_admin_tools.py:296-300) execs this binary
# inside the bench's own "nginx" compose service -- never inside adminer/mailpit -- to prove each
# admin tool answers THROUGH nginx. The image is fm's own, never a guess: same function the
# rendered compose template calls for `nginx_image_tag` (templates/docker-compose.tmpl:24).
WAIT_FOR_IT = "wait-for-it"
USAGE_ERROR_MARKERS = ("unknown argument", "usage:", "not found", "no such file")

# Comfortably below the 30s ceiling `wait_till_services_started` enforces across its whole retry
# loop, so a probe that merely runs slow (rather than hanging outright) still trips this.
TIMEOUT_CEILING_S = 15


@pytest.fixture(scope="module")
def nginx_image() -> str:
    """The real fm nginx image whose entrypoint the readiness probe runs inside."""
    image = default_nginx_image()
    pulled = subprocess.run(  # noqa: S603
        ["docker", "pull", image],  # noqa: S607
        capture_output=True,
        text=True,
        check=False,
    )
    if pulled.returncode != 0:
        pytest.skip(f"cannot pull {image}: {pulled.stderr.strip()[:300]}")
    return image


def _run_in_nginx_image(image: str, script: str) -> subprocess.CompletedProcess:
    return subprocess.run(  # noqa: S603
        ["docker", "run", "--rm", "--entrypoint", "bash", image, "-c", script],  # noqa: S607
        capture_output=True,
        text=True,
        check=False,
    )


def test_wait_for_it_is_on_path_and_understands_its_timeout_flag(nginx_image):
    """`wait-for-it -t` must exist and parse in fm's nginx image, not report a usage error."""
    result = _run_in_nginx_image(nginx_image, f"{WAIT_FOR_IT} -t 1 127.0.0.1:1")
    combined = (result.stdout + result.stderr).lower()
    assert not any(marker in combined for marker in USAGE_ERROR_MARKERS), (
        f"{WAIT_FOR_IT} -t is not usable (output={result.stdout + result.stderr!r})"
    )
    assert result.returncode != 127, f"{WAIT_FOR_IT} missing from PATH (output={result.stdout + result.stderr!r})"


def test_wait_for_it_reports_both_directions_within_its_own_timeout(nginx_image):
    """The probe must fail fast on a closed port and succeed fast on a real listener, both under -t."""
    closed_start = time.monotonic()
    closed = _run_in_nginx_image(nginx_image, f"{WAIT_FOR_IT} -t 2 127.0.0.1:59999")
    closed_elapsed = time.monotonic() - closed_start

    assert closed.returncode != 0, "wait-for-it reported success against a port nothing listens on"
    assert closed_elapsed < TIMEOUT_CEILING_S, (
        f"closed-port probe took {closed_elapsed:.1f}s against a -t 2 ceiling -- it hung instead of timing out"
    )

    open_script = f"python3 -m http.server 59998 >/tmp/srv.log 2>&1 & sleep 0.5 && {WAIT_FOR_IT} -t 2 127.0.0.1:59998"
    open_start = time.monotonic()
    opened = _run_in_nginx_image(nginx_image, open_script)
    open_elapsed = time.monotonic() - open_start

    assert opened.returncode == 0, f"wait-for-it reported failure against a real listener: {opened.stderr}"
    assert open_elapsed < TIMEOUT_CEILING_S, f"open-port probe took {open_elapsed:.1f}s instead of returning promptly"


def test_temp_container_is_removed_after_normal_use(docker_client, fixture_image, containers_before):
    """`create_temp_container` must not leave its container behind after a clean `with` exit."""
    with docker_client.create_temp_container(fixture_image) as container:
        assert container.name in current_containers()

    assert current_containers() == containers_before


def test_temp_container_is_removed_when_the_with_block_raises(docker_client, fixture_image, containers_before):
    """An exception inside the `with` block must propagate AND still trigger container cleanup."""

    class BoomError(Exception):
        pass

    def _boom():
        with docker_client.create_temp_container(fixture_image) as container:
            assert container.name in current_containers()
            raise BoomError("boom")

    with pytest.raises(BoomError):
        _boom()

    assert current_containers() == containers_before


def test_temp_container_raises_runtime_error_for_a_missing_image(docker_client, containers_before):
    """A nonexistent image must raise `RuntimeError`, not leak a container or a raw `DockerException`."""
    with pytest.raises(RuntimeError), docker_client.create_temp_container("fm-test-fixture-does-not-exist:missing"):
        pass

    assert current_containers() == containers_before
