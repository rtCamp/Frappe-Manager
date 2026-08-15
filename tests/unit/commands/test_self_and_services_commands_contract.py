"""Regression contracts for `fm self *` and `fm services *`.

These commands act on SHARED infrastructure: the global-db + global-nginx-proxy stack every
bench on the host depends on, plus every bench at once. The decisions defended here are the ones
whose failure mode is "every bench on this host", not "this command misbehaved":

* `fm self stop` must actually stop a bench whose containers are up, and must tear the stack down
  in dependency order (benches, then the proxy, then the database it fronts).
* `fm self update` must never offer or perform a DOWNGRADE of the CLI underneath benches whose
  on-disk state was written by a newer fm.
* `fm self compose` must hand docker the compose files in the order fm's own
  DockerComposeWrapper uses, so `docker-compose.override.yml` still wins.
* `fm self real-ip` writes into the LIVE proxy's conf.d: the header is validated before any
  write, and a file nginx rejects is rolled back instead of being left to break the proxy's next
  start.
* `fm services start|stop <service>` confirms the work it did, not only the work it skipped, and
  `fm services shell all` is refused up front instead of failing as a bogus shell exit code.

Everything external is mocked at its seam: no docker daemon, no network, no real ~/frappe.
"""

import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
import typer
from typer.testing import CliRunner

from frappe_manager.commands.self.compose import compose
from frappe_manager.commands.self.real_ip import real_ip
from frappe_manager.commands.self.stop import stop
from frappe_manager.commands.self.update import update
from frappe_manager.commands.services.shell import shell_services
from frappe_manager.commands.services.start import start_services
from frappe_manager.commands.services.stop import stop_services
from frappe_manager.docker.docker_exceptions import DockerException
from frappe_manager.docker.subprocess_output import SubprocessOutput
from frappe_manager.output_manager import get_global_output_handler
from frappe_manager.site_manager.modules.realip import build_proxy_realip_conf

runner = CliRunner()


@pytest.fixture
def out():
    """Record what the command reported.

    tests/unit/conftest.py installs a real RichOutputHandler globally; patching the instance's
    sinks keeps `error()`'s raise-the-exception behaviour intact.
    """
    handler = get_global_output_handler()
    with (
        patch.object(handler, "print") as p,
        patch.object(handler, "warning") as w,
        patch.object(handler, "display_error") as e,
        patch.object(handler, "stop"),
        patch.object(handler, "change_head"),
    ):
        yield SimpleNamespace(print=p, warning=w, display_error=e, handler=handler)


def texts(mock) -> list[str]:
    return [c.args[0] if c.args else c.kwargs.get("text", "") for c in mock.call_args_list]


def joined(mock) -> str:
    return "\n".join(texts(mock))


def docker_failure(command: list[str], stderr: str) -> DockerException:
    return DockerException(command, SubprocessOutput(stdout=[], stderr=[stderr], combined=[stderr], exit_code=1))


# =========================================================================== #
# fm self stop
# =========================================================================== #


class StopHarness:
    """`fm self stop` over a recording services manager and bench service."""

    def __init__(self, bench_names=("vtest.localhost",), running_services=True):
        self.calls: list[str] = []
        self.services = MagicMock(name="services_manager")
        self.services.is_service_running.side_effect = lambda _s: running_services
        self.services.stop_service.side_effect = lambda services: self.calls.append(f"stop-service:{services[0]}")

        self.benches = {}
        for name in bench_names:
            bench = MagicMock(name=f"bench:{name}")
            # A PARTIALLY running bench: `bench.running` is all-or-nothing over the MAIN compose
            # file only, so a crashed frappe (or surviving worker/admin-tools containers) reads
            # as False while containers are still up.
            bench.running = False
            bench.stop.side_effect = lambda n=name: self.calls.append(f"stop-bench:{n}")
            self.benches[name] = bench

        self.bench_service = MagicMock(name="bench_service")
        self.bench_service.get_bench_names.return_value = list(bench_names)
        self.bench_service.get_bench.side_effect = lambda name, **_kw: self.benches[name]

        self.ctx = MagicMock(spec=typer.Context)
        self.ctx.obj = {"services": self.services, "verbose": False}

    def run(self, **kwargs):
        with patch("frappe_manager.commands.self.stop.BenchService", return_value=self.bench_service):
            stop(self.ctx, **{"global_only": False, "benches_only": False, **kwargs})


def test_a_partially_running_bench_is_still_stopped(out):
    """D57: the dropped `if bench.running` guard was strictly narrower than `bench.stop()`.

    `bench.running` never looks at docker-compose.workers.yml or docker-compose.admin-tools.yml
    and is False as soon as one main service is not 'running', while `Bench.stop()` stops all
    three. Skipping on it left running containers behind on exactly the loaded host this command
    exists to reclaim RAM on.
    """
    h = StopHarness()

    h.run(benches_only=True)

    h.benches["vtest.localhost"].stop.assert_called_once_with()
    assert "Skipping already stopped bench vtest.localhost" not in joined(out.print)
    assert "Stopped bench vtest.localhost" in joined(out.print)


