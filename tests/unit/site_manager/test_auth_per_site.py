"""Basic auth is per site: one site can prompt while its neighbours do not.

`auth_basic` is a server-context directive, so this was inexpressible until the bench nginx grew
one server block per site. What the block layout then forces is the shape of everything here:
`custom/*.conf` is included in EVERY site's block and `auth_basic` may not appear twice in one
context, so there is no bench-wide auth conf any more. Every site is rendered from its effective
auth into `custom/<site>/auth.conf`, whether that auth is its own or the bench's.
"""

from pathlib import Path

import pytest

from frappe_manager.site_manager.bench_config import AuthConfig, SiteConfig, WebAuthConfig
from frappe_manager.site_manager.modules.auth import (
    MAP_CONF_NAME,
    SERVER_CONF_NAME,
    auth_vars,
    container_htpasswd_path,
    container_site_htpasswd_path,
    htpasswd_name,
    site_htpasswd_name,
    site_var_suffix,
)
from tests.unit.site_manager.test_site_contract import SITE, build_bench, make_bench_config

OTHER = "b.example.com"


@pytest.fixture
def bench(tmp_path):
    """A two-site bench whose second site carries its own auth."""

    def _make(bench_auth: AuthConfig | None = None, site_auth: WebAuthConfig | None = None, admin_tools=True):
        bench_path = tmp_path / SITE
        bench_path.mkdir(parents=True, exist_ok=True)
        config = make_bench_config(
            bench_path / "bench_config.toml",
            auth=bench_auth,
            admin_tools=admin_tools,
            sites={SITE: SiteConfig(), OTHER: SiteConfig(auth=site_auth)},
        )
        return build_bench(tmp_path, bench_config=config)

    return _make


def _conf(h, site: str) -> Path:
    return h.conf_dir / "custom" / site / SERVER_CONF_NAME


class TestScopeIsolation:
    def test_a_sites_own_auth_does_not_reach_its_neighbour(self, bench):
        h = bench(bench_auth=None, site_auth=WebAuthConfig(web=True, password="sp"))
        h.bench.ensure_fm_nginx_confs()

        # The bench has no auth of its own, so the inheriting site is unprotected and only the
        # overriding one is gated. A bench-wide conf could not express that at all.
        assert _conf(h, OTHER).exists()
        assert not _conf(h, SITE).exists()

    def test_the_inheriting_site_is_rendered_too_not_left_to_a_bench_wide_conf(self, bench):
        h = bench(bench_auth=AuthConfig(web=True, password="bp"))
        h.bench.ensure_fm_nginx_confs()

        # Both sites follow the bench, and BOTH get their own file. Rendering the shared case into
        # `custom/auth.conf` instead would be a second `auth_basic` in the block of any site that
        # later took auth of its own, which nginx refuses to load.
        assert _conf(h, SITE).exists()
        assert _conf(h, OTHER).exists()
        assert not (h.conf_dir / "custom" / SERVER_CONF_NAME).exists()

    def test_every_block_carries_exactly_one_auth_basic(self, bench):
        h = bench(bench_auth=AuthConfig(web=True, password="bp"), site_auth=WebAuthConfig(web=True, password="sp"))
        h.bench.ensure_fm_nginx_confs()

        # The property nginx enforces, asserted per site: the shared `custom/*.conf` glob must
        # contribute no `auth_basic`, or the site directory's file makes two in one context.
        shared = list((h.conf_dir / "custom").glob("*.conf"))
        assert not any("auth_basic" in p.read_text() for p in shared)
        for site in (SITE, OTHER):
            assert _conf(h, site).read_text().count("auth_basic ") == 1

    def test_a_site_has_its_own_credentials_not_the_benchs(self, bench):
        h = bench(
            bench_auth=AuthConfig(web=True, user="benchuser", password="bp"),
            site_auth=WebAuthConfig(web=True, user="siteuser", password="sp"),
        )
        h.bench.ensure_fm_nginx_confs()

        # Two files, so a password handed out for one site is not a password to the other.
        assert (h.conf_dir / "http_auth" / htpasswd_name(SITE)).exists()
        assert (h.conf_dir / "http_auth" / site_htpasswd_name(SITE, OTHER)).exists()
        assert container_site_htpasswd_path(SITE, OTHER) in _conf(h, OTHER).read_text()
        assert container_htpasswd_path(SITE) in _conf(h, SITE).read_text()


