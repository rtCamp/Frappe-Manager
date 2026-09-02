"""`/adminer/` and `/mailpit/` are routed per site, not from every hostname the bench serves.

The distinction that shapes this: there is exactly ONE Adminer and one Mailpit per bench, so
protecting them per site would be a bypass -- a different lock on a second door into the same room,
reachable by changing the `Host` header. Removing the `location` from a site's server block leaves
no door at all, which is why routing is the per-site control and auth is not.
"""

from unittest.mock import MagicMock

import pytest

from frappe_manager.site_manager.bench_config import AuthConfig, SiteConfig, WebAuthConfig
from frappe_manager.site_manager.modules.auth import build_tools_auth_block, container_htpasswd_path
from frappe_manager.site_manager.modules.bench_admin_tools import BenchAdminTools
from tests.unit.site_manager.test_site_contract import SITE, build_bench, make_bench_config

OTHER = "b.example.com"


@pytest.fixture
def tools(tmp_path):
    """A real BenchAdminTools over a two-site bench, with only compose/docker mocked."""

    def _make(bench_admin_tools=True, site_value=None, auth=None):
        bench_path = tmp_path / SITE
        bench_path.mkdir(parents=True, exist_ok=True)
        config = make_bench_config(
            bench_path / "bench_config.toml",
            admin_tools=bench_admin_tools,
            auth=auth,
            sites={SITE: SiteConfig(), OTHER: SiteConfig(serve_admin_tools=site_value)},
        )
        h = build_bench(tmp_path, bench_config=config)
        proxy = MagicMock()
        proxy.dirs.conf.host = h.conf_dir
        # Built without __init__ on purpose: it wires ComposeFile and DockerClient, none of which
        # the rendering reads. The state the method DOES use is set explicitly below.
        obj = BenchAdminTools.__new__(BenchAdminTools)
        obj.bench = h.bench
        obj.bench_name = SITE
        obj.nginx_proxy = proxy
        obj.nginx_config_location_path = h.conf_dir / "custom" / "admin-tools.conf"
        return h, obj

    return _make


def _routed(h, site: str) -> bool:
    return (h.conf_dir / "custom" / site / "admin-tools.conf").is_file()


class TestRouting:
    def test_a_site_that_opts_out_gets_no_location_at_all(self, tools):
        h, obj = tools(site_value=False)
        obj.save_nginx_location_config()

        # No file means no `location ^~ /adminer/` in that site's server block, so the request
        # falls through to Frappe. There is nothing to bypass, unlike a per-site password on a
        # location both hostnames still carry.
        assert _routed(h, SITE)
        assert not _routed(h, OTHER)

    def test_both_sites_route_them_by_default(self, tools):
        h, obj = tools(site_value=None)
        obj.save_nginx_location_config()

        # Absent means follow the bench, which is what every bench did before this was expressible.
        assert _routed(h, SITE)
        assert _routed(h, OTHER)

    def test_the_bench_level_switch_is_a_hard_floor(self, tools):
        h, obj = tools(bench_admin_tools=False, site_value=True)
        obj.save_nginx_location_config()

        # A site cannot route to containers that are not running: the bench-level key starts and
        # stops the one pair, so `serve_admin_tools = true` under it would only ever be a 502.
        assert not _routed(h, SITE)
        assert not _routed(h, OTHER)

    def test_the_shared_file_every_older_bench_has_is_removed(self, tools):
        h, obj = tools(site_value=False)
        obj.nginx_config_location_path.parent.mkdir(parents=True, exist_ok=True)
        obj.nginx_config_location_path.write_text("location ^~ /adminer/ { }\n")

        obj.save_nginx_location_config()

        # Left in place it would keep serving /adminer/ from the hostname the site just opted out
        # of, because `custom/*.conf` is included in every site's block.
        assert not obj.nginx_config_location_path.exists()

    def test_flipping_a_site_back_on_restores_its_route(self, tools):
        h, obj = tools(site_value=False)
        obj.save_nginx_location_config()
        assert not _routed(h, OTHER)

        h.bench.bench_config.sites[OTHER].serve_admin_tools = True
        obj.save_nginx_location_config()

        assert _routed(h, OTHER)
        assert "/adminer/" in (h.conf_dir / "custom" / OTHER / "admin-tools.conf").read_text()

    def test_removing_the_locations_clears_every_site(self, tools):
        h, obj = tools()
        obj.save_nginx_location_config()
        assert _routed(h, SITE)
        assert _routed(h, OTHER)

        obj.remove_nginx_location_config()

        # `fm update --admin-tools disable` stops the containers, so no site may keep a route.
        assert not _routed(h, SITE)
        assert not _routed(h, OTHER)


