"""Regression contracts for `fm self *` and `fm services *`.

These commands act on SHARED infrastructure: the mariadb + nginx-proxy stack every
bench on the host depends on, plus every bench at once. The decisions defended here are the ones
whose failure mode is "every bench on this host", not "this command misbehaved":

* `fm self stop` must actually stop a bench whose containers are up, and must tear the stack down
  in dependency order (benches, then the proxy, then the database it fronts).
* `fm self upgrade` must never offer or perform a DOWNGRADE of the CLI underneath benches whose
  on-disk state was written by a newer fm.
* `fm self compose` moved to top-level `fm compose` with the canonical bench selection; it must
  still hand docker the compose files in the order fm's own DockerComposeWrapper uses, so
  `docker-compose.override.yml` still wins.
* `fm services real-ip` writes into the LIVE proxy's conf.d: the header is validated before any
  write, and a file nginx rejects is rolled back instead of being left to break the proxy's next
  start.
* `fm services info` is where the shared root-db credentials moved when they left the bench
  card, so it must actually carry them, and it must report a service whose container does not
  exist as stopped rather than omitting it.
* `fm services migrate` is the services tier of a migration (shared services + fm config):
  it stamps the system version only on success, never targets a bench, and is a no-op when
  already current. `fm migrate` refuses while this tier is behind, so this command must
  exist and work on its own.
* `fm services start|stop <service>` confirms the work it did, not only the work it skipped, and
  `fm services shell all` is refused up front instead of failing as a bogus shell exit code.

Everything external is mocked at its seam: no docker daemon, no network, no real ~/frappe.
"""

import json
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
import typer
from typer.testing import CliRunner

from frappe_manager.commands.compose import compose
from frappe_manager.commands.self.stop import stop
from frappe_manager.commands.self.upgrade import upgrade
from frappe_manager.commands.services.info import info as services_info
from frappe_manager.commands.services.real_ip import real_ip
from frappe_manager.commands.services.shell import shell_services
from frappe_manager.commands.services.start import start_services
from frappe_manager.commands.services.stop import stop_services
from frappe_manager.docker.docker_exceptions import DockerException
from frappe_manager.docker.subprocess_output import SubprocessOutput
from frappe_manager.output_manager import get_global_output_handler
from frappe_manager.services_manager.services import ServicesManager
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

    def fail(self, name: str, exc: Exception):
        self.benches[name].stop.side_effect = exc


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
        "stop-service:nginx-proxy",
        "stop-service:mariadb",
    ]


def test_benches_only_never_touches_the_global_services(out):
    h = StopHarness()

    h.run(benches_only=True)

    assert h.calls == ["stop-bench:vtest.localhost"]


def test_global_only_never_touches_the_benches(out):
    h = StopHarness()

    h.run(global_only=True)

    assert h.calls == ["stop-service:nginx-proxy", "stop-service:mariadb"]


def test_an_already_stopped_service_is_still_skipped(out):
    h = StopHarness(running_services=False)

    h.run(global_only=True)

    assert h.calls == []
    assert "Skipping already stopped service mariadb" in joined(out.print)


def test_a_bench_that_could_not_be_stopped_makes_the_command_exit_nonzero(out):
    """The best-effort loop is right, reporting success afterwards is not: `fm self stop` used to
    warn about the bench it failed to stop and then exit 0, so a script was told the host was
    quiet while containers were still up. The same file already exits 1 for a bad flag pair."""
    h = StopHarness(bench_names=("a.localhost", "b.localhost"))
    h.fail("a.localhost", RuntimeError("compose down failed"))

    with pytest.raises(typer.Exit) as exc:
        h.run()

    assert exc.value.exit_code == 1
    # Best effort preserved: the other bench and both global services were still stopped.
    assert h.calls == [
        "stop-bench:b.localhost",
        "stop-service:nginx-proxy",
        "stop-service:mariadb",
    ]
    assert "Failed to stop a.localhost: compose down failed" in joined(out.warning)
    assert "Still running: a.localhost" in joined(out.display_error)


def test_the_failure_report_names_every_bench_still_up(out):
    h = StopHarness(bench_names=("a.localhost", "b.localhost"))
    h.fail("a.localhost", RuntimeError("boom"))
    h.fail("b.localhost", RuntimeError("boom"))

    with pytest.raises(typer.Exit):
        h.run()

    assert "Still running: a.localhost, b.localhost" in joined(out.display_error)


def test_a_clean_shutdown_reports_nothing_still_running(out):
    h = StopHarness(bench_names=("a.localhost",))

    h.run()

    out.display_error.assert_not_called()


