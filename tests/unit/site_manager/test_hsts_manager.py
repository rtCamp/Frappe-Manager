"""Contract of `frappe_manager/site_manager/modules/hsts_manager.py`.

The nginx-proxy `vhost.d/<domain>` file is SHARED: `UploadLimitManager` appends a
`client_max_body_size` directive to it, `VhostConfigManager` owns a `# fm:https-redirect`
block in it, `fm maintenance` owns a `# fm:maintenance` block in it, and operators hand-write
directives into it. So writing/removing the HSTS override must touch only fm's own block.
"""

from pathlib import Path

import pytest

from frappe_manager.site_manager.modules.hsts_manager import HstsManager
from frappe_manager.ssl_manager.vhost_config_manager import VhostConfigManager

DOMAIN = "example.com"
UPLOAD_LIMIT = "client_max_body_size 512m;"
HANDWRITTEN = "add_header X-Operator-Wrote-This 1;"


@pytest.fixture
def vhostd(tmp_path: Path) -> Path:
    d = tmp_path / "vhostd"
    d.mkdir()
    return d


@pytest.fixture
def manager(vhostd: Path) -> HstsManager:
    return HstsManager(vhostd)


class TestSetHsts:
    def test_an_on_value_hides_the_bench_header_unconditionally(self, manager, vhostd):
        changed = manager.set_hsts(DOMAIN, "max-age=63072000; includeSubDomains; preload")

        text = (vhostd / DOMAIN).read_text()
        assert changed is True
        # Not keyed on `$https`: the bench sends its hardcoded header on every response
        # regardless of scheme, so hiding it must not become scheme-dependent either.
        assert "proxy_hide_header Strict-Transport-Security;" in text
        assert "if ($https)" not in text.split("proxy_hide_header", 1)[0]

    def test_an_on_value_emits_the_header_only_over_https(self, manager, vhostd):
        """RFC 6797: a host must not send this header over a non-secure connection. This vhost's
        server block answers both `listen 80` and `listen ... ssl`, so the value must be gated on
        the connection actually being TLS, not emitted unconditionally the way `proxy_hide_header`
        correctly is."""
        manager.set_hsts(DOMAIN, "max-age=63072000; includeSubDomains; preload")

        text = (vhostd / DOMAIN).read_text()
        assert 'set $fm_hsts_value "";' in text
        assert 'if ($https) {\n    set $fm_hsts_value "max-age=63072000; includeSubDomains; preload";\n}' in text
        assert "add_header Strict-Transport-Security $fm_hsts_value always;" in text
        # The literal value must never appear directly in an unconditional add_header: that is
        # exactly the bug (STS emitted over plain HTTP) this construction exists to avoid.
        assert 'add_header Strict-Transport-Security "max-age=63072000; includeSubDomains; preload"' not in text

    def test_off_still_hides_the_bench_header_but_adds_nothing(self, manager, vhostd):
        """The bug this closes: the bench emits its hardcoded header no matter what `hsts`
        says, so "off" must still strip it -- it must simply not replace it with one of fm's
        own."""
        manager.set_hsts(DOMAIN, "off")

        text = (vhostd / DOMAIN).read_text()
        assert "proxy_hide_header Strict-Transport-Security;" in text
        assert "add_header Strict-Transport-Security" not in text

    def test_it_is_idempotent_and_never_stacks_duplicate_blocks(self, manager, vhostd):
        manager.set_hsts(DOMAIN, "max-age=31536000")
        first = (vhostd / DOMAIN).read_text()

        changed = manager.set_hsts(DOMAIN, "max-age=31536000")

        assert changed is False
        assert (vhostd / DOMAIN).read_text() == first
        assert first.count(HstsManager.BLOCK_BEGIN) == 1

    def test_a_changed_value_replaces_the_block_rather_than_stacking(self, manager, vhostd):
        manager.set_hsts(DOMAIN, "max-age=31536000")

        changed = manager.set_hsts(DOMAIN, "max-age=63072000; includeSubDomains")

        text = (vhostd / DOMAIN).read_text()
        assert changed is True
        assert text.count(HstsManager.BLOCK_BEGIN) == 1
        assert "max-age=31536000" not in text
        assert "max-age=63072000; includeSubDomains" in text

    def test_switching_to_off_drops_the_previous_add_header(self, manager, vhostd):
        manager.set_hsts(DOMAIN, "max-age=31536000")

        manager.set_hsts(DOMAIN, "off")

        text = (vhostd / DOMAIN).read_text()
        assert "add_header Strict-Transport-Security" not in text
        assert "proxy_hide_header Strict-Transport-Security;" in text

    def test_foreign_content_in_the_shared_file_survives_untouched(self, manager, vhostd):
        path = vhostd / DOMAIN
        path.write_text(UPLOAD_LIMIT + "\n" + HANDWRITTEN + "\n")

        manager.set_hsts(DOMAIN, "max-age=31536000")

        text = path.read_text()
        assert UPLOAD_LIMIT in text
        assert HANDWRITTEN in text


