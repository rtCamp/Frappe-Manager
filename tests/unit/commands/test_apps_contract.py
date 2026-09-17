"""Characterization tests for `fm apps add` / `fm apps list`.

`fm apps add` replaces the old `fm update --apps` block: it fetches app code onto a bench and,
depending on the address, installs it into nothing (bare BENCH), one site (BENCH/SITE) or every
site (BENCH/all). It also introduces a drain gate ahead of `bench migrate`, reusing
`DeployOrchestrator.drain_workers`/`resume_workers` the same way `fm restart` does.

What is pinned here:

* the drain gate runs BEFORE `graft_apps` (before any mutation), and a timeout aborts having
  changed nothing;
* `DrainUnavailable` (an image with no fmx) is a warning, not a timeout, and the command proceeds
  undrained;
* `--no-drain` skips the gate outright and warns about the SIGUSR1/kill_timeout fallback;
* a bare-BENCH address fetches code only and does NOT restart (the bug the noun-group redesign
  fixes: the old `fm update --apps` restarted unconditionally even when nothing was installed);
* `BENCH/SITE` installs, migrates and restarts;
* `BENCH/all` reports a failing site and continues to the rest, exiting non-zero;
* an image-runtime bench is redirected to `fm bake` / `fm switch` instead of running at all.
"""

from contextlib import ExitStack
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
import typer

from frappe_manager.commands.apps.add import add_apps
from frappe_manager.commands.apps.list import list_apps
from frappe_manager.output_manager import set_global_output_handler
from frappe_manager.output_manager.base import OutputHandler
from frappe_manager.site_manager.bench_config import BenchRuntime
from frappe_manager.site_manager.exceptions import BenchNotRunning
from frappe_manager.site_manager.modules.deploy_orchestrator import DrainUnavailable

pytestmark = pytest.mark.timeout(15)

BENCH = "mybench.localhost"


class AppsWorld:
    """Drives the real ``add_apps``/``list_apps`` with every collaborator replaced at its seam."""

    def __init__(self, stack: ExitStack) -> None:
        self.output = MagicMock(spec=OutputHandler)
        set_global_output_handler(self.output)

        self.services = MagicMock(name="services_manager")

        self.bench = MagicMock(name="Bench")
        self.bench.name = BENCH
        self.bench.path = MagicMock()
        # Default: no workspace on disk, so `apps.txt` reads degrade gracefully unless a test
        # opts in and flips `.exists.return_value`.
        (self.bench.path / "workspace" / "frappe-bench" / "sites" / "apps.txt").exists.return_value = False
        self.bench.running = True

        cfg = self.bench.bench_config
        cfg.runtime = BenchRuntime.mount
        cfg.site_names = ["site-a", "site-b"]
        cfg.workers = None
        cfg.apps_list = []

        app_manager = self.bench.app_manager
        app_manager.bench_cli_cmd = ["/opt/bench"]
        app_manager.graft_apps.return_value = (["erpnext"], None)
        app_manager.install_app_to_site = MagicMock()
        app_manager._container_run = MagicMock()

        self.bench.restart_web_containers_services = MagicMock()
        self.bench.restart_workers_containers_services = MagicMock()

        bench_cls = MagicMock(name="Bench class")
        bench_cls.get_object.return_value = self.bench
        self.bench_cls = bench_cls

        self.check_migration = MagicMock(name="check_bench_migration_required")

        self.orchestrator = MagicMock(name="DeployOrchestrator instance")
        self.orchestrator.drain_workers.return_value = True
        self.orchestrator.workers_config.drain_timeout = 300
        orchestrator_cls = MagicMock(name="DeployOrchestrator class", return_value=self.orchestrator)
        self.orchestrator_cls = orchestrator_cls
        p = stack.enter_context
        p(patch("frappe_manager.commands.apps.add.Bench", bench_cls))
        p(patch("frappe_manager.commands.apps.add.check_bench_migration_required", self.check_migration))
        p(patch("frappe_manager.commands.apps.add.DeployOrchestrator", orchestrator_cls))

    # -- observation ---------------------------------------------------

    @property
    def errors(self) -> list[str]:
        return [c.args[0] for c in self.output.display_error.call_args_list if c.args]

    @property
    def warnings(self) -> list[str]:
        return [c.args[0] for c in self.output.warning.call_args_list if c.args]

    @property
    def prints(self) -> list[str]:
        return [c.args[0] for c in self.output.print.call_args_list if c.args]

    # -- run -------------------------------------------------------------

    def run(self, *, site: str | None = None, apps=None, **kwargs):
        ctx = MagicMock(spec=typer.Context)
        ctx.obj = {"services": self.services, "site": site}
        apps = apps if apps is not None else [SimpleNamespace(name="erpnext", repo="frappe/erpnext", ref=None)]
        return add_apps(ctx, address=BENCH, apps=apps, **kwargs)


