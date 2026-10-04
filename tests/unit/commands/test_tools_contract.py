"""Characterization tests for `fm tools enable/disable/status`.

`fm tools` re-hosts the CLI layer of `update.py`'s old `--admin-tools` flag
(`update.py:615-712`) as its own noun group: `enable`/`disable BENCH[/SITE|all]` and a
read-only `status BENCH`. The mechanism (`Bench.admin_tools`, `sync_admin_tools_compose`,
`ensure_fm_nginx_confs`, `bench_nginx_controller`) is untouched; what is pinned here is the CLI
decision table -- which address scope does what, and in what order -- exactly as
`test_update_and_deploy_contract.py`'s `TestAdminTools`/`TestSiteScopedAdminTools` classes pinned
it for the old flag.

The group is not registered on the root app yet (wave 2 wires that), so every test calls the
command functions directly with a hand-built `ctx.obj`, the same way
`test_update_and_deploy_contract.py::UpdateWorld.run` does.
"""

from contextlib import ExitStack, contextmanager
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
import typer
from typer.testing import CliRunner

from frappe_manager.commands.tools import tools_app
from frappe_manager.commands.tools.disable import disable
from frappe_manager.commands.tools.enable import enable
from frappe_manager.commands.tools.status import status
from frappe_manager.output_manager import railcard, set_global_output_handler
from frappe_manager.output_manager.base import OutputHandler
from frappe_manager.site_manager.bench_config import AuthConfig, SiteConfig
from frappe_manager.utils.callbacks import RESERVED_BENCH_NAME

pytestmark = pytest.mark.timeout(15)

BENCH = "mybench.localhost"


@contextmanager
def _null_spinner(*_args, **_kwargs):
    yield


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


class ToolsWorld:
    """Drives `enable()`/`disable()`/`status()` with every collaborator replaced at its seam."""

    def __init__(self, tmp_path: Path, stack: ExitStack) -> None:
        self.output = MagicMock(spec=OutputHandler)
        set_global_output_handler(self.output)

        self.benches_root = tmp_path / "benches"
        self.bench_path = self.benches_root / BENCH
        self.bench_path.mkdir(parents=True)

        self.services = MagicMock(name="services_manager")

        self.bench = MagicMock(name="Bench")
        self.bench.name = BENCH
        self.bench.path = self.bench_path

        cfg = self.bench.bench_config
        cfg.admin_tools = True
        cfg.site_names = [BENCH]
        cfg.sites = {BENCH: SiteConfig()}
        cfg.auth = AuthConfig(tools=True)

        self.bench.nginx_conf_serves_per_site.return_value = True
        self.bench.admin_tools.compose_file_manager.compose_path.exists.return_value = True

        self.check_migration = MagicMock(name="check_bench_migration_required")

        bench_cls = MagicMock(name="Bench class")
        bench_cls.get_object.return_value = self.bench
        self.bench_cls = bench_cls

        p = stack.enter_context
        for module in ("enable", "disable", "status"):
            p(patch(f"frappe_manager.commands.tools.{module}.Bench", bench_cls))
            p(patch(f"frappe_manager.commands.tools.{module}.check_bench_migration_required", self.check_migration))
        p(patch("frappe_manager.commands.tools.enable.spinner", _null_spinner))
        p(patch("frappe_manager.commands.tools.disable.spinner", _null_spinner))
        p(patch("frappe_manager.commands.tools._helpers.spinner", _null_spinner))


    @property
    def config(self):
        return self.bench.bench_config

    def sites(self, names: list[str], **overrides) -> None:
        """Record `names` as the bench's sites; `overrides` apply to the LAST one."""
        self.config.site_names = names
        recorded = {name: SiteConfig() for name in names[:-1]}
        recorded[names[-1]] = SiteConfig(**overrides)
        self.config.sites = recorded


    @property
    def errors(self) -> list[str]:
        return [c.args[0] for c in self.output.display_error.call_args_list if c.args]

    @property
    def prints(self) -> list[str]:
        return [c.args[0] for c in self.output.print.call_args_list if c.args]

    @property
    def warnings(self) -> list[str]:
        return [c.args[0] for c in self.output.warning.call_args_list if c.args]

    @property
    def saves(self) -> int:
        return self.bench.save_bench_config.call_count


    def _ctx(self, site: str | None) -> typer.Context:
        ctx = MagicMock(spec=typer.Context)
        ctx.obj = {"services": self.services, "site": site}
        return ctx

    def run_enable(self, *, site: str | None = None, **kwargs):
        return enable(self._ctx(site), address=BENCH, **kwargs)

    def run_disable(self, *, site: str | None = None, **kwargs):
        return disable(self._ctx(site), address=BENCH, **kwargs)

    def run_status(self, **kwargs) -> "_CardSpy":
        """Runs `status()` with `railcard.Card` swapped for `_CardSpy` and returns the one card
        the command built."""
        _CardSpy.made = []
        with patch.object(railcard, "Card", _CardSpy):
            status(self._ctx(None), benchname=BENCH, **kwargs)
        (card,) = _CardSpy.made
        return card


