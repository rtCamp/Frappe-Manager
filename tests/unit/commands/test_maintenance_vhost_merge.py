"""Maintenance must share the per-domain vhost.d file with other writers.

jwilder/nginx-proxy has exactly ONE vhost.d/<domain> file, and fm's upload
limit feature (plus hand-written directives) already lives there. Maintenance
owns a marked block inside it; enable/disable must never destroy the rest.
"""

from frappe_manager.commands.maintenance import (
    _has_fm_block,
    _strip_fm_block,
    _vhost_conf,
)

FOREIGN = "client_max_body_size 50m;\n"


def _block() -> str:
    return _vhost_conf("mybench", "a" * 32, "/usr/share/nginx/html", 503, 300, [], [], secure_cookie=False)


def test_block_is_detectable_and_strippable():
    block = _block()
    assert _has_fm_block(block)
    assert _strip_fm_block(block).strip() == ""


def test_enable_over_foreign_content_preserves_it():
    # what enable writes when the file already holds an upload limit
    existing = FOREIGN
    merged = _block() + _strip_fm_block(existing).strip("\n") + "\n"
    assert "client_max_body_size 50m;" in merged
    assert _has_fm_block(merged)
    # disable removes only the block, leaving the foreign directive
    remainder = _strip_fm_block(merged).strip("\n")
    assert remainder == "client_max_body_size 50m;"
    assert not _has_fm_block(remainder)


def test_reenable_replaces_block_without_duplicating_foreign_lines():
    merged = _block() + FOREIGN
    remerged = _block() + _strip_fm_block(merged).strip("\n") + "\n"
    assert remerged.count("client_max_body_size") == 1
    assert remerged.count("# fm:maintenance BEGIN") == 1
