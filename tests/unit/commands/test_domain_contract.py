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
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
import typer

from frappe_manager.commands.arguments import BenchSiteArgument
from frappe_manager.commands.domain.add import add_domain
from frappe_manager.commands.domain.list import list_domains
from frappe_manager.commands.domain.remove import remove_domain
from frappe_manager.output_manager import set_global_output_handler
from frappe_manager.output_manager.base import OutputHandler
from frappe_manager.services_manager.proxy_dropins import ProxyDropins
from frappe_manager.site_manager.bench_config import SiteConfig
from frappe_manager.site_manager.domain_conflict import DomainConflict, DomainConflictError
from frappe_manager.site_manager.exceptions import BenchNotRunning
from frappe_manager.ssl_manager import SUPPORTED_SSL_TYPES

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


    @property
    def errors(self) -> list[str]:
        return [c.args[0] for c in self.output.display_error.call_args_list if c.args]

    @property
    def prints(self) -> list[str]:
        return [c.args[0] for c in self.output.print.call_args_list if c.args]

    @property
    def warnings(self) -> list[str]:
        return [c.args[0] for c in self.output.warning.call_args_list if c.args]


    def _ctx(self, *, site: str | None = None, domain: str | None = None, args: list[str] | None = None) -> typer.Context:
        ctx = MagicMock(spec=typer.Context)
        ctx.obj = {"services": self.services, "fm_config_manager": self.fm_config, "site": site, "domain": domain}
        # `fm domain remove` is registered with allow_extra_args, so Click hands stray positionals
        # here instead of rejecting them itself.
        ctx.args = args or []
        return ctx

    def add(self, domains: list[str], *, site: str | None = None, allow_domain_conflicts: bool = False):
        return add_domain(self._ctx(site=site), address=BENCH, domains=domains, allow_domain_conflicts=allow_domain_conflicts)

    def remove(self, *, domain: str | None, args: list[str] | None = None, address: str = BENCH):
        return remove_domain(self._ctx(domain=domain, args=args), address=address)

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

    def test_an_accepted_conflict_still_warns(self, world):
        """`--allow-domain-conflicts` permits the clash; it does not hide it. The check is still
        run, because two benches answering one hostname means the proxy decides which site a
        visitor reaches -- silence there is the defect, not the conflict."""
        conflict = DomainConflict(domain="www.new.com", owner_bench="other.localhost", owner_site="other.localhost")
        world.validate_domains_unique.side_effect = DomainConflictError([conflict])

        world.add(["www.new.com"], allow_domain_conflicts=True)

        assert world.validate_domains_unique.call_args.kwargs["skip_check"] is False
        assert any("alternate between them" in w for w in world.warnings)
        world.bench.update_alias_domains.assert_called_once()

    def test_config_enforce_domain_uniqueness_false_also_warns(self, world):
        """Turning the rule off host-wide is the same opt-in, and gets the same warning."""
        conflict = DomainConflict(domain="www.new.com", owner_bench="other.localhost", owner_site="other.localhost")
        world.validate_domains_unique.side_effect = DomainConflictError([conflict])
        world.fm_config.validation.enforce_domain_uniqueness = False

        world.add(["www.new.com"])

        assert any("alternate between them" in w for w in world.warnings)
        world.bench.update_alias_domains.assert_called_once()

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

    def test_the_add_grammar_typed_at_a_removal_suggests_the_command_that_works(self, world):
        """`fm domain add BENCH/SITE DOMAIN` transcribed into a removal put the domain in a
        second positional, and Click answered "Got unexpected extra argument" -- naming neither
        the grammar nor the command the operator meant, which is derivable from what they typed."""
        with pytest.raises(typer.Exit):
            world.remove(domain=None, address=f"{BENCH}/{BENCH}", args=["www.example.com"])

        assert "takes one address, the domain itself" in world.errors[0]
        assert f"fm domain remove {BENCH}/www.example.com" in world.errors[0]
        world.bench.update_alias_domains.assert_not_called()

    def test_a_flag_is_not_mistaken_for_a_domain(self, world):
        """Only bare positionals are the transcription case; an unconsumed option is a different
        error and must not produce a nonsense suggestion."""
        world.remove(domain="www.example.com", args=["--some-flag"])

        assert world.errors == []
        world.bench.update_alias_domains.assert_called_once()


class TestDomainList:
    def test_prints_plain_lines_through_the_data_channel(self, world):
        """Plain lines, and through the handler: a rich cell would truncate a long hostname, and
        a raw write would be invisible to the file log and corrupt the --events stream."""
        world.bench.bench_config.sites = {BENCH: SiteConfig(alias_domains=["www.example.com"])}
        world.bench.bench_config.site_names = [BENCH]

        world.list()

        world.output.print_data.assert_not_called()
        assert [call.args[0] for call in world.output.data_raw.call_args_list] == [
            f"{BENCH}  primary",
            f"{BENCH}  www.example.com",
        ]


class TestRequiredDomainsArgument:
    def test_the_variadic_domains_argument_is_required_at_the_parser(self):
        """`fm domain add BENCH` with no DOMAIN must be a usage error, not the former
        'Alias domains updated successfully' over an empty list."""
        import typer.main as typer_main

        from frappe_manager.commands.domain import domain_app

        click_group = typer_main.get_command(domain_app)
        add_cmd = click_group.commands["add"]
        domains_param = next(p for p in add_cmd.params if p.name == "domains")
        assert domains_param.required is True