@pytest.fixture
def world():
    with ExitStack() as stack:
        yield AppsWorld(stack)


class TestDrainGate:
    def test_drain_runs_before_graft(self, world):
        """The gate is entered before any mutation: drain_workers fires before graft_apps."""
        order: list[str] = []
        world.orchestrator.drain_workers.side_effect = lambda: order.append("drain") or True
        world.bench.app_manager.graft_apps.side_effect = lambda *a, **k: order.append("graft") or (["erpnext"], None)

        world.run(site="site-a")

        assert order == ["drain", "graft"]

    def test_timeout_aborts_before_any_mutation(self, world):
        """A drain timeout resumes the workers, changes nothing, and pins the wording."""
        world.orchestrator.drain_workers.return_value = False

        with pytest.raises(typer.Exit) as exc:
            world.run(site="site-a")

        assert exc.value.exit_code == 1
        assert len(world.errors) == 1
        assert "Nothing was changed" in world.errors[0]
        assert "Drain timed out after 300s" in world.errors[0]
        world.orchestrator.resume_workers.assert_called_once()
        world.bench.app_manager.graft_apps.assert_not_called()
        world.bench.restart_web_containers_services.assert_not_called()

    def test_drain_unavailable_proceeds_with_a_warning(self, world):
        """An image with no fmx cannot be drained; that is not a timeout, so it just warns and continues."""
        world.orchestrator.drain_workers.side_effect = DrainUnavailable("no fmx in this image.")

        world.run(site="site-a")

        assert world.errors == []
        assert any("no fmx in this image" in w for w in world.warnings)
        world.bench.app_manager.graft_apps.assert_called_once()
        # Never suspended, so there is nothing to resume.
        world.orchestrator.resume_workers.assert_not_called()

    def test_no_drain_skips_the_gate_and_warns(self, world):
        world.run(site="site-a", drain=False)

        world.orchestrator.drain_workers.assert_not_called()
        assert any("WITHOUT draining" in w and "SIGUSR1" in w for w in world.warnings)
        world.bench.app_manager.graft_apps.assert_called_once()

    def test_successful_drain_resumes_after_the_work(self, world):
        world.run(site="site-a")

        world.orchestrator.drain_workers.assert_called_once()
        world.orchestrator.resume_workers.assert_called_once()


class TestAddressScope:
    def test_bare_bench_fetches_code_and_does_not_restart(self, world):
        """The bug fix: a bare BENCH used to restart unconditionally even though nothing installs."""
        world.run(site=None)

        world.bench.app_manager.graft_apps.assert_called_once()
        world.bench.app_manager.install_app_to_site.assert_not_called()
        world.bench.app_manager._container_run.assert_not_called()
        world.bench.restart_web_containers_services.assert_not_called()
        world.bench.restart_workers_containers_services.assert_not_called()
        assert any("Install it with 'fm apps add" in p for p in world.prints)

    def test_one_site_installs_migrates_and_restarts(self, world):
        world.run(site="site-a")

        world.bench.app_manager.install_app_to_site.assert_called_once_with("erpnext", site_name="site-a")
        world.bench.app_manager._container_run.assert_called_once()
        migrate_cmd = world.bench.app_manager._container_run.call_args[0][0]
        assert "site-a" in migrate_cmd and "migrate" in migrate_cmd
        world.bench.restart_web_containers_services.assert_called_once_with(use_container_restart=False)
        world.bench.restart_workers_containers_services.assert_called_once_with(use_container_restart=False)
        assert any("Grafted apps applied to site-a" in p for p in world.prints)

    def test_all_continues_past_a_failing_site_and_exits_nonzero(self, world):
        """`BENCH/all` reports a failing site and still finishes the rest, then exits non-zero."""

        def install(app_name, site_name):
            if site_name == "site-a":
                raise RuntimeError("boom")

        world.bench.app_manager.install_app_to_site.side_effect = install

        with pytest.raises(typer.Exit) as exc:
            world.run(site="all")

        assert exc.value.exit_code == 1
        # Both sites were attempted -- the failure on site-a did not stop site-b.
        install_calls = world.bench.app_manager.install_app_to_site.call_args_list
        assert {c.kwargs["site_name"] for c in install_calls} == {"site-a", "site-b"}
        assert any("site-a: boom" in w for w in world.warnings)
        assert any("Apps grafted, but these sites failed: site-a" in e for e in world.errors)
        # The site that succeeded still got its restart -- failures are reported, not fatal.
        world.bench.restart_web_containers_services.assert_called_once()

    def test_replacing_an_existing_app_grafts_but_installs_nothing(self, world):
        """`graft_apps` reports only NEWLY added apps; a replaced app is already on the site, and the
        stashed old code is called out so the operator knows to clean it up."""
        world.bench.app_manager.graft_apps.return_value = ([], "/benches/x/apps.stash")

        world.run(site="all")

        world.bench.app_manager.install_app_to_site.assert_not_called()
        assert world.warnings[0] == "Replaced app code moved to /benches/x/apps.stash -- review and delete it."