def test_a_bench_failure_is_reported_even_with_benches_only(out):
    """--benches-only skips the global block, so the exit must not hang off it."""
    h = StopHarness(bench_names=("a.localhost",))
    h.fail("a.localhost", RuntimeError("boom"))

    with pytest.raises(typer.Exit) as exc:
        h.run(benches_only=True)

    assert exc.value.exit_code == 1


def test_global_only_cannot_fail_on_a_bench_it_never_looked_at(out):
    h = StopHarness(bench_names=("a.localhost",))
    h.fail("a.localhost", RuntimeError("boom"))

    h.run(global_only=True)

    out.display_error.assert_not_called()


# =========================================================================== #
# fm self upgrade
# =========================================================================== #


def run_upgrade(published: str, current: str, *, yes: bool = True):
    ctx = MagicMock(spec=typer.Context)
    payload = MagicMock()
    payload.text = json.dumps({"info": {"version": published}})
    with (
        patch("frappe_manager.commands.self.upgrade.requests.get", return_value=payload),
        patch("frappe_manager.commands.self.upgrade.get_current_fm_version", return_value=current),
        patch("frappe_manager.commands.self.upgrade.install_package") as install,
    ):
        upgrade(ctx, yes=yes)
    return install


def test_a_published_version_older_than_the_installed_one_is_never_installed(out):
    """D58: the test was plain string inequality, so a dev build (which is AHEAD of the published
    release) was offered the PyPI version -- and `--yes` performed that downgrade unattended."""
    install = run_upgrade(published="0.19.3", current="0.20.0.dev0")

    install.assert_not_called()
    assert "New update available" not in joined(out.print)
    assert "fm is already up to date (v0.20.0.dev0)" in joined(out.print)


def test_an_identical_version_is_not_an_update(out):
    install = run_upgrade(published="0.19.3", current="0.19.3")

    install.assert_not_called()


def test_a_newer_published_version_is_still_installed(out):
    install = run_upgrade(published="0.21.0", current="0.20.0.dev0")

    install.assert_called_once_with("frappe-manager", "0.21.0")


# =========================================================================== #
# fm compose
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
        patch("frappe_manager.commands.compose.CLI_BENCHES_DIRECTORY", tmp_path),
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


def test_compose_double_dash_shifts_the_bound_token_into_the_args_and_picks_the_bench(
    tmp_path, out, monkeypatch
):
    """`fm compose -- ps`: click consumes the `--` and binds 'ps' to BENCH, so the command must
    read argv to tell this apart from `fm compose ps`, treat 'ps' as the first docker compose
    argument, and resolve the bench the way every bench command does when the name is omitted."""
    bench_path = tmp_path / "picked.localhost"
    bench_path.mkdir()
    (bench_path / "docker-compose.yml").write_text("services: {}\n")

    ctx = MagicMock(spec=typer.Context)
    ctx.args = ["-a"]
    monkeypatch.setattr(sys, "argv", ["fm", "compose", "--", "ps", "-a"])

    with (
        patch("frappe_manager.commands.compose.CLI_BENCHES_DIRECTORY", tmp_path),
        patch(
            "frappe_manager.commands.compose.sitename_callback", return_value="picked.localhost"
        ) as resolver,
        patch("os.chdir") as chdir,
        patch("os.execvp") as execvp,
    ):
        compose(ctx, benchname="ps")

    resolver.assert_called_once_with(None)
    chdir.assert_called_once_with(bench_path)
    assert execvp.call_args.args[1] == ["docker", "compose", "-f", "docker-compose.yml", "ps", "-a"]


def test_compose_named_bench_not_found_teaches_the_double_dash_form(tmp_path, out, monkeypatch):
    """A typo'd bench must still fail loudly -- never be silently handed to docker compose --
    and the refusal names the escape hatch."""
    from frappe_manager.commands.compose import _benchname_callback
    from frappe_manager.site_manager.exceptions import BenchNotFoundError

    monkeypatch.setattr(sys, "argv", ["fm", "compose", "ps"])

    with (
        patch("frappe_manager.utils.callbacks.CLI_BENCHES_DIRECTORY", tmp_path),
        pytest.raises(BenchNotFoundError) as excinfo,
    ):
        _benchname_callback("ps")

    assert "fm compose -- ps" in str(excinfo.value)


# =========================================================================== #
# fm services real-ip
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
                ["docker", "compose", "exec", "nginx-proxy", "nginx", "-t"],
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
        service="nginx-proxy", command="nginx -t", stream=False
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
# fm services info
# =========================================================================== #