@pytest.fixture
def world(tmp_path):
    with ExitStack() as stack:
        yield ToolsWorld(tmp_path, stack)


class TestEnableBench:
    def test_seeds_compose_and_mail_keys_when_absent(self, world):
        world.bench.admin_tools.compose_file_manager.compose_path.exists.return_value = False

        world.run_enable(mailpit_as_default_mail_server=True)

        assert world.config.admin_tools is True
        world.bench.sync_admin_tools_compose.assert_called_once_with()
        world.bench.admin_tools.configure_mailpit_as_default_server.assert_called_once_with()
        world.bench.admin_tools.enable.assert_not_called()
        world.bench.ensure_fm_nginx_confs.assert_called_once_with()
        assert world.saves == 1
        assert world.prints == ["Enabled Admin-tools"]

    def test_mailpit_flag_dropped_when_seeding_and_not_requested(self, world):
        world.bench.admin_tools.compose_file_manager.compose_path.exists.return_value = False

        world.run_enable(mailpit_as_default_mail_server=False)

        world.bench.admin_tools.configure_mailpit_as_default_server.assert_not_called()

    def test_existing_compose_uses_force_configure(self, world):
        world.bench.admin_tools.compose_file_manager.compose_path.exists.return_value = True

        world.run_enable(mailpit_as_default_mail_server=True)

        world.bench.admin_tools.enable.assert_called_once_with(force_configure=True)
        world.bench.sync_admin_tools_compose.assert_not_called()
        world.bench.ensure_fm_nginx_confs.assert_called_once_with()

    def test_site_scope_with_mailpit_writes_that_sites_config(self, world):
        """The flag used to be silently dropped on a site address; now it lands in the site's own
        site_config.json, after the route exists."""
        world.run_enable(site=BENCH, mailpit_as_default_mail_server=True)

        world.bench.admin_tools.configure_mailpit_for_site.assert_called_once_with(BENCH)
        world.bench.admin_tools.configure_mailpit_as_default_server.assert_not_called()

    def test_site_scope_without_the_flag_leaves_mail_alone(self, world):
        world.run_enable(site=BENCH, mailpit_as_default_mail_server=False)

        world.bench.admin_tools.configure_mailpit_for_site.assert_not_called()

    def test_all_scope_with_mailpit_is_refused_before_any_work(self, world):
        with pytest.raises(typer.Exit) as exc:
            world.run_enable(site=RESERVED_BENCH_NAME, mailpit_as_default_mail_server=True)

        assert exc.value.exit_code == 1
        assert "bench-wide setting covers every site" in world.errors[0]
        world.bench_cls.get_object.assert_not_called()
        world.bench.admin_tools.configure_mailpit_for_site.assert_not_called()