class TestImageRuntimeRedirect:
    def test_image_runtime_redirects_to_bake_and_switch(self, world):
        world.bench.bench_config.runtime = BenchRuntime.image

        with pytest.raises(typer.Exit) as exc:
            world.run(site="site-a")

        assert exc.value.exit_code == 1
        assert len(world.errors) == 1
        assert "fm bake" in world.errors[0]
        assert "fm switch" in world.errors[0]
        world.bench.app_manager.graft_apps.assert_not_called()
        world.orchestrator.drain_workers.assert_not_called()


class TestBenchMustBeRunning:
    def test_stopped_bench_refuses(self, world):
        world.bench.running = False

        with pytest.raises(BenchNotRunning) as exc:
            world.run(site="site-a")

        assert exc.value.bench_name == BENCH
        world.bench.app_manager.graft_apps.assert_not_called()


class TestListApps:
    def _ctx(self, world):
        ctx = MagicMock(spec=typer.Context)
        ctx.obj = {"services": world.services}
        return ctx

    def test_lists_recorded_and_installed_apps(self, world, capsys):
        world.bench.bench_config.apps_list = [
            SimpleNamespace(name="frappe", repo="frappe/frappe", ref="version-15"),
            SimpleNamespace(name="erpnext", repo="frappe/erpnext", ref=None),
        ]
        apps_txt = world.bench.path / "workspace" / "frappe-bench" / "sites" / "apps.txt"
        apps_txt.exists.return_value = True
        apps_txt.read_text.return_value = "frappe\nerpnext\n"

        with (
            patch("frappe_manager.commands.apps.list.Bench", world.bench_cls),
            patch("frappe_manager.commands.apps.list.check_bench_migration_required", world.check_migration),
        ):
            list_apps(self._ctx(world), benchname=BENCH)

        out = capsys.readouterr().out
        assert "frappe   frappe/frappe:version-15" in out
        assert "erpnext  frappe/erpnext:default" in out
        assert "installed on disk" in out
        assert "erpnext" in out

    def test_degrades_gracefully_with_no_workspace(self, world, capsys):
        world.bench.bench_config.apps_list = []

        with (
            patch("frappe_manager.commands.apps.list.Bench", world.bench_cls),
            patch("frappe_manager.commands.apps.list.check_bench_migration_required", world.check_migration),
        ):
            list_apps(self._ctx(world), benchname=BENCH)

        out = capsys.readouterr().out
        assert "(none)" in out
        assert "no workspace on disk (image runtime)" in out


class TestRequiredAppsArgument:
    def test_the_variadic_apps_argument_is_required_at_the_parser(self):
        """`fm apps add BENCH` with no APP:REF must be a usage error, not a silent success:
        before this pin, an empty invocation drained the workers, grafted nothing, and
        restarted services to install zero apps."""
        import typer.main as typer_main

        from frappe_manager.commands.apps import apps_app

        click_group = typer_main.get_command(apps_app)
        add_cmd = click_group.commands["add"]
        apps_param = next(p for p in add_cmd.params if p.name == "apps")
        assert apps_param.required is True
