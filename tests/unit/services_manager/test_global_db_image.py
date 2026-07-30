"""The global-db engine is pinned in three places and must not drift.

`services.py` seeds `mariadb/conf` by copying `/etc/mysql` out of the same image the
compose file runs, so a stale tag in either place seeds one major version's config
for another's server. The tag itself tracks what Frappe's CI tests against, not the
soft warning bounds in frappe/database/mariadb/setup_db.py.
"""

import re
from pathlib import Path

import yaml

from frappe_manager import GLOBAL_DB_IMAGE

_TEMPLATES = Path("frappe_manager/templates")
_SERVICES_TEMPLATES = (
    _TEMPLATES / "docker-compose.services.tmpl",
    _TEMPLATES / "docker-compose.services.osx.tmpl",
)


def _global_db_image(text: str) -> str:
    # the global-db service is the first block in both templates
    block = text.split("global-db:", 1)[1]
    match = re.search(r"^\s*image:\s*(\S+)", block, re.MULTILINE)
    assert match, "no image line found under global-db"
    return match.group(1)


def test_both_service_templates_pin_the_constant():
    for template in _SERVICES_TEMPLATES:
        assert _global_db_image(template.read_text()) == GLOBAL_DB_IMAGE, template


def test_the_pin_is_a_mariadb_tag_with_an_explicit_version():
    # `latest` would drift past the range frappe tests, and a bare `mariadb` is worse.
    repo, _, tag = GLOBAL_DB_IMAGE.partition(":")
    assert repo == "mariadb"
    assert tag
    assert tag != "latest"
    assert re.fullmatch(r"\d+\.\d+(\.\d+)?", tag), tag


def test_the_pin_stays_inside_the_range_frappe_declares():
    # frappe/database/mariadb/setup_db.py warns below 10.6 and above 11.8 on v16.
    # Crossing either bound should be a deliberate edit here, not a silent bump.
    major, minor = (int(part) for part in GLOBAL_DB_IMAGE.partition(":")[2].split(".")[:2])
    assert (major, minor) >= (10, 6)
    assert (major, minor) <= (11, 8)


def test_engine_flags_are_exactly_the_three_that_are_needed():
    # Asserted as an exact list, not a subset: --skip-innodb-read-only-compressed was
    # only needed on MariaDB 10.6.1-10.6.5, where innodb_read_only_compressed
    # defaulted to ON and frappe's COMPRESSED core tables became read-only. The engine
    # defaults it off again from 10.6.6, so re-adding it here would be carrying a
    # workaround for a version fm no longer pins.
    expected = [
        "--character-set-server=utf8mb4",
        "--collation-server=utf8mb4_unicode_ci",
        "--skip-character-set-client-handshake",
    ]
    for template in _SERVICES_TEMPLATES:
        compose = yaml.safe_load(template.read_text())
        assert compose["services"]["global-db"]["command"] == expected, template


def test_engine_auto_upgrades_system_tables_on_a_version_change():
    # Without this the container starts on a newer engine with the previous version's
    # system tables, which surfaces later as confusing privilege and schema errors.
    for template in _SERVICES_TEMPLATES:
        compose = yaml.safe_load(template.read_text())
        assert compose["services"]["global-db"]["environment"]["MARIADB_AUTO_UPGRADE"] == 1, template
