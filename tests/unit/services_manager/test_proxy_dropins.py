"""Contract of `frappe_manager/services_manager/proxy_dropins.py`.

The nginx-proxy `vhost.d/<domain>` file is SHARED: it carries fm's own bootstrap include block
plus whatever an operator hand-wrote into it directly, so writing or removing one of fm's
per-concern fragments must never disturb a byte of that foreign content.
"""

from pathlib import Path

import pytest

from frappe_manager.services_manager.proxy_dropins import ORDER, ProxyDropins

DOMAIN = "example.com"
HANDWRITTEN = "allow 10.0.0.0/8;\n"


@pytest.fixture
def vhostd(tmp_path: Path) -> Path:
    d = tmp_path / "vhostd"
    d.mkdir()
    return d


@pytest.fixture
def fmd(tmp_path: Path) -> Path:
    return tmp_path / "fmd"


@pytest.fixture
def dropins(vhostd: Path, fmd: Path) -> ProxyDropins:
    return ProxyDropins(vhostd, fmd)


class TestOrderIsARegistry:
    """`ORDER` is the single source of truth for which concerns exist; a name outside it must
    never reach the filesystem through any entry point."""

    def test_set_rejects_an_unregistered_concern(self, dropins):
        with pytest.raises(ValueError):
            dropins.set(DOMAIN, "bogus", "x;\n")

    def test_remove_rejects_an_unregistered_concern(self, dropins):
        with pytest.raises(ValueError):
            dropins.remove(DOMAIN, "bogus")

    def test_fragment_path_rejects_an_unregistered_concern(self, dropins):
        with pytest.raises(ValueError):
            dropins.fragment_path(DOMAIN, "bogus")


class TestSet:
    def test_writes_the_fragment_with_its_order_prefix(self, dropins, fmd):
        dropins.set(DOMAIN, "hsts", "add_header x 1;\n")
        path = fmd / "vhost" / DOMAIN / f"{ORDER['hsts']:02d}-hsts.conf"
        assert path.read_text() == "add_header x 1;\n"

    def test_an_identical_second_write_reports_no_change(self, dropins):
        dropins.set(DOMAIN, "hsts", "add_header x 1;\n")
        assert dropins.set(DOMAIN, "hsts", "add_header x 1;\n") is False


class TestBootstrapEscapesWildcardDomains:
    """An unescaped `*` in the include path is a glob to nginx, not a literal path segment:
    `/etc/nginx/fm.d/vhost/*.example.com/*.conf` would match every sibling domain directory too,
    leaking one bench's fragments into another's -- measured on a live proxy. The bootstrap
    written into `vhostd/<domain>` must always escape a wildcard domain's `*` to `\\*`."""

    def test_a_wildcard_domain_is_escaped_in_the_include_line(self, dropins, vhostd):
        dropins.set("*.example.com", "hsts", "add_header x 1;\n")
        text = (vhostd / "*.example.com").read_text()
        assert r"include /etc/nginx/fm.d/vhost/\*.example.com/*.conf;" in text


class TestForeignContentSurvives:
    def test_foreign_content_survives_set_then_remove_byte_for_byte(self, dropins, vhostd):
        path = vhostd / DOMAIN
        path.write_text(HANDWRITTEN)

        dropins.set(DOMAIN, "hsts", "add_header x 1;\n")
        assert path.read_text().endswith(HANDWRITTEN)

        dropins.remove(DOMAIN, "hsts")
        assert path.read_text() == HANDWRITTEN


class TestRemove:
    def test_removing_the_last_fragment_removes_the_domain_dir_and_the_bootstrap_file(self, dropins, vhostd, fmd):
        dropins.set(DOMAIN, "hsts", "add_header x 1;\n")

        dropins.remove(DOMAIN, "hsts")

        assert not (fmd / "vhost" / DOMAIN).exists()
        assert not (vhostd / DOMAIN).exists()

    def test_a_vhostd_file_whose_only_foreign_content_is_whitespace_is_not_unlinked(self, dropins, vhostd):
        """The exact remainder is judged for truthiness, not `.strip()`: whitespace a foreign
        writer left behind is still that writer's bytes, not fm's to discard."""
        path = vhostd / DOMAIN
        path.write_text("\n\n")
        dropins.set(DOMAIN, "hsts", "add_header x 1;\n")

        dropins.remove(DOMAIN, "hsts")

        assert path.exists()
        assert path.read_text() == "\n\n"


class TestActive:
    def test_returns_concern_names_in_order_order(self, dropins):
        dropins.set(DOMAIN, "hsts", "x;\n")
        dropins.set(DOMAIN, "upload-limit", "y;\n")

        assert dropins.active(DOMAIN) == ["upload-limit", "hsts"]

    def test_a_missing_domain_directory_returns_empty_without_raising(self, dropins):
        assert dropins.active("nope.example.com") == []