class TestDomainRemoveWithACertificate:
    """A certificate outlives the domain it was issued for unless this command deals with it:
    nginx stops answering immediately, but the material and its private key stay on disk, and
    re-adding the domain puts THAT certificate back in service with no issuance step."""

    def _holds(self, world, ssl_type):
        world.bench.bench_config.sites = {"shop.local": SiteConfig(alias_domains=["www.shop.com"])}
        world.bench.certificate_manager.certificates = [
            SimpleNamespace(domain="www.shop.com", ssl_type=ssl_type)
        ]

    def test_a_dev_certificate_goes_with_the_domain(self, world):
        """Signed by a CA fm owns and regenerated in seconds, so refusing would be a wall for
        nothing."""
        self._holds(world, SUPPORTED_SSL_TYPES.dev)

        world.remove(domain="www.shop.com")

        world.bench.certificate_manager.remove_certificate_by_domain.assert_called_once_with("www.shop.com")
        world.bench.update_alias_domains.assert_called_once()

    def test_a_letsencrypt_certificate_is_refused_and_the_domain_kept(self, world):
        """Reissuing costs a rate-limited round with the CA, so fm will not discard it on the
        operator's behalf -- and the domain stays, because a half-done removal is worse."""
        self._holds(world, SUPPORTED_SSL_TYPES.le)

        with pytest.raises(typer.Exit):
            world.remove(domain="www.shop.com")

        world.bench.certificate_manager.remove_certificate_by_domain.assert_not_called()
        world.bench.update_alias_domains.assert_not_called()

    def test_the_refusal_names_the_command_that_removes_it(self, world):
        """Pointing at `fm ssl remove` is the whole point: the operator is one command from done."""
        self._holds(world, SUPPORTED_SSL_TYPES.custom)

        with pytest.raises(typer.Exit):
            world.remove(domain="www.shop.com")

        said = " ".join(str(c) for c in [*world.output.display_error.call_args_list, *world.output.print.call_args_list])
        assert f"fm ssl remove {BENCH}/www.shop.com" in said


class TestDomainRemoveProxyDropinCleanup:
    """The nginx proxy drop-in fragments for a domain outlive it the same way a certificate
    does (see TestDomainRemoveWithACertificate above): nginx keeps serving the orphaned
    fragment, and a domain later added to a DIFFERENT bench silently inherits this bench's
    upload limit and HSTS header unless this command cleans them up."""

    def _dropins(self, world, tmp_path) -> ProxyDropins:
        services_path = tmp_path / "services"
        world.bench.services.path = services_path
        return ProxyDropins.for_services_path(services_path)

    def test_removes_every_dropin_and_the_vhostd_bootstrap(self, world, tmp_path):
        """Seeds two concerns so a short-circuiting `any(dropins.remove(...) for ...)` -- which
        stops after the first True and strands every concern after it, e.g. HSTS, for the next
        bench to silently inherit -- fails this test."""
        dropins = self._dropins(world, tmp_path)
        dropins.set("www.example.com", "upload-limit", "client_max_body_size 50m;\n")
        dropins.set("www.example.com", "hsts", "add_header Strict-Transport-Security max-age=63072000;\n")

        world.remove(domain="www.example.com")

        assert dropins.active("www.example.com") == []
        assert not (dropins.fmd_dir / "vhost" / "www.example.com").exists()
        assert not (dropins.vhostd_dir / "www.example.com").exists()

    def test_another_domains_dropins_are_untouched(self, world, tmp_path):
        """Cleanup is scoped to the removed domain's own fragment directory; a sibling alias's
        drop-ins and bootstrap must survive the removal."""
        dropins = self._dropins(world, tmp_path)
        dropins.set("www.example.com", "upload-limit", "client_max_body_size 50m;\n")
        dropins.set("api.example.com", "upload-limit", "client_max_body_size 50m;\n")

        world.remove(domain="www.example.com")

        assert dropins.active("api.example.com") == ["upload-limit"]
        assert (dropins.vhostd_dir / "api.example.com").exists()

    def test_reloads_the_proxy_once_when_a_dropin_is_removed(self, world, tmp_path):
        """One domain removal with two concerns must fold into a single nginx reload, not zero
        and not two."""
        dropins = self._dropins(world, tmp_path)
        dropins.set("www.example.com", "upload-limit", "client_max_body_size 50m;\n")
        dropins.set("www.example.com", "hsts", "add_header Strict-Transport-Security max-age=63072000;\n")

        world.remove(domain="www.example.com")

        world.bench.services.nginx_controller.reload.assert_called_once()

    def test_does_not_reload_the_proxy_when_the_domain_had_no_dropins(self, world, tmp_path):
        """No fragments to clean up means nothing changed in the shared nginx config, so a
        reload would be wasted work against a potentially busy proxy."""
        self._dropins(world, tmp_path)

        world.remove(domain="www.example.com")

        world.bench.services.nginx_controller.reload.assert_not_called()
