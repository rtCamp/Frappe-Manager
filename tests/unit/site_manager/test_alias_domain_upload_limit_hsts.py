"""`BenchOrchestrator.update_alias_domains` must leave a newly added alias enforced immediately.

Measured bug: `fm domain add BENCH DOMAIN` regenerated compose and recreated the bench's nginx
container, but never ran `apply_upload_limit`/`apply_hsts`. On a real server this left the new
alias with no `vhostd/<domain>` drop-in at all, so a 5 MB POST to the freshly added alias came
back **413** while the bench's `upload_limit` was configured for 50M -- the domain fell back to
nginx-proxy's own 1M default instead of the bench's. The same gap left the alias without the
HSTS override until an unrelated `fm start` happened to heal it.

These tests exercise the real `Bench.apply_upload_limit`/`Bench.apply_hsts` (ProxyDropins writing
real files under a tmp services dir) through `update_alias_domains`, with only the docker-facing
`_update_alias_domains_lightweight` stubbed out -- that half is covered by
`test_bench_orchestrator_phases.py`'s existing alias-domain tests.
"""

from pathlib import Path
from unittest.mock import MagicMock

from frappe_manager.services_manager.proxy_dropins import ProxyDropins
from frappe_manager.site_manager.bench_config import BenchConfig
from frappe_manager.site_manager.modules.bench_orchestrator import BenchOrchestrator
from frappe_manager.site_manager.modules.upload_limit import upload_limit_conf
from frappe_manager.site_manager.site import Bench

SITE = "shop.example.com"
ALIAS = "www.shop.example.com"


def _config(tmp_path: Path, *, bench_upload_limit: str = "50M", site_upload_limit: str | None = None) -> BenchConfig:
    site_lines = [f'[sites."{SITE}"]']
    if site_upload_limit is not None:
        site_lines.append(f'upload_limit = "{site_upload_limit}"')
    toml = "\n".join(
        [
            f'name = "{SITE}"',
            "developer_mode = false",
            "admin_tools = false",
            'environment = "prod"',
            f'upload_limit = "{bench_upload_limit}"',
            *site_lines,
        ]
    )
    path = tmp_path / "bench_config.toml"
    path.write_text(toml)
    return BenchConfig.import_from_toml(path)


def _bench(tmp_path: Path, config: BenchConfig) -> Bench:
    """A real `Bench` (bypassing `__init__`) so `apply_upload_limit`/`apply_hsts` run for real
    against real files, while everything docker-facing stays a bare mock."""
    bench = Bench.__new__(Bench)
    bench.name = SITE
    bench.path = tmp_path / "bench"
    bench.path.mkdir(parents=True, exist_ok=True)
    bench.logger = MagicMock()
    bench.output = MagicMock()
    bench.bench_config = config
    bench.docker_client = MagicMock()

    services_path = tmp_path / "services"
    (services_path / "nginx-proxy" / "vhostd").mkdir(parents=True, exist_ok=True)
    (services_path / "nginx-proxy" / "fmd").mkdir(parents=True, exist_ok=True)
    bench.services = MagicMock()
    bench.services.path = services_path
    bench.services.is_service_running.return_value = True
    bench.services.nginx_controller = MagicMock()

    bench.save_bench_config = MagicMock()
    return bench


def _orchestrator(bench: Bench) -> BenchOrchestrator:
    orchestrator = BenchOrchestrator(bench, output_handler=MagicMock())
    # The docker/compose half of adding an alias is pinned by test_bench_orchestrator_phases.py;
    # stubbed here so these tests isolate the upload-limit/HSTS wiring this fix adds.
    orchestrator._update_alias_domains_lightweight = MagicMock()
    return orchestrator


def test_a_new_alias_gets_the_bench_upload_limit_without_a_separate_fm_start(tmp_path):
    """The measured consequence: before this fix a 5 MB upload to a freshly added alias was
    rejected with 413 on a bench configured for 50M, because the alias had no `vhostd` drop-in at
    all until the next `fm start`."""
    config = _config(tmp_path, bench_upload_limit="50M")
    bench = _bench(tmp_path, config)
    orchestrator = _orchestrator(bench)

    orchestrator.update_alias_domains(add_domains=[ALIAS], site=SITE)

    dropins = ProxyDropins.for_services_path(bench.services.path)
    fragment = dropins.fragment_path(ALIAS, "upload-limit")
    assert fragment.is_file(), "the new alias has no upload-limit drop-in; it would 413 at 50M+"
    assert fragment.read_text() == upload_limit_conf("50M")


def test_a_site_with_its_own_limit_gives_its_new_alias_that_value_not_the_bench_default(tmp_path):
    """A site's own `upload_limit` wins and survives: its new alias must be born with the SITE's
    effective limit (10M), not the bench's 50M default."""
    config = _config(tmp_path, bench_upload_limit="50M", site_upload_limit="10M")
    bench = _bench(tmp_path, config)
    orchestrator = _orchestrator(bench)

    orchestrator.update_alias_domains(add_domains=[ALIAS], site=SITE)

    dropins = ProxyDropins.for_services_path(bench.services.path)
    fragment = dropins.fragment_path(ALIAS, "upload-limit")
    assert fragment.read_text() == upload_limit_conf("10M")


def test_adding_an_already_present_alias_changes_nothing_and_never_reloads(tmp_path):
    """A no-op add (the domain is already an alias) returns before touching upload-limit/HSTS at
    all, so the shared proxy -- serving every other bench too -- is never reloaded for it."""
    config = _config(tmp_path, bench_upload_limit="50M")
    config.sites[SITE].alias_domains = [ALIAS]
    bench = _bench(tmp_path, config)
    orchestrator = _orchestrator(bench)

    orchestrator.update_alias_domains(add_domains=[ALIAS], site=SITE)

    dropins = ProxyDropins.for_services_path(bench.services.path)
    assert dropins.fragment_path(ALIAS, "upload-limit").exists() is False
    bench.services.nginx_controller.reload.assert_not_called()
    bench.save_bench_config.assert_not_called()