def test_shutdown_runs_benches_then_the_proxy_then_the_database(out):
    """D64: dependency order. The proxy must go down before the database it fronts, and both
    after the benches, so nothing is ever reachable-but-databaseless."""
    h = StopHarness()

    h.run()

    assert h.calls == [
        "stop-bench:vtest.localhost",
        "stop-service:global-nginx-proxy",
        "stop-service:global-db",
    ]


def test_benches_only_never_touches_the_global_services(out):
    h = StopHarness()

    h.run(benches_only=True)

    assert h.calls == ["stop-bench:vtest.localhost"]


def test_global_only_never_touches_the_benches(out):
    h = StopHarness()

    h.run(global_only=True)

    assert h.calls == ["stop-service:global-nginx-proxy", "stop-service:global-db"]


def test_an_already_stopped_service_is_still_skipped(out):
    h = StopHarness(running_services=False)

    h.run(global_only=True)

    assert h.calls == []
    assert "Skipping already stopped service global-db" in joined(out.print)


# =========================================================================== #
# fm self update
# =========================================================================== #


def run_update(published: str, current: str, *, yes: bool = True):
    ctx = MagicMock(spec=typer.Context)
    payload = MagicMock()
    payload.text = json.dumps({"info": {"version": published}})
    with (
        patch("frappe_manager.commands.self.update.requests.get", return_value=payload),
        patch("frappe_manager.commands.self.update.get_current_fm_version", return_value=current),
        patch("frappe_manager.commands.self.update.install_package") as install,
    ):
        update(ctx, yes=yes)
    return install


def test_a_published_version_older_than_the_installed_one_is_never_installed(out):
    """D58: the test was plain string inequality, so a dev build (which is AHEAD of the published
    release) was offered the PyPI version -- and `--yes` performed that downgrade unattended."""
    install = run_update(published="0.19.3", current="0.20.0.dev0")

    install.assert_not_called()
    assert "New update available" not in joined(out.print)
    assert "fm is already up to date (v0.20.0.dev0)" in joined(out.print)


def test_an_identical_version_is_not_an_update(out):
    install = run_update(published="0.19.3", current="0.19.3")

    install.assert_not_called()


def test_a_newer_published_version_is_still_installed(out):
    install = run_update(published="0.21.0", current="0.20.0.dev0")

    install.assert_called_once_with("frappe-manager", "0.21.0")


# =========================================================================== #
# fm self compose
# =========================================================================== #


def test_compose_files_are_ordered_base_first_and_override_last(tmp_path, out):
    """D63: glob-sorted order put docker-compose.yml LAST, so the base overrode the user's
    docker-compose.override.yml -- the inverse of DockerComposeWrapper's documented contract."""
    bench_path = tmp_path / "vtest.localhost"
    bench_path.mkdir()
    for name in (
        "docker-compose.yml",
        "docker-compose.override.yml",
        "docker-compose.workers.yml",
        "docker-compose.admin-tools.yml",
    ):
        (bench_path / name).write_text("services: {}\n")

    ctx = MagicMock(spec=typer.Context)
    ctx.args = ["ps"]

    with (
        patch("frappe_manager.commands.self.compose.sitename_callback", side_effect=lambda n: n),
        patch("frappe_manager.commands.self.compose.CLI_BENCHES_DIRECTORY", tmp_path),
        patch("os.chdir"),
        patch("os.execvp") as execvp,
    ):
        compose(ctx, benchname="vtest.localhost")

    argv = execvp.call_args.args[1]
    assert argv == [
        "docker",
        "compose",
        "-f",
        "docker-compose.yml",
        "-f",
        "docker-compose.workers.yml",
        "-f",
        "docker-compose.admin-tools.yml",
        "-f",
        "docker-compose.override.yml",
        "ps",
    ]


# =========================================================================== #
# fm self real-ip
# =========================================================================== #

EXISTING_CONF = "# fm-real-ip\nset_real_ip_from 10.0.0.0/8;\nreal_ip_header X-Forwarded-For;\n"


class RealIpHarness:
    def __init__(self, tmp_path: Path, *, proxy_running=True, nginx_t_fails=False, reload_ok=True):
        self.confd = tmp_path / "confd"
        self.confd.mkdir(parents=True)
        self.conf = self.confd / "fm-real-ip.conf"

        self.services = MagicMock(name="services_manager")
        self.services.proxy_storage.dirs.confd.host = str(self.confd)
        self.services.is_service_running.side_effect = lambda _s: proxy_running
        if nginx_t_fails:
            self.services.docker_client.compose.exec.side_effect = docker_failure(
                ["docker", "compose", "exec", "global-nginx-proxy", "nginx", "-t"],
                'invalid number of arguments in "deny" directive',
            )
        self.services.nginx_controller.reload.return_value = reload_ok

        self.ctx = MagicMock(spec=typer.Context)
        self.ctx.obj = {"services": self.services}

    def run(self, **kwargs):
        real_ip(
            self.ctx,
            **{"cdn": None, "trust": [], "header": None, "off": False, "status": False, **kwargs},
        )


