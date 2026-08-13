"""
Tests for MigrationValidator version-support and bench-scanning decisions.

Contracts defended here:

1. ``target_benches is None`` means "infrastructure-only migration". The validator must
   short-circuit: it must NOT scan the benches directory at all, and it must report
   "nothing to migrate" (min version == current version, needs-migration == False).
   Inverting that guard is invisible in the return value alone (an unfiltered scan
   processes zero benches), so these tests also pin the collaborator interaction.
2. When benches ARE targeted, the scan result must actually be used: the minimum bench
   version is the lowest version among processed benches, and a bench older than the
   current version means migration is needed.
3. ``validate_version_support`` must REFUSE (return False) for a version below
   MINIMUM_SUPPORTED_VERSION, with the exact boundary being inclusive: the minimum
   supported version itself is accepted.
"""

from pathlib import Path
from unittest.mock import Mock, patch

import pytest

from frappe_manager.migration_manager.migration_constants import MINIMUM_SUPPORTED_VERSION
from frappe_manager.migration_manager.migration_validator import BenchFilter, MigrationValidator
from frappe_manager.migration_manager.version import Version

VALIDATOR_MODULE = "frappe_manager.migration_manager.migration_validator"


def make_validator(target_benches, exclude_benches=None, prev="0.18.0", current="0.20.0"):
    """Build a MigrationValidator with a mock output handler."""
    bench_filter = BenchFilter(target_benches=target_benches, exclude_benches=exclude_benches or [])
    return MigrationValidator(
        prev_version=Version(prev),
        current_version=Version(current),
        bench_filter=bench_filter,
        output_handler=Mock(),
    )


def patch_benches(bench_versions: dict[str, str]):
    """Patch the benches collaborators so ``bench_name -> version`` is the whole world.

    ``get_all_benches()`` returns ``name -> <bench>/docker-compose.yml`` and the validator
    reads ``bench_path.parent``, which is what ``get_bench_migration_version`` receives.
    """
    all_benches = {name: Path("/benches") / name / "docker-compose.yml" for name in bench_versions}

    def fake_version(bench_path: Path) -> Version:
        return Version(bench_versions[bench_path.name])

    benches_manager = Mock()
    benches_manager.get_all_benches = Mock(return_value=all_benches)
    benches_cls = Mock(return_value=benches_manager)
    version_fn = Mock(side_effect=fake_version)

    return (
        patch(f"{VALIDATOR_MODULE}.MigrationBenches", benches_cls),
        patch(f"{VALIDATOR_MODULE}.get_bench_migration_version", version_fn),
        benches_cls,
        version_fn,
    )


class TestGetMinimumBenchVersion:
    """MigrationValidator.get_minimum_bench_version()"""

    def test_no_target_benches_skips_bench_scan_entirely(self):
        """target_benches is None => infrastructure-only: benches must never be read.

        The return value alone cannot distinguish "short-circuited" from "scanned and
        matched nothing", so assert the collaborators were not touched.
        """
        validator = make_validator(target_benches=None)
        benches_patch, version_patch, benches_cls, version_fn = patch_benches({"bench-a": "0.18.0"})

        with benches_patch, version_patch:
            result = validator.get_minimum_bench_version()

        assert result == Version("0.20.0"), "Must report current version when no benches are targeted"
        benches_cls.assert_not_called()
        version_fn.assert_not_called()

    def test_targeted_benches_return_lowest_bench_version(self):
        """With benches targeted, the scan drives the result: the lowest version wins."""
        validator = make_validator(target_benches=["bench-a", "bench-b"])
        benches_patch, version_patch, benches_cls, version_fn = patch_benches(
            {"bench-a": "0.19.0", "bench-b": "0.18.0"},
        )

        with benches_patch, version_patch:
            result = validator.get_minimum_bench_version()

        assert result == Version("0.18.0"), "Minimum across targeted benches must be returned"
        benches_cls.assert_called_once()
        assert version_fn.call_count == 2

    def test_untargeted_and_excluded_benches_do_not_lower_the_minimum(self):
        """Only benches that pass the filter contribute to the minimum."""
        validator = make_validator(target_benches=["bench-b", "bench-c"], exclude_benches=["bench-c"])
        benches_patch, version_patch, _, version_fn = patch_benches(
            {"bench-a": "0.17.0", "bench-b": "0.19.0", "bench-c": "0.17.0"},
        )

        with benches_patch, version_patch:
            result = validator.get_minimum_bench_version()

        assert result == Version("0.19.0"), "Excluded/untargeted benches must be ignored"
        assert version_fn.call_count == 1

    def test_bench_newer_than_current_does_not_raise_the_minimum(self):
        """current_version is the ceiling of the minimum."""
        validator = make_validator(target_benches=["bench-a"], current="0.20.0")
        benches_patch, version_patch, _, _ = patch_benches({"bench-a": "0.21.0"})

        with benches_patch, version_patch:
            result = validator.get_minimum_bench_version()

        assert result == Version("0.20.0")


