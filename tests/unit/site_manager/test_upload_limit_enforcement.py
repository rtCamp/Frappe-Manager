"""The upload limit is a per-SITE property enforced in three places (Frappe's `site_config.json`,
the global proxy's per-domain vhost.d fragment, and the bench's own nginx conf), with the bench's
`upload_limit` as the default a site inherits when it carries none of its own.

`BenchConfig.effective_upload_limit`/`sites_with_own_upload_limit`/`get_site_mappings` are the
model-layer contract (see `test_upload_limit_per_site.py`); this file defends that
`Bench.apply_upload_limit` and `Bench.ensure_fm_nginx_confs` actually READ it correctly at every
layer, including the trap that governs the bench nginx layer: `custom/*.conf` is included in every
site's server block, so a flat bench-wide `client_max_body_size` and a per-site one can never
coexist, and a bench whose nginx conf predates per-site server blocks has to fall back to the
bench value -- the same precedent `fm auth` already set for `auth_basic`.
"""

from pathlib import Path
from unittest.mock import MagicMock

from frappe_manager.services_manager.proxy_dropins import ProxyDropins
from frappe_manager.site_manager.bench_config import AuthConfig, SiteConfig
from tests.unit.site_manager.test_site_contract import SITE, build_bench, make_bench_config

OTHER = "other.localhost"


def _two_site_config(tmp_path, *, other_limit="200M", bench_limit="50M"):
    bench_path = tmp_path / SITE
    bench_path.mkdir(parents=True, exist_ok=True)
    return make_bench_config(
        bench_path / "bench_config.toml",
        auth=AuthConfig(web=False, tools=False),
        upload_limit=bench_limit,
        sites={SITE: SiteConfig(), OTHER: SiteConfig(upload_limit=other_limit)},
    )


def _put_site_config(bench_path: Path, site: str) -> Path:
    site_config = bench_path / "workspace" / "frappe-bench" / "sites" / site / "site_config.json"
    site_config.parent.mkdir(parents=True, exist_ok=True)
    site_config.write_text("{}")
    return site_config


class TestAllThreeLayersFollowTheEffectiveLimit:
    def test_a_site_with_its_own_limit_gets_it_everywhere_a_site_without_gets_the_bench_default(self, tmp_path):
        config = _two_site_config(tmp_path)
        h = build_bench(tmp_path, bench_config=config)
        bench = h.bench
        bench.docker_ops = MagicMock()
        site_config_a = _put_site_config(h.path, SITE)
        site_config_b = _put_site_config(h.path, OTHER)
        vhostd = h.services.path / "nginx-proxy" / "vhostd"
        vhostd.mkdir(parents=True, exist_ok=True)

        bench.ensure_fm_nginx_confs()
        bench.apply_upload_limit()

        # Frappe layer: max_file_size in bytes, the bench default for SITE and its own 200M for OTHER.
        assert '"max_file_size": 52428800' in site_config_a.read_text()
        assert '"max_file_size": 209715200' in site_config_b.read_text()

        # Proxy layer: one fragment per domain, resolved to its owning site's effective limit.
        dropins = ProxyDropins.for_services_path(h.services.path)
        assert dropins.fragment_path(SITE, "upload-limit").read_text() == "client_max_body_size 50m;\n"
        assert dropins.fragment_path(OTHER, "upload-limit").read_text() == "client_max_body_size 200m;\n"

        # Bench nginx layer: one file per site, in its own directory.
        assert (h.conf_dir / "custom" / SITE / "upload-limit.conf").read_text() == "client_max_body_size 50m;\n"
        assert (h.conf_dir / "custom" / OTHER / "upload-limit.conf").read_text() == "client_max_body_size 200m;\n"