class _CardSpy:
    """Stand-in for railcard.Card recording the facts the command decided on."""

    made: list = []

    def __init__(self, name, meta, active=True, link=None):
        self.name, self.meta, self.active, self.link = name, meta, active, link
        self.rows: list[tuple[str, str]] = []
        _CardSpy.made.append(self)

    def fact(self, label, value):
        self.rows.append((label, value))
        return self

    def section(self, title):
        return self

    def render(self):
        return f"<rendered {self.name}>"

    @property
    def facts(self) -> dict:
        return dict(self.rows)


class ServicesInfoHarness:
    def __init__(self, tmp_path: Path, *, statuses=None):
        self.confd = tmp_path / "confd"
        self.confd.mkdir(parents=True)

        self.services = MagicMock(name="services_manager")
        self.services.path = tmp_path / "services"
        self.services.database_manager.database_server_info = SimpleNamespace(
            user="root", password="rootpass", host="mariadb"
        )
        self.services.proxy_storage.dirs.confd.host = str(self.confd)
        self.services.compose_file_manager.get_services_list.return_value = [
            "mariadb",
            "nginx-proxy",
        ]
        self.services.compose_file_manager.get_container_names.return_value = {
            "mariadb": "fm__mariadb",
            "nginx-proxy": "fm__nginx-proxy",
        }
        default = [
            {"Service": "mariadb", "State": "running", "Name": "fm__mariadb"},
            {"Service": "nginx-proxy", "State": "running", "Name": "fm__nginx-proxy"},
        ]
        self.services.docker_client.compose.get_all_services_status.return_value = (
            default if statuses is None else statuses
        )

        self.ctx = MagicMock(spec=typer.Context)
        self.ctx.obj = {"services": self.services}

    def run(self, monkeypatch) -> _CardSpy:
        from frappe_manager.output_manager import railcard

        _CardSpy.made = []
        monkeypatch.setattr(railcard, "Card", _CardSpy)
        services_info(self.ctx)
        (card,) = _CardSpy.made
        return card


def test_services_info_carries_the_root_db_credentials(tmp_path, out, monkeypatch):
    """The row moved off the bench card, so this card is now the ONLY place fm prints the
    shared mariadb root credentials."""
    h = ServicesInfoHarness(tmp_path)

    card = h.run(monkeypatch)

    assert card.facts["root db"] == (
        "root [fm.muted]/[/fm.muted] [fm.secret]rootpass[/fm.secret] [fm.muted]@[/fm.muted] mariadb"
    )
    assert card.active


def test_services_info_summarizes_an_active_realip_conf(tmp_path, out, monkeypatch):
    h = ServicesInfoHarness(tmp_path)
    (h.confd / "fm-real-ip.conf").write_text(
        build_proxy_realip_conf(["203.0.113.0/24", "2400:cb00::/32"], "CF-Connecting-IP", recursive=True)
    )

    card = h.run(monkeypatch)

    assert card.facts["real-ip"] == "trusting 2 range(s), restoring client IP from CF-Connecting-IP"


def test_services_info_never_describes_a_foreign_conf_as_fms(tmp_path, out, monkeypatch):
    """A hand-written fm-real-ip.conf (no fm marker) must read as not configured, the same
    ownership rule every fm-managed nginx file follows."""
    h = ServicesInfoHarness(tmp_path)
    (h.confd / "fm-real-ip.conf").write_text("set_real_ip_from 10.0.0.0/8;\n")

    card = h.run(monkeypatch)

    assert "not configured" in card.facts["real-ip"]


def test_services_info_reports_a_missing_container_as_stopped(tmp_path, out, monkeypatch):
    """get_all_services_status only reports containers that exist, so a never-created service
    would otherwise silently vanish from the card."""
    h = ServicesInfoHarness(
        tmp_path, statuses=[{"Service": "mariadb", "State": "running", "Name": "fm__mariadb"}]
    )

    card = h.run(monkeypatch)

    assert "stopped:[/fm.muted] nginx-proxy" in card.facts["global"]
    assert not card.active


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

    start_services(ctx, ServicesEnum.mariadb)

    services.start_service.assert_called_once_with(services=["mariadb"])
    assert "Started service mariadb" in joined(out.print)


def test_stopping_a_running_service_confirms_the_work(out):
    from frappe_manager.services_manager import ServicesEnum

    ctx, services = make_services_ctx(running=True)

    stop_services(ctx, ServicesEnum.mariadb)

    services.stop_service.assert_called_once_with(services=["mariadb"])
    assert "Stopped service mariadb" in joined(out.print)


