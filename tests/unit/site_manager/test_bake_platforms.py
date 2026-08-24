"""[build].platform contract (BakeManager.resolve_target_platform).

None = native. A single platform is honored, cross-building under emulation when it
differs from the daemon arch -- provision source only, because a workspace snapshot
already carries host-arch binaries.
"""

import pytest

from frappe_manager.site_manager.modules.bake import BakeError, BakeManager


class TestResolveTargetPlatform:
    def test_none_builds_native_silently(self):
        assert BakeManager.resolve_target_platform(None, "amd64", "provision") == (None, None)

    def test_matching_platform_is_explicit_but_quiet(self):
        platform, info = BakeManager.resolve_target_platform("linux/amd64", "amd64", "provision")
        assert (platform, info) == ("linux/amd64", None)

    def test_cross_platform_is_honored_with_emulation_notice(self):
        # The Mac case: arm64 daemon, amd64 target -> honored, not just warned about.
        platform, info = BakeManager.resolve_target_platform("linux/amd64", "arm64", "provision")
        assert platform == "linux/amd64"
        assert info is not None and "emulation" in info

    def test_cross_platform_rejects_workspace_source(self):
        # A workspace snapshot contains host-arch binaries; only provision can cross-build.
        with pytest.raises(BakeError, match="source='provision'"):
            BakeManager.resolve_target_platform("linux/amd64", "arm64", "workspace")

    def test_unknown_daemon_arch_still_honors_target(self):
        # Introspection failure: pass the platform through; docker will enforce it.
        assert BakeManager.resolve_target_platform("linux/amd64", None, "provision") == ("linux/amd64", None)


class TestManifestArchitectures:
    """parse_manifest_architectures: registry manifest-list -> arch set (or None)."""

    def test_manifest_list_yields_arches(self):
        manifest = """{"schemaVersion": 2, "manifests": [
            {"platform": {"architecture": "amd64", "os": "linux"}},
            {"platform": {"architecture": "arm64", "os": "linux"}}]}"""
        assert BakeManager.parse_manifest_architectures(manifest) == {"amd64", "arm64"}

    def test_buildx_attestation_entries_ignored(self):
        manifest = """{"manifests": [
            {"platform": {"architecture": "amd64", "os": "linux"}},
            {"platform": {"architecture": "unknown", "os": "unknown"}}]}"""
        assert BakeManager.parse_manifest_architectures(manifest) == {"amd64"}

    def test_single_image_manifest_is_undeterminable(self):
        # No "manifests" key -> cannot prove anything -> None (never a failure).
        assert BakeManager.parse_manifest_architectures('{"schemaVersion": 2, "config": {}}') is None

    def test_garbage_is_undeterminable(self):
        assert BakeManager.parse_manifest_architectures("not json at all") is None
        assert BakeManager.parse_manifest_architectures("[1, 2]") is None
