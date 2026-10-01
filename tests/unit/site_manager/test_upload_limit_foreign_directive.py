"""An operator's own `client_max_body_size` in the shared `vhostd/<domain>` file and fm's own
upload-limit fragment cannot coexist: nginx treats a second `client_max_body_size` in one server
context as FATAL ("directive is duplicate"), even when the two copies arrive through different
`include`d files, because `include` does not open a new context. Left in place, the next proxy
reload takes down every bench on the host, not just this domain.

`claim_foreign_upload_limit` removes a foreign copy before fm writes its own fragment, and
`Bench.apply_upload_limit` must say what it removed -- never silent in either direction.
"""

from unittest.mock import MagicMock

import pytest

from frappe_manager.services_manager.proxy_dropins import INCLUDE_BEGIN, INCLUDE_END, ProxyDropins
from frappe_manager.site_manager.modules.upload_limit import claim_foreign_upload_limit, upload_limit_conf
from tests.unit.site_manager.test_site_contract import SITE, build_bench

BOOTSTRAP = f"{INCLUDE_BEGIN}\ninclude /etc/nginx/fm.d/vhost/{SITE}/*.conf;\n{INCLUDE_END}\n"


@pytest.fixture
def harness(tmp_path):
    return build_bench(tmp_path)


class TestClaimForeignUploadLimit:
    """Pure-function behaviour of the scan-and-remove over a single vhost.d file."""

    def test_removes_the_directive_and_returns_its_value(self, tmp_path):
        vhost_file = tmp_path / SITE
        vhost_file.write_text(f"{BOOTSTRAP}client_max_body_size 75m;\nallow 10.0.0.0/8;\n")

        removed = claim_foreign_upload_limit(vhost_file)

        assert removed == "75m"
        # The `# fm:include` bootstrap and the unrelated foreign line survive byte for byte; no
        # stray blank line is left where the directive's own line was.
        assert vhost_file.read_text() == f"{BOOTSTRAP}allow 10.0.0.0/8;\n"

    def test_no_directive_returns_none_and_leaves_the_file_untouched(self, tmp_path):
        vhost_file = tmp_path / SITE
        original = f"{BOOTSTRAP}allow 10.0.0.0/8;\n"
        vhost_file.write_text(original)

        assert claim_foreign_upload_limit(vhost_file) is None
        assert vhost_file.read_text() == original

    def test_a_missing_file_returns_none(self, tmp_path):
        assert claim_foreign_upload_limit(tmp_path / "absent-domain") is None

    def test_leading_and_trailing_blank_lines_around_the_directive_survive(self, tmp_path):
        """Only the directive's own line goes; the file's own surrounding newlines are not fm's
        to touch."""
        vhost_file = tmp_path / SITE
        vhost_file.write_text("\nclient_max_body_size 50m;\n\n")

        removed = claim_foreign_upload_limit(vhost_file)

        assert removed == "50m"
        assert vhost_file.read_text() == "\n\n"


class TestApplyUploadLimitClaimsForeignDirective:
    """`Bench.apply_upload_limit` is the only writer of the `upload-limit` fragment; this is
    where a foreign directive in the same file must be claimed before fm's own copy goes in."""

    def _dropins_and_vhost_file(self, harness):
        dropins = ProxyDropins.for_services_path(harness.services.path)
        return dropins, dropins.vhostd_dir / SITE

    def test_a_hand_written_directive_is_removed_and_the_operator_is_warned(self, harness):
        """Left in place, fm's fragment below becomes a SECOND `client_max_body_size` in the
        same nginx server context: a duplicate that is fatal to nginx on the next reload and
        takes down every bench on the host, not just this domain."""
        dropins, vhost_file = self._dropins_and_vhost_file(harness)
        vhost_file.write_text("client_max_body_size 200m;\n")

        changed = harness.bench.apply_upload_limit()

        assert changed is True
        fragment = dropins.fragment_path(SITE, "upload-limit")
        assert fragment.read_text() == upload_limit_conf(harness.bench.bench_config.upload_limit)
        assert "client_max_body_size 200m;" not in vhost_file.read_text()
        warned = " ".join(str(c.args[0]) for c in harness.bench.output.warning.call_args_list)
        assert SITE in warned
        assert "200m" in warned
        assert "fm update --upload-limit" in warned

    def test_other_foreign_content_and_the_bootstrap_survive_byte_for_byte(self, harness):
        dropins, vhost_file = self._dropins_and_vhost_file(harness)
        vhost_file.write_text(f"{BOOTSTRAP}allow 10.0.0.0/8;\nclient_max_body_size 200m;\n")

        harness.bench.apply_upload_limit()

        assert vhost_file.read_text() == f"{BOOTSTRAP}allow 10.0.0.0/8;\n"

    def test_no_foreign_directive_means_no_warning_and_no_spurious_change(self, harness):
        """A fragment and bootstrap that already match fm's desired state, and no foreign
        directive, must report no change and no warning -- the claim path must never manufacture
        either on its own."""
        dropins, vhost_file = self._dropins_and_vhost_file(harness)
        vhost_file.write_text(BOOTSTRAP)
        size = harness.bench.bench_config.upload_limit.lower()
        fragment = dropins.fragment_path(SITE, "upload-limit")
        fragment.parent.mkdir(parents=True, exist_ok=True)
        fragment.write_text(upload_limit_conf(size))

        changed = harness.bench.apply_upload_limit()

        assert changed is False
        harness.bench.output.warning.assert_not_called()

    def test_the_reload_still_happens_exactly_once(self, harness):
        """Claiming a foreign directive folds into the same `changed` flag the fragment write
        already drives the caller's reload with; it must not add a reload of its own on top of
        the single `upload_limit_changed or hsts_changed` reload `start()` already issues."""
        _, vhost_file = self._dropins_and_vhost_file(harness)
        vhost_file.write_text("client_max_body_size 200m;\n")
        harness.bench.docker_ops = MagicMock()
        harness.bench.orchestrator = MagicMock()

        harness.bench.start()

        assert harness.services.nginx_controller.reload.call_count == 1