def test_a_header_that_is_not_a_token_is_rejected_before_anything_is_written(tmp_path, out):
    """D62: --header went verbatim into `real_ip_header <value>;`, so a ';' injected arbitrary
    directives into the live proxy's conf.d."""
    h = RealIpHarness(tmp_path)

    with pytest.raises(typer.Exit):
        h.run(trust=["1.2.3.0/24"], header="X-Real-IP; deny all; #")

    assert not h.conf.exists()
    h.services.nginx_controller.reload.assert_not_called()
    assert "is not a valid header name" in joined(out.display_error)


def test_a_config_nginx_rejects_is_rolled_back_and_the_command_fails(tmp_path, out):
    """D62: nothing ran `nginx -t`, and the file stayed behind in a directory bind-mounted into
    the global proxy -- so the proxy refused to start on its next restart, taking every bench on
    the host down long after this command reported success."""
    h = RealIpHarness(tmp_path, nginx_t_fails=True)
    h.conf.write_text(EXISTING_CONF)

    with pytest.raises(typer.Exit):
        h.run(trust=["203.0.113.0/24"])

    assert h.conf.read_text() == EXISTING_CONF
    h.services.nginx_controller.reload.assert_not_called()
    assert "rolled back" in joined(out.display_error)
    assert "Real-ip active" not in joined(out.print)


def test_a_rejected_first_write_leaves_no_file_behind(tmp_path, out):
    h = RealIpHarness(tmp_path, nginx_t_fails=True)

    with pytest.raises(typer.Exit):
        h.run(trust=["203.0.113.0/24"])

    assert not h.conf.exists()


def test_a_validated_config_is_written_and_the_proxy_reloaded(tmp_path, out):
    h = RealIpHarness(tmp_path)

    h.run(trust=["203.0.113.0/24"], header="X-Forwarded-For")

    assert h.conf.read_text() == build_proxy_realip_conf(["203.0.113.0/24"], "X-Forwarded-For", recursive=True)
    h.services.docker_client.compose.exec.assert_called_once_with(
        service="global-nginx-proxy", command="nginx -t", stream=False
    )
    h.services.nginx_controller.reload.assert_called_once_with()
    assert "Real-ip active" in joined(out.print)


def test_a_failed_reload_is_not_reported_as_active(tmp_path, out):
    """D62: NginxController.reload() only warned on a persistent failure while real_ip printed
    'Real-ip active' regardless."""
    h = RealIpHarness(tmp_path, reload_ok=False)

    h.run(trust=["203.0.113.0/24"])

    assert "Real-ip active" not in joined(out.print)
    assert "did not reload" in joined(out.warning)


def test_a_stopped_proxy_is_reported_as_pending_not_active(tmp_path, out):
    """D62 (smaller hole): with the proxy down there is nothing to validate against and reload()
    is a no-op, so claiming the configuration is active is false."""
    h = RealIpHarness(tmp_path, proxy_running=False)

    h.run(trust=["203.0.113.0/24"])

    assert h.conf.exists()
    h.services.docker_client.compose.exec.assert_not_called()
    assert "Real-ip active" not in joined(out.print)
    assert "applies on next start" in joined(out.print)


# =========================================================================== #
# fm services start | stop | shell
# =========================================================================== #


def make_services_ctx(running: bool):
    services = MagicMock(name="services_manager")
    services.is_service_running.return_value = running
    ctx = MagicMock(spec=typer.Context)
    ctx.obj = {"services": services}
    return ctx, services


def test_starting_a_stopped_service_confirms_the_work(out):
    """D65: the work path printed nothing while the no-op path printed a message, so the operator
    got confirmation only when nothing happened."""
    from frappe_manager.services_manager import ServicesEnum

    ctx, services = make_services_ctx(running=False)

    start_services(ctx, ServicesEnum.global_db)

    services.start_service.assert_called_once_with(services=["global-db"])
    assert "Started service global-db" in joined(out.print)


def test_stopping_a_running_service_confirms_the_work(out):
    from frappe_manager.services_manager import ServicesEnum

    ctx, services = make_services_ctx(running=True)

    stop_services(ctx, ServicesEnum.global_db)

    services.stop_service.assert_called_once_with(services=["global-db"])
    assert "Stopped service global-db" in joined(out.print)


def test_a_no_op_start_still_says_it_skipped(out):
    from frappe_manager.services_manager import ServicesEnum

    ctx, services = make_services_ctx(running=True)

    start_services(ctx, ServicesEnum.global_db)

    services.start_service.assert_not_called()
    assert "Skipping already running service global-db" in joined(out.print)


def test_services_shell_refuses_all_instead_of_running_a_bogus_exec():
    """D66: `all` is a valid ServicesEnum value but shell has no `all` branch, so it ran
    `docker compose exec all /bin/bash` and docker's "no such service: all" was swallowed and
    reported as 'Shell exited with error code: 1', as if the shell had run."""
    app = typer.Typer()
    app.command("shell")(shell_services)

    with patch("frappe_manager.services_manager.services.ServicesManager.shell") as shell:
        result = runner.invoke(app, ["all"])

    assert result.exit_code != 0
    assert "'all' is not supported" in " ".join(result.output.split())
    shell.assert_not_called()