class TestRemoveHsts:
    def test_removes_the_file_when_nothing_else_remains(self, manager, vhostd):
        manager.set_hsts(DOMAIN, "max-age=31536000")

        manager.remove_hsts(DOMAIN)

        assert not (vhostd / DOMAIN).exists()

    def test_foreign_content_keeps_the_file_alive(self, manager, vhostd):
        manager.set_hsts(DOMAIN, "max-age=31536000")
        path = vhostd / DOMAIN
        path.write_text(path.read_text() + UPLOAD_LIMIT + "\n")

        manager.remove_hsts(DOMAIN)

        assert path.read_text() == UPLOAD_LIMIT + "\n"

    def test_on_a_file_without_our_block_changes_nothing(self, manager, vhostd):
        path = vhostd / DOMAIN
        path.write_text(UPLOAD_LIMIT + "\n")

        manager.remove_hsts(DOMAIN)

        assert path.read_text() == UPLOAD_LIMIT + "\n"

    def test_on_a_missing_file_does_nothing(self, manager, vhostd):
        manager.remove_hsts(DOMAIN)

        assert not (vhostd / DOMAIN).exists()

    def test_a_pure_whitespace_remainder_is_kept_not_treated_as_empty(self, manager, vhostd):
        """Regression: an emptiness check based on `.strip()` treats a foreign remainder that is
        only a blank line as "nothing left" and unlinks the file, losing bytes that were never
        fm's to remove."""
        path = vhostd / DOMAIN
        path.write_text("\n")
        manager.set_hsts(DOMAIN, "max-age=31536000")

        manager.remove_hsts(DOMAIN)

        assert path.read_bytes() == b"\n"


class TestAddThenRemoveIsATrueInverse:
    """Same standard as the sibling `# fm:https-redirect` block
    (`tests/unit/ssl_manager/test_vhost_config_manager.py`): add-then-remove restores the shared
    file byte-for-byte, not merely to semantically equivalent content, including a foreign
    remainder's own leading/trailing blank lines."""

    def test_a_brand_new_domain_returns_to_no_file_at_all(self, manager, vhostd):
        manager.set_hsts(DOMAIN, "max-age=31536000")

        manager.remove_hsts(DOMAIN)

        assert not (vhostd / DOMAIN).exists()

    @pytest.mark.parametrize(
        "original",
        [
            UPLOAD_LIMIT + "\n",
            # The exact shape the sibling redirect-block test was found against: a leading blank
            # line before the first real directive.
            "\n" + HANDWRITTEN + "\n",
            # Trailing blank line, no leading one.
            UPLOAD_LIMIT + "\n\n",
            # No trailing newline at all.
            HANDWRITTEN,
            # A file that also holds the redirect and upload-limit blocks: three independent
            # marked/unmarked regions in one shared file, only one of which this manager owns.
            VhostConfigManager.BLOCK_BEGIN
            + "\n"
            + VhostConfigManager.HTTPS_REDIRECT_CONFIG
            + VhostConfigManager.BLOCK_END
            + "\n"
            + UPLOAD_LIMIT
            + "\n",
        ],
        ids=["plain", "leading-blank-line", "trailing-blank-line", "no-trailing-newline", "redirect+upload-limit"],
    )
    def test_a_shared_file_returns_to_its_pre_add_bytes(self, manager, vhostd, original):
        path = vhostd / DOMAIN
        path.write_text(original)
        before = path.read_bytes()

        manager.set_hsts(DOMAIN, "max-age=31536000")
        assert path.read_bytes() != before  # sanity: set_hsts actually changed the file
        manager.remove_hsts(DOMAIN)

        assert path.read_bytes() == before