class TestPerSiteAuthInteraction:
    def test_the_tools_gate_names_the_benchs_credentials_on_a_site_with_its_own(self, tools):
        h, obj = tools(auth=AuthConfig(web=False, tools=True, password="bp"))
        h.bench.bench_config.sites[OTHER].auth = WebAuthConfig(web=True, password="sp")
        obj.save_nginx_location_config()

        # The tools are one container pair for the whole bench, so they stay on bench credentials
        # whatever a site does with its own web prompt.
        conf = (h.conf_dir / "custom" / OTHER / "admin-tools.conf").read_text()
        assert f"auth_basic_user_file {container_htpasswd_path(SITE)};" in conf

    def test_each_sites_block_reflects_its_own_web_state(self, tools):
        h, obj = tools(auth=AuthConfig(web=False, tools=False, password="bp"))
        h.bench.bench_config.sites[OTHER].auth = WebAuthConfig(web=True, password="sp")
        obj.save_nginx_location_config()

        # tools off + web on has to opt the locations OUT of the site's gate, and tools off + web
        # off has nothing to opt out of. One shared file could not say both.
        opted_out = (h.conf_dir / "custom" / OTHER / "admin-tools.conf").read_text()
        plain = (h.conf_dir / "custom" / SITE / "admin-tools.conf").read_text()
        assert "auth_basic off;" in opted_out
        assert "auth_basic" not in plain


class TestNotABypass:
    def test_removing_a_route_is_not_expressible_as_auth(self):
        # The reason this feature is routing and not a per-site password: with tools auth on, every
        # routed hostname carries the SAME gate, so no hostname is a weaker way in. Weakening one
        # would be the bypass, and there is no per-site input here that could produce a different
        # gate for the same containers.
        first = build_tools_auth_block(web=False, tools=True, auth_file=container_htpasswd_path("shop"))
        second = build_tools_auth_block(web=True, tools=True, auth_file=container_htpasswd_path("shop"))
        assert first == second


class TestOlderNginxConf:
    def test_the_locations_fall_back_to_the_shared_file(self, tools):
        h, obj = tools(site_value=False)
        # A conf from an image without per-site server blocks: it includes `custom/*.conf` only.
        conf_d = h.bench.path / "configs" / "nginx" / "conf" / "conf.d"
        conf_d.mkdir(parents=True, exist_ok=True)
        (conf_d / "default.conf").write_text("server {\n  include /etc/nginx/custom/*.conf;\n}\n")

        obj.save_nginx_location_config()

        # Per-site files there would be read by nothing, so the tools would answer on NO hostname
        # while the config recorded one of them as serving. The shared file is what every version of
        # the template includes, so the routing request is not honoured but the tools stay usable.
        assert obj.nginx_config_location_path.is_file()
        assert not _routed(h, SITE)
        assert not _routed(h, OTHER)
        assert "/adminer/" in obj.nginx_config_location_path.read_text()

    def test_stale_per_site_files_are_cleared_on_the_fallback_path(self, tools):
        h, obj = tools()
        obj.save_nginx_location_config()
        assert _routed(h, SITE)

        conf_d = h.bench.path / "configs" / "nginx" / "conf" / "conf.d"
        conf_d.mkdir(parents=True, exist_ok=True)
        (conf_d / "default.conf").write_text("server {\n  include /etc/nginx/custom/*.conf;\n}\n")
        obj.save_nginx_location_config()

        # An image downgrade, or a conf older than the code that wrote these: leaving them beside
        # the shared file would duplicate the locations once the newer template came back.
        assert not _routed(h, SITE)
        assert not _routed(h, OTHER)
        assert obj.nginx_config_location_path.is_file()

    def test_the_fallback_creates_the_custom_directory_it_needs(self, tools):
        """A bench can reach the fallback before anything has created `custom/`: the tools are
        enabled, the conf on disk is an older one, and no per-site or bench-wide drop-in exists yet.
        The one file that keeps `/adminer/` reachable has to be written anyway."""
        import shutil

        h, obj = tools()
        conf_d = h.bench.path / "configs" / "nginx" / "conf" / "conf.d"
        conf_d.mkdir(parents=True, exist_ok=True)
        (conf_d / "default.conf").write_text("server {\n  include /etc/nginx/custom/*.conf;\n}\n")
        shutil.rmtree(h.conf_dir / "custom", ignore_errors=True)

        obj.save_nginx_location_config()

        assert obj.nginx_config_location_path.is_file()
        assert "/adminer/" in obj.nginx_config_location_path.read_text()
