"""`execute_command` defaults the exec user to `frappe`, and ONLY for the frappe service.

The guard under test is `if compose_service == "frappe" and not user`. It decides two
things that are easy to get wrong in opposite directions:

* frappe service, no explicit user -> run as `frappe`. Without it docker exec falls back
  to the image's default user (root), and every file a bench command writes into the
  workspace ends up root-owned on the host.
* any other service (mariadb, redis-cache, ...) -> no `user` kwarg at all. Those images
  have no `frappe` account, so forcing one makes `docker compose exec` fail outright.

An explicitly requested user is never overridden, in either direction.
"""

from unittest.mock import MagicMock

from frappe_manager.site_manager.modules.bench_docker import BenchDockerOps


def _ops(service: str) -> BenchDockerOps:
    """A BenchDockerOps whose only live collaborator is a mocked compose wrapper."""
    ops = BenchDockerOps.__new__(BenchDockerOps)
    ops.docker_client = MagicMock()
    ops.output = MagicMock()
    ops.docker_client.compose.get_all_services_status.return_value = [{"Service": service, "State": "running"}]
    result = MagicMock()
    result.stdout = []
    result.stderr = []
    result.exit_code = 0
    ops.docker_client.compose.exec.return_value = result
    ops.docker_client.compose.run.return_value = result
    return ops


def _exec_kwargs(ops: BenchDockerOps) -> dict:
    return ops.docker_client.compose.exec.call_args.kwargs


class TestExecuteCommandDefaultUser:
    def test_frappe_service_without_a_user_runs_as_frappe(self):
        ops = _ops("frappe")

        assert ops.execute_command("frappe", "bench version") == 0
        assert _exec_kwargs(ops)["user"] == "frappe"

    def test_non_frappe_service_is_given_no_user_at_all(self):
        """mariadb has no `frappe` account; sending one would fail the exec."""
        ops = _ops("mariadb")

        assert ops.execute_command("mariadb", "mysql --version") == 0
        assert "user" not in _exec_kwargs(ops)

    def test_explicit_user_wins_over_the_frappe_default(self):
        ops = _ops("frappe")

        ops.execute_command("frappe", "chown -R frappe:frappe .", user="root")

        assert _exec_kwargs(ops)["user"] == "root"

    def test_explicit_user_is_honoured_on_a_non_frappe_service(self):
        ops = _ops("redis-cache")

        ops.execute_command("redis-cache", "redis-cli ping", user="redis")

        assert _exec_kwargs(ops)["user"] == "redis"