class TestCheckBenchesNeedMigration:
    """MigrationValidator.check_benches_need_migration()"""

    def test_no_target_benches_skips_bench_scan_entirely(self):
        """target_benches is None => no bench migration is needed and no bench is read."""
        validator = make_validator(target_benches=None)
        benches_patch, version_patch, benches_cls, version_fn = patch_benches({"bench-a": "0.18.0"})

        with benches_patch, version_patch:
            result = validator.check_benches_need_migration()

        assert result is False, "Infrastructure-only migration must not claim benches need migration"
        benches_cls.assert_not_called()
        version_fn.assert_not_called()

    def test_targeted_bench_below_current_version_needs_migration(self):
        """A targeted bench older than current => True (the scan result must be used)."""
        validator = make_validator(target_benches=["bench-a"], current="0.19.0")
        benches_patch, version_patch, benches_cls, _ = patch_benches({"bench-a": "0.18.0"})

        with benches_patch, version_patch:
            result = validator.check_benches_need_migration()

        assert result is True
        benches_cls.assert_called_once()

    def test_targeted_bench_at_current_version_needs_no_migration(self):
        """Boundary: bench version == current version is already migrated."""
        validator = make_validator(target_benches=["bench-a"], current="0.19.0")
        benches_patch, version_patch, _, _ = patch_benches({"bench-a": "0.19.0"})

        with benches_patch, version_patch:
            result = validator.check_benches_need_migration()

        assert result is False

    def test_only_untargeted_old_bench_needs_no_migration(self):
        """An old bench that is not targeted must not trigger migration."""
        validator = make_validator(target_benches=["bench-b"], current="0.19.0")
        benches_patch, version_patch, _, version_fn = patch_benches({"bench-a": "0.17.0", "bench-b": "0.19.0"})

        with benches_patch, version_patch:
            result = validator.check_benches_need_migration()

        assert result is False
        assert version_fn.call_count == 1


class TestValidateVersionSupport:
    """MigrationValidator.validate_version_support()"""

    def test_version_below_minimum_is_refused(self):
        """Too-old version => migration must be REFUSED (False), with guidance printed."""
        validator = make_validator(target_benches=None, current="0.20.0")

        result = validator.validate_version_support(Version("0.17.0"))

        assert result is False, "Migration from an unsupported version must be refused"
        assert validator.output.display_error.call_count == 3
        messages = " ".join(str(call.args[0]) for call in validator.output.display_error.call_args_list)
        assert "0.17.0" in messages
        assert MINIMUM_SUPPORTED_VERSION.version in messages

    def test_minimum_supported_version_is_accepted(self):
        """Boundary: the minimum supported version itself is supported."""
        validator = make_validator(target_benches=None)

        result = validator.validate_version_support(MINIMUM_SUPPORTED_VERSION)

        assert result is True
        validator.output.display_error.assert_not_called()

    @pytest.mark.parametrize("version", ["0.19.0", "0.20.0", "1.0.0"])
    def test_versions_above_minimum_are_accepted(self, version):
        validator = make_validator(target_benches=None, current="1.0.0")

        result = validator.validate_version_support(Version(version))

        assert result is True
        validator.output.display_error.assert_not_called()

    def test_fresh_install_sentinel_is_accepted(self):
        """0.0.0 means "no previous install" and must bypass the minimum check."""
        validator = make_validator(target_benches=None)

        result = validator.validate_version_support(Version("0.0.0"))

        assert result is True
        validator.output.display_error.assert_not_called()