class TestSharedHttpContext:
    def test_two_scopes_exemptions_coexist_in_one_map_file(self, bench):
        h = bench(
            bench_auth=AuthConfig(web=True, password="bp", allow_paths=["/bench/hook"]),
            site_auth=WebAuthConfig(web=True, password="sp", allow_paths=["/site/hook"]),
        )
        h.bench.ensure_fm_nginx_confs()

        # `geo`/`map` are http context, so both scopes' blocks land in the SAME file. Distinct
        # variable names are the only thing keeping them apart; one name would mean one scope's
        # realm silently won.
        text = (h.conf_dir / "conf.d" / MAP_CONF_NAME).read_text()
        bench_realm, _, _ = auth_vars("")
        site_realm, _, _ = auth_vars(site_var_suffix(OTHER))
        assert f"map $uri {bench_realm} {{" in text
        assert f"map $uri {site_realm} {{" in text
        assert "/bench/hook" in text
        assert "/site/hook" in text

    def test_each_block_reads_the_variable_its_own_scope_defines(self, bench):
        h = bench(
            bench_auth=AuthConfig(web=True, password="bp", allow_paths=["/bench/hook"]),
            site_auth=WebAuthConfig(web=True, password="sp", allow_paths=["/site/hook"]),
        )
        h.bench.ensure_fm_nginx_confs()

        # A conf reading a variable no map defines is a config nginx will not start on, so the
        # suffix in the server block and the suffix in the map file have to be the same one.
        site_realm, _, _ = auth_vars(site_var_suffix(OTHER))
        bench_realm, _, _ = auth_vars("")
        assert f"auth_basic {site_realm};" in _conf(h, OTHER).read_text()
        assert f"auth_basic {bench_realm};" in _conf(h, SITE).read_text()

    def test_a_site_name_that_would_slug_into_its_neighbour_still_gets_its_own_variable(self):
        # `a.b` and `a-b` are different hostnames that slug to the same identifier, and a shared
        # realm variable would silently give them one prompt.
        assert site_var_suffix("a.b") != site_var_suffix("a-b")


class TestSweep:
    def test_dropping_a_sites_auth_removes_its_conf_and_its_htpasswd(self, bench):
        h = bench(bench_auth=None, site_auth=WebAuthConfig(web=True, password="sp"))
        h.bench.ensure_fm_nginx_confs()
        conf = _conf(h, OTHER)
        htpasswd = h.conf_dir / "http_auth" / site_htpasswd_name(SITE, OTHER)
        assert conf.exists()
        assert htpasswd.exists()

        h.bench.bench_config.sites[OTHER].auth = None
        h.bench.ensure_fm_nginx_confs()

        # The site follows the bench again, which protects nothing, so its credentials must not be
        # left on disk backing a prompt nobody serves.
        assert not conf.exists()
        assert not htpasswd.exists()

    def test_the_pre_per_site_bench_wide_conf_is_swept(self, bench):
        h = bench(bench_auth=AuthConfig(web=True, password="bp"))
        legacy = h.conf_dir / "custom" / SERVER_CONF_NAME
        legacy.parent.mkdir(parents=True, exist_ok=True)
        legacy.write_text('# fm:auth generated by `fm auth`; do not edit\nauth_basic "Restricted";\n')

        h.bench.ensure_fm_nginx_confs()

        # Every bench that had web auth before this change has this file. Left behind it is the
        # duplicate `auth_basic` that stops nginx starting, so the upgrade has to remove it.
        assert not legacy.exists()

    def test_a_hand_written_conf_in_a_site_directory_is_never_deleted(self, bench):
        h = bench(bench_auth=None)
        mine = _conf(h, OTHER)
        mine.parent.mkdir(parents=True, exist_ok=True)
        mine.write_text("auth_basic off;  # mine, not fm's\n")

        h.bench.ensure_fm_nginx_confs()

        # Only files carrying fm's marker are fm's to remove, per-site included.
        assert mine.read_text() == "auth_basic off;  # mine, not fm's\n"