def test_a_no_op_start_still_says_it_skipped(out):
    from frappe_manager.services_manager import ServicesEnum

    ctx, services = make_services_ctx(running=True)

    start_services(ctx, ServicesEnum.mariadb)

    services.start_service.assert_not_called()
    assert "Skipping already running service mariadb" in joined(out.print)


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


def test_services_shell_propagates_the_containers_exit_code():
    """`fm shell` for a bench execs into the container and propagates its status. The global
    services shell printed the code and exited 0, so the two disagreed and no script could branch
    on it. Driven through the REAL ServicesManager.shell over a mocked docker client."""
    app = typer.Typer()
    app.command("shell")(shell_services)

    manager = ServicesManager(path=Path("/nonexistent"), output_handler=get_global_output_handler())
    manager.docker_client = MagicMock(name="docker_client")
    manager.docker_client.compose.exec.side_effect = DockerException(
        ["docker", "compose", "exec"],
        SubprocessOutput(stdout=[], stderr=[], combined=[], exit_code=130),
    )

    @app.callback()
    def _with_ctx(ctx: typer.Context):
        ctx.obj = {"services": manager}

    result = runner.invoke(app, ["shell", "mariadb"])

    assert result.exit_code == 130
    manager.docker_client.compose.exec.assert_called_once_with("mariadb", command="/bin/bash", capture_output=False)


# =========================================================================== #
# fm services migrate
# =========================================================================== #


def _run_services_migrate(*, system_version="0.20.0", current_version="0.21.0", execute_result=True, **kwargs):
    from frappe_manager.commands.services.migrate import migrate_services
    from frappe_manager.migration_manager.version import Version

    fm_config_manager = MagicMock()
    fm_config_manager.get_system_migration_version.return_value = Version(system_version)

    ctx = MagicMock(spec=typer.Context)
    ctx.obj = {"fm_config_manager": fm_config_manager}

    with (
        patch("frappe_manager.commands.services.migrate.get_current_fm_version", return_value=current_version),
        patch("frappe_manager.commands.services.migrate.MigrationExecutor") as executor_cls,
        patch("frappe_manager.commands.services.migrate.spinner"),
    ):
        executor_cls.return_value.execute.return_value = execute_result
        try:
            migrate_services(ctx, **{"auto_proceed": False, "rerun": False, **kwargs})
            raised = None
        except typer.Exit as exc:
            raised = exc
    return SimpleNamespace(executor_cls=executor_cls, fm_config_manager=fm_config_manager, exit=raised)


def test_services_migrate_runs_the_services_tier_only_and_never_stamps_itself(out):
    """The mocked executor is the sole stamper of the services-tier ledger (its real
    finalize_success). A stamp observed here would be the command's own duplicate write."""
    r = _run_services_migrate()

    kwargs = r.executor_cls.call_args.kwargs
    assert kwargs["migrate_global_services"] is True
    assert kwargs["target_benches"] is None
    assert r.exit is None
    r.fm_config_manager.set_system_migration_version.assert_not_called()
    r.fm_config_manager.export_to_toml.assert_not_called()


def test_services_migrate_is_a_noop_when_already_current(out):
    r = _run_services_migrate(system_version="0.21.0", current_version="0.21.0")

    assert r.exit.exit_code == 0
    r.executor_cls.assert_not_called()
    r.fm_config_manager.set_system_migration_version.assert_not_called()


def test_services_migrate_rerun_runs_even_when_current(out):
    r = _run_services_migrate(system_version="0.21.0", current_version="0.21.0", rerun=True)

    assert r.exit is None
    assert r.executor_cls.call_args.kwargs["rerun"] is True


def test_services_migrate_failure_exits_without_stamping_the_version(out):
    """A failed tier migration must leave the version ledger behind, or the gate and
    fm migrate would treat a half-cut install as current."""
    r = _run_services_migrate(execute_result=False)

    assert r.exit.exit_code == 1
    r.fm_config_manager.set_system_migration_version.assert_not_called()


def test_services_migrate_hands_the_kind_scoped_backup_flags_through(out):
    """--skip-db-backup alone is the flag the motivating case needs: skip the huge engine
    dump while KEEPING the near-free config backups the rollback restores. The command's
    only job is to hand each kind through unchanged."""
    r = _run_services_migrate(skip_db_backup=True, skip_config_backup=False)

    kwargs = r.executor_cls.call_args.kwargs
    assert kwargs["skip_db_backup"] is True
    assert kwargs["skip_config_backup"] is False
    assert kwargs["skip_backup"] is False
