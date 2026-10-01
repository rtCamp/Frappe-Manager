"""The upload limit is a per-SITE property, with the bench's `upload_limit` as the default a site
inherits when it carries none of its own.

`effective_upload_limit`/`sites_with_own_upload_limit` are the one place that answers "which limit
applies" and "which sites opted out of the bench default", mirroring the precedent `auth_for` and
`serves_admin_tools` already set for other per-site-with-bench-fallback fields -- except an unknown
site name RAISES here rather than silently falling back to the bench default, because answering a
question about a site that does not exist would hide the bug instead of surfacing it.
"""

from pathlib import Path

import pytest
from pydantic import ValidationError

from frappe_manager.site_manager.bench_config import BenchConfig, SiteConfig
from tests.unit.site_manager.test_site_contract import SITE, make_bench_config

OTHER = "b.example.com"


class TestEffectiveUploadLimit:
    def test_a_site_without_its_own_value_inherits_the_bench_default(self, tmp_path):
        config = make_bench_config(tmp_path, upload_limit="50M", sites={SITE: SiteConfig()})
        assert config.effective_upload_limit(SITE) == "50M"

    def test_a_sites_own_value_wins_over_the_bench_default(self, tmp_path):
        config = make_bench_config(
            tmp_path,
            upload_limit="50M",
            sites={SITE: SiteConfig(), OTHER: SiteConfig(upload_limit="200M")},
        )
        assert config.effective_upload_limit(SITE) == "50M"
        assert config.effective_upload_limit(OTHER) == "200M"

    def test_an_unknown_site_name_raises_rather_than_answering_with_the_bench_default(self, tmp_path):
        config = make_bench_config(tmp_path, upload_limit="50M", sites={SITE: SiteConfig()})
        with pytest.raises(ValueError, match="nosuch.example.com"):
            config.effective_upload_limit("nosuch.example.com")


class TestSitesWithOwnUploadLimit:
    def test_lists_only_the_sites_carrying_their_own_value(self, tmp_path):
        config = make_bench_config(
            tmp_path,
            upload_limit="50M",
            sites={
                SITE: SiteConfig(upload_limit="200M"),
                OTHER: SiteConfig(),
                "c.example.com": SiteConfig(upload_limit="1G"),
            },
        )
        assert config.sites_with_own_upload_limit() == ["c.example.com", SITE]

    def test_empty_when_no_site_set_its_own(self, tmp_path):
        config = make_bench_config(tmp_path, upload_limit="50M", sites={SITE: SiteConfig(), OTHER: SiteConfig()})
        assert config.sites_with_own_upload_limit() == []


class TestPerSiteFormatValidation:
    def test_an_invalid_per_site_value_is_refused_at_construction(self):
        with pytest.raises(ValidationError, match="Invalid upload limit format"):
            SiteConfig(upload_limit="50MB")

    def test_a_valid_per_site_value_is_accepted(self):
        assert SiteConfig(upload_limit="200M").upload_limit == "200M"
        assert SiteConfig(upload_limit="1G").upload_limit == "1G"


def _import(tmp_path, text: str) -> BenchConfig:
    path = tmp_path / "bench_config.toml"
    path.write_text(text)
    return BenchConfig.import_from_toml(path)


class TestRoundTrip:
    def test_a_bench_config_with_no_per_site_upload_limit_round_trips_without_gaining_one(self, tmp_path: Path):
        text = (
            f'name = "{SITE}"\ndeveloper_mode = false\nadmin_tools = false\nenvironment = "prod"\n'
            f'\n[sites."{SITE}"]\n'
        )
        config = _import(tmp_path, text)
        assert config.sites[SITE].upload_limit is None

        config.export_to_toml(config.root_path)
        rendered = config.root_path.read_text()
        assert "upload_limit" not in rendered.split(f'[sites."{SITE}"]', 1)[1]

        reimported = BenchConfig.import_from_toml(config.root_path)
        assert reimported.sites[SITE].upload_limit is None

    def test_a_sites_own_upload_limit_round_trips(self, tmp_path: Path):
        text = (
            f'name = "{SITE}"\ndeveloper_mode = false\nadmin_tools = false\nenvironment = "prod"\n'
            f'\n[sites."{SITE}"]\nupload_limit = "200M"\n'
        )
        config = _import(tmp_path, text)
        assert config.sites[SITE].upload_limit == "200M"

        config.export_to_toml(config.root_path)
        reimported = BenchConfig.import_from_toml(config.root_path)
        assert reimported.sites[SITE].upload_limit == "200M"