class TestExactlyOneDirectiveAppliesPerServerBlock:
    def test_per_site_blocks_the_shared_glob_contributes_no_directive_of_its_own(self, tmp_path):
        config = _two_site_config(tmp_path)
        h = build_bench(tmp_path, bench_config=config)

        h.bench.ensure_fm_nginx_confs()

        assert not (h.conf_dir / "custom" / "upload-limit.conf").exists()
        shared = list((h.conf_dir / "custom").glob("*.conf"))
        assert not any("client_max_body_size" in p.read_text() for p in shared)
        for site, limit in ((SITE, "50m"), (OTHER, "200m")):
            text = (h.conf_dir / "custom" / site / "upload-limit.conf").read_text()
            assert text.count("client_max_body_size") == 1
            assert text == f"client_max_body_size {limit};\n"

    def test_legacy_flat_conf_the_per_site_directories_carry_no_directive_of_their_own(self, tmp_path):
        config = _two_site_config(tmp_path)
        h = build_bench(tmp_path, bench_config=config, per_site_nginx=False)

        h.bench.ensure_fm_nginx_confs()

        flat = h.conf_dir / "custom" / "upload-limit.conf"
        assert flat.read_text().count("client_max_body_size") == 1
        assert flat.read_text() == "client_max_body_size 50m;\n"
        assert not (h.conf_dir / "custom" / SITE / "upload-limit.conf").exists()
        assert not (h.conf_dir / "custom" / OTHER / "upload-limit.conf").exists()


class TestLegacyNginxConfFallback:
    def test_a_bench_whose_conf_predates_per_site_blocks_follows_the_bench_value_and_warns(self, tmp_path):
        config = _two_site_config(tmp_path)
        h = build_bench(tmp_path, bench_config=config, per_site_nginx=False)

        h.bench.ensure_fm_nginx_confs()

        assert h.bench.nginx_conf_serves_per_site() is False
        # Not pinned to the exact sentence: the operator must learn which site(s) it left alone
        # and what value the whole bench follows instead.
        warned = " ".join(str(c.args[0]) for c in h.bench.output.warning.call_args_list)
        assert OTHER in warned
        assert SITE not in warned  # SITE has no override of its own, must not be named
        assert "50M" in warned

    def test_a_bench_whose_conf_already_serves_per_site_warns_nothing(self, tmp_path):
        config = _two_site_config(tmp_path)
        h = build_bench(tmp_path, bench_config=config, per_site_nginx=True)

        h.bench.ensure_fm_nginx_confs()

        assert not any("predates" in str(c.args[0]) for c in h.bench.output.warning.call_args_list)


class TestFlatConfIsSweptWhenPerSiteFilesAreWritten:
    def test_an_fm_written_flat_conf_is_removed_once_per_site_files_land(self, tmp_path):
        config = _two_site_config(tmp_path)
        h = build_bench(tmp_path, bench_config=config)
        flat = h.conf_dir / "custom" / "upload-limit.conf"
        flat.parent.mkdir(parents=True, exist_ok=True)
        # Exactly the shape every bench's flat file has always been written in, fm-owned content
        # that predates per-site rendering existing at all.
        flat.write_text("client_max_body_size 50m;\n")

        h.bench.ensure_fm_nginx_confs()

        assert not flat.exists()
        assert (h.conf_dir / "custom" / SITE / "upload-limit.conf").exists()

    def test_a_hand_written_flat_conf_is_never_deleted(self, tmp_path):
        """Only a copy shaped exactly like fm's own output is fm's to sweep; anything else in the
        file is an operator's, same bar `is_fm_auth_conf` sets for auth."""
        config = _two_site_config(tmp_path)
        h = build_bench(tmp_path, bench_config=config)
        flat = h.conf_dir / "custom" / "upload-limit.conf"
        flat.parent.mkdir(parents=True, exist_ok=True)
        flat.write_text("client_max_body_size 50m;  # mine, not fm's\n")

        h.bench.ensure_fm_nginx_confs()

        assert flat.read_text() == "client_max_body_size 50m;  # mine, not fm's\n"

    def test_a_stale_per_site_file_is_swept_when_its_site_is_dropped(self, tmp_path):
        config = _two_site_config(tmp_path)
        h = build_bench(tmp_path, bench_config=config)
        h.bench.ensure_fm_nginx_confs()
        assert (h.conf_dir / "custom" / OTHER / "upload-limit.conf").exists()

        config.sites = {SITE: SiteConfig()}
        h.bench.bench_nginx_controller.reload.reset_mock()
        h.bench.ensure_fm_nginx_confs()

        assert not (h.conf_dir / "custom" / OTHER / "upload-limit.conf").exists()
        h.bench.bench_nginx_controller.reload.assert_called_once_with()


