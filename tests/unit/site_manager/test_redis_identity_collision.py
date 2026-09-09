"""`BenchSiteManager.check_redis_identity_collision`: the create-time wiring around
`compose_shape.redis_server_identity` (the identity logic itself is tested in
`test_compose_shape.py`, against a fake `Runner` -- nothing here duplicates that).

This file proves the WIRING: no external `[redis]` is a no-op, a SAME verdict refuses the
create with a `BenchOperationException`, an UNKNOWN verdict never refuses and logs at DEBUG
(never a warning, never stdout), and the container command actually goes through
`_container_run` -> `docker_client.compose.exec`, the same seam every other readiness check
in this module uses.
"""

import json
from unittest.mock import MagicMock

import pytest

from frappe_manager.docker import DockerException
from frappe_manager.docker.subprocess_output import SubprocessOutput
from frappe_manager.site_manager.bench_config import RedisConfig
from frappe_manager.site_manager.exceptions import BenchOperationException
from frappe_manager.site_manager.modules.bench_site import BenchSiteManager
from frappe_manager.site_manager.modules.compose_shape import REDIS_IDENTITY_MARKER

BENCH_NAME = "shop"
# Measured live (see compose_shape.validate_redis_endpoints's docstring): this IP IS cache-box,
# so a hostname-string comparison would never catch this pair -- only the identity check can.
CACHE_URL = "redis://cache-box:6379/0"
QUEUE_URL = "redis://10.2.0.19:6379/0"


def _manager(redis_config: RedisConfig | None) -> BenchSiteManager:
    """A BenchSiteManager with the REAL `_container_run`, stopped at the compose seam."""
    manager = object.__new__(BenchSiteManager)  # bypass __init__ (no Docker, no services stack)
    manager.bench_name = BENCH_NAME
    manager.docker_client = MagicMock()
    manager.output = MagicMock()
    manager.logger = MagicMock()
    manager.bench_config = MagicMock()
    manager.bench_config.redis = redis_config
    return manager


def _reply(run_ids: list) -> SubprocessOutput:
    text = f"{REDIS_IDENTITY_MARKER} " + json.dumps({"run_ids": run_ids})
    return SubprocessOutput(stdout=[text], stderr=[], combined=[text], exit_code=0)


def test_no_external_redis_is_a_noop():
    """fm's own `redis-cache`/`redis-queue` containers are distinct compose services and can
    never collide with each other, so a bench with no `[redis]` never execs at all."""
    manager = _manager(None)
    manager.check_redis_identity_collision()
    manager.docker_client.compose.exec.assert_not_called()


def test_same_run_id_and_index_refuses_the_create():
    manager = _manager(RedisConfig(cache=CACHE_URL, queue=QUEUE_URL))
    manager.docker_client.compose.exec.return_value = _reply(["run-id-abc", "run-id-abc"])

    with pytest.raises(BenchOperationException, match="same live server") as excinfo:
        manager.check_redis_identity_collision()

    assert BENCH_NAME in str(excinfo.value)
    assert "delete_keys" in str(excinfo.value)


def test_different_run_ids_proceed_without_a_warning_or_a_debug_log():
    manager = _manager(RedisConfig(cache=CACHE_URL, queue=QUEUE_URL))
    manager.docker_client.compose.exec.return_value = _reply(["run-id-abc", "run-id-xyz"])

    manager.check_redis_identity_collision()  # must not raise

    manager.logger.debug.assert_not_called()
    manager.output.warning.assert_not_called()


def test_an_unknown_verdict_never_refuses_and_logs_at_debug_only():
    """A managed provider or a proxy restricting `INFO`: nothing the operator can act on, so
    this is a DEBUG log through the component logger, never `output.warning` (never stdout)."""
    manager = _manager(RedisConfig(cache=CACHE_URL, queue=QUEUE_URL))
    manager.docker_client.compose.exec.return_value = _reply([None, "run-id-xyz"])

    manager.check_redis_identity_collision()  # must not raise

    assert manager.logger.debug.called
    manager.output.warning.assert_not_called()
    manager.output.print.assert_not_called()


def test_a_nonzero_exec_exit_is_unknown_and_still_proceeds():
    """The container-side exec itself failing (not just the redis connection inside it) is
    exactly as unanswerable as a garbled reply, and must not refuse the create either."""
    manager = _manager(RedisConfig(cache=CACHE_URL, queue=QUEUE_URL))
    manager.docker_client.compose.exec.side_effect = DockerException(
        ["docker", "compose", "exec"],
        SubprocessOutput(stdout=[], stderr=["boom"], combined=["boom"], exit_code=1),
    )

    manager.check_redis_identity_collision()  # must not raise

    assert manager.logger.debug.called


def test_the_collision_check_never_writes_only_execs_info():
    """No sentinel-key write: the one container command issued is built by
    `compose_shape.redis_identity_command`, already pinned (in `test_compose_shape.py`) to
    issue only `INFO`. Here: confirm exactly one exec call is made for the check."""
    manager = _manager(RedisConfig(cache=CACHE_URL, queue=QUEUE_URL))
    manager.docker_client.compose.exec.return_value = _reply(["a", "b"])

    manager.check_redis_identity_collision()

    assert manager.docker_client.compose.exec.call_count == 1
    kwargs = manager.docker_client.compose.exec.call_args.kwargs
    assert "-c" in kwargs["command"]
    assert "env/bin/python" in kwargs["command"]
