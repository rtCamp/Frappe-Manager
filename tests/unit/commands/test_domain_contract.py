"""Characterization tests for the `fm domain add|remove|list` noun group.

These commands are not yet wired into the root app (wave 2 does that), so every test calls the
command function directly with a hand-built `ctx.obj`, mirroring
`test_update_and_deploy_contract.py:219-224`. `Bench.get_object` is mocked per module; the real
`bench.update_alias_domains` (site_manager/modules/bench_orchestrator.py:1376) is exercised
elsewhere and is treated here as an opaque collaborator.
"""

import inspect
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
import typer

from frappe_manager.commands.arguments import BenchSiteArgument
from frappe_manager.commands.domain.add import add_domain
from frappe_manager.commands.domain.list import list_domains
from frappe_manager.commands.domain.remove import remove_domain
from frappe_manager.output_manager import set_global_output_handler
from frappe_manager.output_manager.base import OutputHandler
from frappe_manager.site_manager.bench_config import SiteConfig
from frappe_manager.site_manager.domain_conflict import DomainConflict, DomainConflictError
from frappe_manager.site_manager.exceptions import BenchNotRunning

pytestmark = pytest.mark.timeout(15)

BENCH = "mybench.localhost"


class DomainWorld:
    """Drives the real command functions with every collaborator replaced at its seam."""

    def __init__(self, tmp_path: Path, stack: ExitStack) -> None:
        self.output = MagicMock(spec=OutputHandler)
        # conftest installs a real RichOutputHandler globally; swap the INSTANCE (not the getter)
        # so get_global_output_handler() hands back an observable double.
        set_global_output_handler(self.output)

        self.benches_root = tmp_path / "benches"
        self.bench_path = self.benches_root / BENCH
        self.bench_path.mkdir(parents=True)

        self.services = MagicMock(name="services_manager")
        self.fm_config = MagicMock(name="fm_config_manager")
        self.fm_config.validation.enforce_domain_uniqueness = True

        self.bench = MagicMock(name="Bench")
        self.bench.name = BENCH
        self.bench.site_name = BENCH
        self.bench.running = True
        self.bench.bench_config.sites = {
            BENCH: SiteConfig(alias_domains=["www.example.com", "api.example.com"]),
        }
        self.bench.bench_config.site_names = [BENCH]
        self.bench.bench_config.primary_site_or_none.return_value = BENCH

        bench_cls = MagicMock(name="Bench class")
        bench_cls.get_object.return_value = self.bench
        self.bench_cls = bench_cls

        self.validate_domains_unique = MagicMock(name="validate_domains_unique")
        self.check_migration = MagicMock(name="check_bench_migration_required")

        p = stack.enter_context
        for module in ("add", "remove", "list"):
            p(patch(f"frappe_manager.commands.domain.{module}.Bench", bench_cls))
            p(patch(f"frappe_manager.commands.domain.{module}.check_bench_migration_required", self.check_migration))
        p(patch("frappe_manager.commands.domain.add.CLI_BENCHES_DIRECTORY", self.benches_root))
        p(patch("frappe_manager.commands.domain.add.validate_domains_unique", self.validate_domains_unique))

    # -- observation ---------------------------------------------------

    @property
    def errors(self) -> list[str]:
        return [c.args[0] for c in self.output.display_error.call_args_list if c.args]

    @property
    def prints(self) -> list[str]:
        return [c.args[0] for c in self.output.print.call_args_list if c.args]

    # -- run -------------------------------------------------------------

    def _ctx(self, *, site: str | None = None, domain: str | None = None) -> typer.Context:
        ctx = MagicMock(spec=typer.Context)
        ctx.obj = {"services": self.services, "fm_config_manager": self.fm_config, "site": site, "domain": domain}
        return ctx

    def add(self, domains: list[str], *, site: str | None = None, allow_domain_conflicts: bool = False):
        return add_domain(self._ctx(site=site), address=BENCH, domains=domains, allow_domain_conflicts=allow_domain_conflicts)

    def remove(self, *, domain: str | None):
        return remove_domain(self._ctx(domain=domain), address=BENCH)

    def list(self):
        return list_domains(self._ctx(), benchname=BENCH)


@pytest.fixture
def world(tmp_path):
    with ExitStack() as stack:
        yield DomainWorld(tmp_path, stack)


