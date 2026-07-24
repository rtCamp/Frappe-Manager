"""migrate='auto' probe: marker parsing + config acceptance."""

import pytest

from frappe_manager.site_manager.bench_config import SwitchConfig
from frappe_manager.site_manager.modules.deploy_orchestrator import (
    MIGRATE_PROBE_MARKER,
    parse_migrate_probe,
)


def test_parse_needed():
    assert parse_migrate_probe([f"{MIGRATE_PROBE_MARKER} needed pending=3 drift=none"]) is True


def test_parse_clean():
    lines = ["console noise", f"{MIGRATE_PROBE_MARKER} clean pending=0 drift=none", "more noise"]
    assert parse_migrate_probe(lines) is False


def test_parse_drift_marks_needed():
    assert parse_migrate_probe([f"{MIGRATE_PROBE_MARKER} needed pending=0 drift=erpnext"]) is True


def test_parse_no_verdict():
    assert parse_migrate_probe(["no marker here"]) is None
    assert parse_migrate_probe([]) is None
    assert parse_migrate_probe(None) is None


def test_config_accepts_auto():
    assert SwitchConfig(migrate="auto").migrate == "auto"
    assert SwitchConfig(migrate=True).migrate is True
    assert SwitchConfig(migrate=False).migrate is False


def test_config_rejects_typo_strings():
    # pydantic coerces boolish strings ("yes"/"true") to bool; a typo of "auto" must still fail.
    with pytest.raises(ValueError):
        SwitchConfig(migrate="atuo")
    assert SwitchConfig(migrate="yes").migrate is True  # boolish string coerces, documented


def test_rolling_eligible_matrix():
    from frappe_manager.site_manager.modules.deploy_orchestrator import rolling_eligible

    assert rolling_eligible(False, True, ["migrate"]) is True  # no migrate
    assert rolling_eligible(True, True, ["migrate"]) is True  # migrate covered by maintenance 503
    assert rolling_eligible(True, False, []) is True  # operator asserts additive
    assert rolling_eligible(True, False, ["migrate"]) is False  # migrate, window disabled -> recreate
    assert rolling_eligible(True, False, ["migrate"], override=True) is True  # CLI forces
    assert rolling_eligible(False, True, ["migrate"], override=False) is False  # CLI disables
