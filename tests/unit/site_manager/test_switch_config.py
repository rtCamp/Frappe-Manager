"""SwitchConfig safety-flag contract: backup_db / rollback_image / rollback_db."""

import pytest

from frappe_manager.site_manager.bench_config import SwitchConfig


def test_defaults():
    sc = SwitchConfig()
    assert sc.backup_db is True  # dump taken by default (insurance)
    assert sc.rollback_image is True  # code-level rollback on failed health gate
    assert sc.rollback_db is False  # DB never auto-restored unless opted in


def test_rollback_db_requires_backup_db():
    with pytest.raises(ValueError, match="backup_db"):
        SwitchConfig(backup_db=False, rollback_db=True)


def test_rollback_db_with_backup_db_is_valid():
    sc = SwitchConfig(backup_db=True, rollback_db=True)
    assert sc.rollback_db is True


def test_old_field_names_are_rejected():
    # extra="forbid": stale keys fail loudly instead of being silently ignored.
    with pytest.raises(ValueError):
        SwitchConfig(backups=True)
    with pytest.raises(ValueError):
        SwitchConfig(restore_on_failure=True)


def test_backup_db_auto_accepted():
    assert SwitchConfig(backup_db="auto").backup_db == "auto"


def test_rollback_db_allows_auto_backup():
    # 'auto' dumps exactly when migrate runs -- the only time a restore is needed.
    assert SwitchConfig(backup_db="auto", rollback_db=True).rollback_db is True


def test_keep_releases_default():
    assert SwitchConfig().keep_releases == 7


def test_old_keep_releases_name_is_rejected():
    # Renamed from releases_retain_limit; extra="forbid" fails loudly so users
    # fix their config instead of the key being silently ignored.
    with pytest.raises(ValueError):
        SwitchConfig(releases_retain_limit=3)