class TestDomainAdd:
    def test_validates_uniqueness_and_writes_aliases(self, world):
        world.add(["www.new.com"], site="shop")

        world.validate_domains_unique.assert_called_once_with(
            ["www.new.com"], benches_root=world.benches_root, exclude_bench=BENCH, skip_check=False
        )
        world.bench.update_alias_domains.assert_called_once_with(add_domains=["www.new.com"], site="shop")
        assert world.prints == ["Alias domains updated successfully"]

    def test_bare_bench_attaches_to_primary_site(self, world):
        world.add(["www.new.com"])

        world.bench.update_alias_domains.assert_called_once_with(add_domains=["www.new.com"], site=None)

    def test_allow_domain_conflicts_flag_skips_the_check(self, world):
        world.add(["www.new.com"], allow_domain_conflicts=True)

        assert world.validate_domains_unique.call_args.kwargs["skip_check"] is True
        world.bench.update_alias_domains.assert_called_once()

    def test_config_enforce_domain_uniqueness_false_also_skips_the_check(self, world):
        world.fm_config.validation.enforce_domain_uniqueness = False

        world.add(["www.new.com"])

        assert world.validate_domains_unique.call_args.kwargs["skip_check"] is True

    def test_conflict_refusal_names_the_flag_hint_and_changes_nothing(self, world):
        conflict = DomainConflict(domain="www.new.com", owner_bench="othersite.localhost", owner_site="othersite.localhost")
        world.validate_domains_unique.side_effect = DomainConflictError([conflict])

        with pytest.raises(typer.Exit) as exc:
            world.add(["www.new.com"])

        assert exc.value.exit_code == 1
        assert world.errors == [str(DomainConflictError([conflict]))]
        assert any("--allow-domain-conflicts" in p for p in world.prints)
        world.bench.update_alias_domains.assert_not_called()

    def test_refuses_when_bench_not_running(self, world):
        world.bench.running = False

        with pytest.raises(BenchNotRunning):
            world.add(["www.new.com"])

        world.bench.update_alias_domains.assert_not_called()

    def test_address_argument_type_forbids_all(self):
        # `fm domain add` uses BenchSiteArgument, never BenchSiteAllArgument: an alias belongs to
        # one site, so 'all' must be refused by the PARSER, the same way update.py:305-320 refuses
        # it for --add-alias/--remove-alias/--db-ca today.
        assert inspect.signature(add_domain).parameters["address"].annotation is BenchSiteArgument

    def test_address_argument_callback_rejects_all_site(self, tmp_path):
        # Exercise the actual callback wired onto that argument (not a copy), against a real bench
        # directory so it reaches the site-name check rather than "bench not found".
        argument = inspect.signature(add_domain).parameters["address"].annotation.__metadata__[0]
        (tmp_path / "shop").mkdir()

        ctx = MagicMock(spec=typer.Context)
        ctx.obj = {}

        with patch("frappe_manager.utils.callbacks.CLI_BENCHES_DIRECTORY", tmp_path):
            with pytest.raises(typer.BadParameter):
                argument.callback(ctx, "shop/all")


class TestDomainRemove:
    def test_resolves_the_owning_site_from_its_aliases(self, world):
        world.bench.bench_config.sites = {
            "primary.local": SiteConfig(alias_domains=[]),
            "shop.local": SiteConfig(alias_domains=["www.shop.com"]),
        }

        world.remove(domain="www.shop.com")

        world.bench.update_alias_domains.assert_called_once_with(remove_domains=["www.shop.com"], site="shop.local")
        assert world.prints == ["Alias domains updated successfully"]

    def test_refuses_a_sites_own_canonical_domain(self, world):
        world.bench.bench_config.sites = {BENCH: SiteConfig(alias_domains=[])}

        with pytest.raises(typer.Exit) as exc:
            world.remove(domain=BENCH)

        assert exc.value.exit_code == 1
        assert "not an alias" in world.errors[0]
        world.bench.update_alias_domains.assert_not_called()

    def test_refuses_a_domain_no_site_serves(self, world):
        with pytest.raises(typer.Exit):
            world.remove(domain="unknown.example.com")

        assert "not a served alias" in world.errors[0]
        world.bench.update_alias_domains.assert_not_called()

    def test_requires_a_domain_segment(self, world):
        with pytest.raises(typer.Exit):
            world.remove(domain=None)

        assert "needs a domain" in world.errors[0]
        world.bench.update_alias_domains.assert_not_called()

    def test_refuses_when_bench_not_running(self, world):
        world.bench.running = False

        with pytest.raises(BenchNotRunning):
            world.remove(domain="www.example.com")


class TestDomainList:
    def test_prints_plain_lines_not_a_table(self, world, capsys):
        world.bench.bench_config.sites = {BENCH: SiteConfig(alias_domains=["www.example.com"])}
        world.bench.bench_config.site_names = [BENCH]

        world.list()

        world.output.stop.assert_called_once()
        world.output.print_data.assert_not_called()
        lines = capsys.readouterr().out.splitlines()
        assert lines == [
            f"{BENCH}  primary",
            f"{BENCH}  www.example.com",
        ]