class TestEnableToolsAuthWarning:
    """`fm tools enable` routes the admin tools surface; it never decides whether that surface is
    protected. When auth does not already claim the tools surface, it tells the operator what is
    still open instead of silently protecting it -- and it must never act across the concern by
    minting credentials itself."""

    def test_warns_naming_the_bench_and_the_auth_command_when_auth_is_unset(self, world):
        world.config.auth = None

        world.run_enable()

        assert len(world.warnings) == 1
        warning = world.warnings[0]
        assert BENCH in warning
        assert "fm auth enable" in warning
        assert "--tools" in warning

    def test_warns_when_auth_exists_but_does_not_claim_tools(self, world):
        world.config.auth = AuthConfig(tools=False)

        world.run_enable()

        assert len(world.warnings) == 1
        warning = world.warnings[0]
        assert BENCH in warning
        assert "fm auth enable" in warning
        assert "--tools" in warning

    def test_silent_when_auth_already_claims_tools(self, world):
        world.config.auth = AuthConfig(tools=True)

        world.run_enable()

        world.output.warning.assert_not_called()

    def test_never_mutates_the_bench_auth_config(self, world):
        world.config.auth = AuthConfig(tools=False, user="admin", password="secret")
        before = world.config.auth.model_copy(deep=True)

        world.run_enable()

        assert world.config.auth == before


class TestDisableBench:
    def test_stops_the_pair_and_persists(self, world):
        world.run_disable()

        assert world.config.admin_tools is False
        world.bench.admin_tools.disable.assert_called_once_with()
        assert world.saves == 1
        assert world.prints == ["Disabled Admin-tools"]

    @pytest.mark.parametrize("compose_exists", [True, False], ids=["existing-compose", "no-compose"])
    def test_already_disabled_bench_reports_and_saves_nothing(self, world, compose_exists):
        world.bench.admin_tools.compose_file_manager.compose_path.exists.return_value = compose_exists
        world.config.admin_tools = False

        result = world.run_disable()

        assert result is None
        assert world.prints == ["Admin tools is already disabled"]
        world.bench.admin_tools.disable.assert_not_called()
        assert world.saves == 0

    def test_compose_missing_still_reports_already_disabled_even_if_config_says_enabled(self, world):
        """The OR condition: a missing compose file means the containers are not there to stop,
        whatever `bench_config.admin_tools` still claims."""
        world.bench.admin_tools.compose_file_manager.compose_path.exists.return_value = False
        world.config.admin_tools = True

        result = world.run_disable()

        assert result is None
        assert world.prints == ["Admin tools is already disabled"]
        world.bench.admin_tools.disable.assert_not_called()
        assert world.saves == 0

    def test_disable_never_mints_the_tools_htpasswd(self, world):
        """`ensure_fm_nginx_confs` is the enable path's job; disabling never needs a fresh htpasswd."""
        world.run_disable()

        world.bench.ensure_fm_nginx_confs.assert_not_called()


class TestSiteScopeGuards:
    def test_missing_site_is_refused(self, world):
        world.sites([BENCH])

        with pytest.raises(typer.Exit) as exc:
            world.run_enable(site="unknown.localhost")

        assert exc.value.exit_code == 1
        assert "records no entry for site" in " ".join(world.errors)
        world.bench.admin_tools.save_nginx_location_config.assert_not_called()

    def test_nginx_predating_per_site_blocks_is_refused(self, world):
        world.sites([BENCH])
        world.bench.nginx_conf_serves_per_site.return_value = False

        with pytest.raises(typer.Exit) as exc:
            world.run_enable(site=BENCH)

        assert exc.value.exit_code == 1
        assert "predates one server block per site" in " ".join(world.errors)

    def test_enabling_when_the_bench_tools_are_off_is_refused(self, world):
        world.sites([BENCH])
        world.config.admin_tools = False

        with pytest.raises(typer.Exit) as exc:
            world.run_enable(site=BENCH)

        assert exc.value.exit_code == 1
        assert "nothing to route" in " ".join(world.errors)

    def test_disabling_when_the_bench_tools_are_off_is_allowed(self, world):
        world.sites([BENCH])
        world.config.admin_tools = False

        world.run_disable(site=BENCH)

        assert world.config.sites[BENCH].serve_admin_tools is False
        world.bench.admin_tools.save_nginx_location_config.assert_called_once_with()


