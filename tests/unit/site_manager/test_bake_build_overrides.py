"""Contract tests for BakeManager.apply_build_overrides (#323).

[build].python_version / node_version must override the bench's create-time /
detected versions before provisioning bakes the image; a missing [build] or
missing field leaves the existing version untouched. A non-default
[build].platforms warns (multi/cross-arch not yet honored).
"""

from frappe_manager.site_manager.bench_config import (
    BenchConfig,
    DeployBuildConfig,
    FMBenchEnvType,
)
from frappe_manager.site_manager.modules.bake import BakeManager


class _Out:
    def __init__(self):
        self.warnings = []

    def warning(self, msg):
        self.warnings.append(msg)


def _bench(tmp_path, build=None):
    return BenchConfig(
        name="x.localhost",
        developer_mode=False,
        admin_tools=False,
        environment_type=FMBenchEnvType.prod,
        root_path=tmp_path / "bench_config.toml",
        build=build,
    )


def test_build_versions_override_bench(tmp_path):
    bc = _bench(tmp_path, build=DeployBuildConfig(python_version="3.12", node_version="20"))
    bc.python_version = "3.11"  # create-time value
    bc.node_version = "18"
    BakeManager.apply_build_overrides(bc)
    assert bc.python_version == "3.12"
    assert bc.node_version == "20"


def test_no_build_is_noop(tmp_path):
    bc = _bench(tmp_path)  # build=None
    bc.python_version = "3.11"
    BakeManager.apply_build_overrides(bc)
    assert bc.python_version == "3.11"


def test_partial_build_leaves_unset_field(tmp_path):
    bc = _bench(tmp_path, build=DeployBuildConfig(python_version="3.12"))  # node_version None
    bc.node_version = "18"
    BakeManager.apply_build_overrides(bc)
    assert bc.python_version == "3.12"
    assert bc.node_version == "18"  # untouched


def test_default_platforms_no_warning(tmp_path):
    out = _Out()
    BakeManager.apply_build_overrides(_bench(tmp_path, build=DeployBuildConfig(platforms=["linux/amd64"])), out)
    assert out.warnings == []


def test_custom_platforms_warns(tmp_path):
    out = _Out()
    BakeManager.apply_build_overrides(_bench(tmp_path, build=DeployBuildConfig(platforms=["linux/arm64"])), out)
    assert len(out.warnings) == 1
    assert "not yet honored" in out.warnings[0]