class TestOneReloadPerOperation:
    def test_update_upload_limit_reloads_bench_nginx_and_the_global_proxy_exactly_once(self, tmp_path):
        config = _two_site_config(tmp_path)
        h = build_bench(tmp_path, bench_config=config)
        bench = h.bench
        bench.docker_ops = MagicMock()
        _put_site_config(h.path, SITE)
        _put_site_config(h.path, OTHER)
        (h.services.path / "nginx-proxy" / "vhostd").mkdir(parents=True, exist_ok=True)
        h.services.is_service_running.return_value = True

        from unittest.mock import patch

        from frappe_manager.site_manager.bench_config import BenchConfig

        with patch.object(BenchConfig, "export_to_compose_inputs", return_value={"environment": {}}):
            bench.update_upload_limit("300M")

        bench.bench_nginx_controller.reload.assert_called_once_with()
        h.services.nginx_controller.reload.assert_called_once_with()

    def test_a_second_pass_that_changes_nothing_reloads_neither(self, tmp_path):
        config = _two_site_config(tmp_path)
        h = build_bench(tmp_path, bench_config=config)
        _put_site_config(h.path, SITE)
        _put_site_config(h.path, OTHER)
        (h.services.path / "nginx-proxy" / "vhostd").mkdir(parents=True, exist_ok=True)
        h.services.is_service_running.return_value = True

        h.bench.ensure_fm_nginx_confs()
        h.bench.apply_upload_limit()
        h.bench.bench_nginx_controller.reload.reset_mock()

        h.bench.ensure_fm_nginx_confs()
        assert h.bench.apply_upload_limit() is False

        h.bench.bench_nginx_controller.reload.assert_not_called()


class TestAliasDomainsResolveThroughTheirOwningSite:
    """The measured gap this slice closes: a domain with no owning site must be skipped, not
    silently handed the bench default, and a newly added alias must reach its site's effective
    limit as soon as `apply_upload_limit` runs again -- no restart required."""

    def test_an_alias_added_to_a_site_with_its_own_limit_gets_that_limit_immediately(self, tmp_path):
        config = _two_site_config(tmp_path)
        h = build_bench(tmp_path, bench_config=config)
        bench = h.bench
        (h.services.path / "nginx-proxy" / "vhostd").mkdir(parents=True, exist_ok=True)

        # The alias is added to OTHER (its own 200M limit) after the bench was already running.
        config.sites[OTHER].alias_domains = ["alias.example.com"]

        assert bench.apply_upload_limit() is True

        dropins = ProxyDropins.for_services_path(h.services.path)
        assert dropins.fragment_path("alias.example.com", "upload-limit").read_text() == "client_max_body_size 200m;\n"

    def test_a_domain_with_no_owning_site_in_the_mapping_is_skipped_not_defaulted(self, tmp_path):
        """Belt-and-braces: a real `BenchConfig`'s `domains` and `get_site_mappings` are built from
        the same site/alias data, so they cannot drift apart through the public model -- exercised
        here against a minimal stand-in instead, the same style `test_nginx_conf_heal_on_start.py`
        already uses for this method."""
        from types import SimpleNamespace

        from frappe_manager.site_manager.site import Bench

        orphan = "orphan.example.com"
        bench = Bench.__new__(Bench)
        bench.name = SITE
        bench.path = tmp_path / SITE
        (bench.path / "workspace" / "frappe-bench" / "sites").mkdir(parents=True)
        bench.output = MagicMock()
        bench.bench_config = SimpleNamespace(
            site_names=[SITE],
            domains=[SITE, orphan],
            effective_upload_limit=lambda site: "50M",
            get_site_mappings=lambda: {SITE: SITE},
        )
        bench.services = MagicMock()
        bench.services.path = tmp_path / "services"
        (bench.services.path / "nginx-proxy" / "vhostd").mkdir(parents=True)

        bench.apply_upload_limit()

        dropins = ProxyDropins.for_services_path(bench.services.path)
        assert dropins.fragment_path(SITE, "upload-limit").exists()
        assert not dropins.fragment_path(orphan, "upload-limit").exists()