class TestSiteScopeRouting:
    def _sites(self, world, **overrides):
        world.sites(["shop.localhost", "b.example.com"], **overrides)
        world.config.admin_tools = True

    def test_enable_routes_only_the_named_site(self, world):
        self._sites(world)

        world.run_enable(site="b.example.com")

        assert world.config.sites["b.example.com"].serve_admin_tools is True
        assert world.config.sites["shop.localhost"].serve_admin_tools is None
        world.bench.admin_tools.save_nginx_location_config.assert_called_once_with()
        world.bench.bench_nginx_controller.reload.assert_called_once_with()
        assert world.saves == 1
        assert any("now answer" in line for line in world.prints)

    def test_disable_unroutes_only_the_named_site(self, world):
        self._sites(world)

        world.run_disable(site="b.example.com")

        assert world.config.sites["b.example.com"].serve_admin_tools is False
        assert world.config.sites["shop.localhost"].serve_admin_tools is None
        assert any("no longer answer" in line for line in world.prints)

    def test_all_fan_out_leaves_containers_running_on_disable(self, world):
        self._sites(world)

        world.run_disable(site=RESERVED_BENCH_NAME)

        assert world.config.sites["shop.localhost"].serve_admin_tools is False
        assert world.config.sites["b.example.com"].serve_admin_tools is False
        world.bench.admin_tools.disable.assert_not_called()
        assert world.config.admin_tools is True
        assert any("still running" in line for line in world.prints)

    def test_all_fan_out_clears_opt_outs_on_enable(self, world):
        self._sites(world, serve_admin_tools=False)
        world.config.sites["shop.localhost"].serve_admin_tools = False

        world.run_enable(site=RESERVED_BENCH_NAME)

        assert world.config.sites["shop.localhost"].serve_admin_tools is True
        assert world.config.sites["b.example.com"].serve_admin_tools is True
        assert not any("still running" in line for line in world.prints)

    def test_nginx_reload_failure_is_a_warning_not_a_refusal(self, world):
        self._sites(world)
        world.bench.bench_nginx_controller.reload.side_effect = Exception("nginx down")

        world.run_enable(site="b.example.com")

        assert world.config.sites["b.example.com"].serve_admin_tools is True
        assert world.output.warning.call_count == 1
        assert world.saves == 1


class TestStatus:
    def test_reports_configured_enabled_and_per_site_routing(self, world):
        world.sites(["shop.localhost", "b.example.com"], serve_admin_tools=False)
        world.config.admin_tools = True
        world.config.serves_admin_tools = MagicMock(
            side_effect=lambda site: world.config.sites[site].serve_admin_tools is not False
        )
        world.bench.admin_tools.compose_file_manager.compose_path.exists.return_value = True

        card = world.run_status()

        assert card.name == BENCH
        assert card.meta == "reachable"
        assert card.active
        assert card.facts["containers"] == "configured"
        assert card.facts["admin tools"] == "enabled"
        site_rows = [(label, value) for label, value in card.rows if label in ("sites", "")]
        assert site_rows == [
            ("sites", "shop.localhost  routed"),
            ("", "b.example.com  not routed"),
        ]
        world.output.print_data.assert_called_once()

    def test_reports_unconfigured_and_disabled(self, world):
        world.bench.admin_tools.compose_file_manager.compose_path.exists.return_value = False
        world.config.admin_tools = False
        world.config.serves_admin_tools = MagicMock(return_value=False)

        card = world.run_status()

        assert card.meta == "not reachable"
        assert not card.active
        assert card.facts["containers"] == "not configured"
        assert card.facts["admin tools"] == "disabled"


def test_group_app_is_invokable_standalone_via_cli_runner():
    runner = CliRunner()
    result = runner.invoke(tools_app, [])
    output = " ".join(result.output.split())
    for name in ("enable", "disable", "status"):
        assert name in output