class TestToolsStayBenchWide:
    def test_the_tools_htpasswd_survives_a_site_taking_its_own_auth(self, bench):
        h = bench(
            bench_auth=AuthConfig(web=False, tools=True, password="bp"),
            site_auth=WebAuthConfig(web=True, password="sp"),
        )
        h.bench.ensure_fm_nginx_confs()

        # The tools surface is backed by the bench's htpasswd whatever the sites do; sweeping it as
        # "no scope wants this" would unlock /adminer/ on every hostname.
        assert (h.conf_dir / "http_auth" / htpasswd_name(SITE)).exists()

    def test_a_site_cannot_express_a_tools_value_at_all(self):
        # Not "ignored": there is one Adminer and one Mailpit per bench, so a per-site value could
        # only ever be a lie, and the model refuses to store one.
        with pytest.raises(ValueError):
            SiteConfig(auth={"web": True, "tools": False})


class TestOlderNginxConf:
    """A bench's `conf.d/default.conf` is rendered once, at the nginx container's first boot, and
    then persists, so it reflects whatever image created it rather than the one running now.

    Per-site server blocks arrived in a LATER image than the Authorization-header fix `fm auth`
    already gates on, so there is a real window where a bench passes that gate and still has a conf
    including only `custom/*.conf`. Writing per-site confs there is SILENT: nginx reads none of
    them, the site serves unprotected, and `--status` still reports the prompt as on.
    """

    def _old_bench(self, tmp_path, **auth_kwargs):
        bench_path = tmp_path / SITE
        bench_path.mkdir(parents=True, exist_ok=True)
        config = make_bench_config(
            bench_path / "bench_config.toml",
            auth=AuthConfig(**auth_kwargs),
            sites={SITE: SiteConfig(), OTHER: SiteConfig()},
        )
        return build_bench(tmp_path, bench_config=config, per_site_nginx=False)

    def test_web_auth_falls_back_to_the_conf_the_old_template_includes(self, tmp_path):
        h = self._old_bench(tmp_path, web=True, password="s3cret")
        h.bench.ensure_fm_nginx_confs()

        # The bench-wide path is included by EVERY version of the template, so the prompt is
        # actually served. Per-site files here would have been written and never read.
        assert (h.conf_dir / "custom" / SERVER_CONF_NAME).is_file()
        assert not (h.conf_dir / "custom" / SITE / SERVER_CONF_NAME).exists()
        assert not (h.conf_dir / "custom" / OTHER / SERVER_CONF_NAME).exists()

    def test_the_fallback_conf_actually_gates(self, tmp_path):
        h = self._old_bench(tmp_path, web=True, password="s3cret")
        h.bench.ensure_fm_nginx_confs()

        # The property that matters: a real gate naming a real htpasswd, not an empty file.
        conf = (h.conf_dir / "custom" / SERVER_CONF_NAME).read_text()
        assert "auth_basic " in conf
        assert f"auth_basic_user_file {container_htpasswd_path(SITE)};" in conf
        assert (h.conf_dir / "http_auth" / htpasswd_name(SITE)).is_file()

    def test_an_absent_conf_also_takes_the_compatible_path(self, tmp_path):
        # Nothing is being served yet and the bench-wide conf is correct under either template, so
        # the safe answer for "unknown" is the compatible one.
        h = self._old_bench(tmp_path, web=True, password="s3cret")
        assert not (h.conf_dir / "conf.d" / "default.conf").exists()
        h.bench.ensure_fm_nginx_confs()

        assert (h.conf_dir / "custom" / SERVER_CONF_NAME).is_file()

    def test_a_recorded_site_override_is_reported_as_not_enforced(self, tmp_path):
        h = self._old_bench(tmp_path, web=True, password="bp")
        h.bench.bench_config.sites[OTHER].auth = WebAuthConfig(web=True, password="sp")
        h.bench.ensure_fm_nginx_confs()

        # Silently following the bench would leave the operator believing the site had its own
        # prompt. The whole bench IS protected here, which is the safe direction, but not the one
        # that was asked for.
        warned = " ".join(str(c.args[0]) for c in h.bench.output.warning.call_args_list if c.args)
        assert OTHER in warned
        assert "not enforced" in warned

    def test_the_probe_reads_the_per_site_include_not_the_bench_wide_one(self, tmp_path):
        old = self._old_bench(tmp_path, web=False)
        assert old.bench.nginx_conf_serves_per_site() is False

        conf_d = old.bench.path / "configs" / "nginx" / "conf" / "conf.d"
        conf_d.mkdir(parents=True, exist_ok=True)
        # `custom/*.conf` alone is the OLD template: it must not read as support.
        (conf_d / "default.conf").write_text("server {\n  include /etc/nginx/custom/*.conf;\n}\n")
        assert old.bench.nginx_conf_serves_per_site() is False

        (conf_d / "default.conf").write_text(
            f"server {{\n  include /etc/nginx/custom/*.conf;\n  include /etc/nginx/custom/{SITE}/*.conf;\n}}\n"
        )
        assert old.bench.nginx_conf_serves_per_site() is True


