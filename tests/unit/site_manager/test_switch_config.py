"""SwitchConfig safety-flag contract: backup_db / rollback_image / rollback_db."""

import pytest
from pydantic import ValidationError

from frappe_manager.site_manager.bench_config import SwitchConfig
from frappe_manager.utils.config_keys import collect_unknown_keys


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


def test_old_field_names_are_retained_as_unknown_extras():
    # extra="allow": a stale key is retained (collectible) rather than raising or vanishing.
    sc = SwitchConfig(backups=True)
    assert collect_unknown_keys(sc) == ["backups"]

    sc = SwitchConfig(restore_on_failure=True)
    assert collect_unknown_keys(sc) == ["restore_on_failure"]


def test_backup_db_auto_accepted():
    assert SwitchConfig(backup_db="auto").backup_db == "auto"


def test_rollback_db_allows_auto_backup():
    # 'auto' dumps exactly when migrate runs -- the only time a restore is needed.
    assert SwitchConfig(backup_db="auto", rollback_db=True).rollback_db is True


def test_migrate_auto_is_rejected():
    # `migrate = "auto"` used to probe the new image for pending patches and app-version drift and
    # skip the migrate when it found neither. The probe cannot see a DocType field change: that
    # ships with no patch and no version bump, so it reported "clean" while `bench migrate` would
    # still have run sync_schema. The mode is gone and `migrate` is a plain bool, so the string is
    # now a load-time error rather than a silently skipped schema change.
    with pytest.raises(ValidationError):
        SwitchConfig(migrate="auto")
    assert SwitchConfig(migrate=True).migrate is True
    assert SwitchConfig(migrate=False).migrate is False


def test_migrate_still_coerces_boolish_strings():
    # A TOML `migrate = "yes"` stays a documented true; only a non-boolish string fails.
    assert SwitchConfig(migrate="yes").migrate is True
    with pytest.raises(ValidationError):
        SwitchConfig(migrate="atuo")


def test_keep_releases_default():
    assert SwitchConfig().keep_releases == 7


def test_old_keep_releases_name_is_retained_as_an_unknown_extra():
    # Renamed from releases_retain_limit; extra="allow" retains the stale key (collectible)
    # rather than silently coercing it into the new field or raising.
    sc = SwitchConfig(releases_retain_limit=3)
    assert sc.keep_releases == 7  # the real field keeps its default, untouched by the old name
    assert collect_unknown_keys(sc) == ["releases_retain_limit"]


def test_search_replace_is_gone_from_the_model():
    # `search_replace` advertised "run search-and-replace in DB after restore" and was never
    # read: _restore_db only ever imports fm's OWN dump of THIS site, so there is no other
    # site's URL to rewrite. It was deleted once on that reasoning alone, which broke every
    # command that loaded a bench carrying `search_replace = true` while SwitchConfig was
    # extra="forbid" (observed live: it took down `fm info` and `fm ssl list`). It is gone again
    # in 0.20.0, this time with the loader filtering the stale key at the `--config` overlay seam
    # and the bench migration stripping it from disk; the model itself now tolerates it too
    # (extra="allow"), retaining it as an unknown key for a caller to act on rather than raising.
    # See test_bench_config_toml.py::test_a_bench_config_carrying_keys_removed_in_0_20_0_still_loads.
    sc = SwitchConfig(search_replace=True)
    assert collect_unknown_keys(sc) == ["search_replace"]