class TestAuthForResolver:
    """`auth_for` is the single place that answers "which auth applies to this site".

    Nothing tested it directly: the render reads `entry.auth` itself, so the resolver's only
    callers are the tool locations (for that site's web state) and `fm auth`'s reporting. Replacing
    its override branch with a no-op left every test in this file passing while every site silently
    followed the bench.
    """

    def _config(self, tmp_path, bench_auth, site_auth):
        bench_path = tmp_path / SITE
        bench_path.mkdir(parents=True, exist_ok=True)
        return make_bench_config(
            bench_path / "bench_config.toml",
            auth=bench_auth,
            sites={SITE: SiteConfig(), OTHER: SiteConfig(auth=site_auth)},
        )

    def test_a_site_without_its_own_follows_the_bench(self, tmp_path):
        config = self._config(tmp_path, AuthConfig(web=True, user="benchuser"), None)
        assert config.auth_for(SITE).user == "benchuser"
        assert config.auth_for(OTHER).user == "benchuser"

    def test_a_site_with_its_own_stops_following_the_bench(self, tmp_path):
        config = self._config(
            tmp_path, AuthConfig(web=True, user="benchuser"), WebAuthConfig(web=False, user="siteuser")
        )
        assert config.auth_for(SITE).user == "benchuser"
        assert config.auth_for(OTHER).user == "siteuser"
        # Including the web flag: an override is not a partial merge over the bench's.
        assert config.auth_for(OTHER).web is False

    def test_it_returns_the_sites_own_object_not_a_copy_of_the_benchs(self, tmp_path):
        own = WebAuthConfig(web=True, password="site-pw")
        config = self._config(tmp_path, AuthConfig(web=True, password="bench-pw"), own)
        assert config.auth_for(OTHER) is own

    def test_it_is_never_none_so_a_renderer_need_not_decide_what_absent_means(self, tmp_path):
        config = self._config(tmp_path, None, None)
        resolved = config.auth_for(SITE)
        assert resolved is not None
        # `web=False` is what a bench with no [auth] at all has always served.
        assert resolved.web is False

    def test_a_name_the_bench_does_not_serve_gets_the_bench_answer(self, tmp_path):
        """`site_names` and the recorded table can disagree mid-create, and answering with a
        harder-to-reason-about default there would be worse than answering with the bench's."""
        config = self._config(tmp_path, AuthConfig(web=True, user="benchuser"), None)
        assert config.auth_for("nosuch.example.com").user == "benchuser"
