"""Contract tests for `fm bake --include` ([build].include).

`BakeManager._apply_includes` copies extra host paths into the build context's
frappe-bench tree (src or src:dest; dest relative to /workspace/frappe-bench),
overriding existing files, with guardrails against escaping the bench root.
"""

from unittest.mock import MagicMock

import pytest

from frappe_manager.site_manager.bench_config import BenchConfig, FMBenchEnvType
from frappe_manager.site_manager.modules.bake import BakeError, BakeManager


def _manager(tmp_path):
    bc = BenchConfig(
        name="x.localhost",
        developer_mode=False,
        admin_tools=False,
        environment_type=FMBenchEnvType.prod,
        root_path=tmp_path / "bench_config.toml",
    )
    return BakeManager(bc, output_handler=MagicMock())


def test_include_file_with_explicit_dest(tmp_path):
    src = tmp_path / "patches.txt"
    src.write_text("patch")
    fb = tmp_path / "ctx" / "frappe-bench"
    fb.mkdir(parents=True)
    _manager(tmp_path)._apply_includes(fb, [f"{src}:sites/patches.txt"])  # noqa: SLF001
    assert (fb / "sites" / "patches.txt").read_text() == "patch"


def test_include_dir_and_default_dest(tmp_path):
    d = tmp_path / "fixtures"
    (d / "sub").mkdir(parents=True)
    (d / "sub" / "a.json").write_text("{}")
    fb = tmp_path / "ctx" / "frappe-bench"
    fb.mkdir(parents=True)
    _manager(tmp_path)._apply_includes(fb, [str(d)])  # noqa: SLF001  (dest defaults to basename)
    assert (fb / "fixtures" / "sub" / "a.json").read_text() == "{}"


def test_include_overrides_existing_file(tmp_path):
    src = tmp_path / "hooks.py"
    src.write_text("OVERRIDE")
    fb = tmp_path / "ctx" / "frappe-bench"
    (fb / "apps" / "erpnext").mkdir(parents=True)
    (fb / "apps" / "erpnext" / "hooks.py").write_text("original")
    _manager(tmp_path)._apply_includes(fb, [f"{src}:apps/erpnext/hooks.py"])  # noqa: SLF001
    assert (fb / "apps" / "erpnext" / "hooks.py").read_text() == "OVERRIDE"


def test_missing_source_errors(tmp_path):
    fb = tmp_path / "ctx" / "frappe-bench"
    fb.mkdir(parents=True)
    with pytest.raises(BakeError):
        _manager(tmp_path)._apply_includes(fb, [f"{tmp_path / 'nope.txt'}:sites/x"])  # noqa: SLF001


def test_absolute_dest_rejected(tmp_path):
    src = tmp_path / "f"
    src.write_text("x")
    fb = tmp_path / "ctx" / "frappe-bench"
    fb.mkdir(parents=True)
    with pytest.raises(BakeError):
        _manager(tmp_path)._apply_includes(fb, [f"{src}:/etc/passwd"])  # noqa: SLF001


def test_parent_escape_dest_rejected(tmp_path):
    src = tmp_path / "f"
    src.write_text("x")
    fb = tmp_path / "ctx" / "frappe-bench"
    fb.mkdir(parents=True)
    with pytest.raises(BakeError):
        _manager(tmp_path)._apply_includes(fb, [f"{src}:../../escape"])  # noqa: SLF001


def test_include_dest_creates_the_whole_missing_parent_chain(tmp_path):
    """A dest can point deeper than anything the build context holds yet.

    Dropping a patch into `apps/<app>/<app>/patches/<version>/` is the ordinary use of
    `--include`, and none of those levels exist in a context populated from an image or a bare
    workspace, so the entire parent chain has to be created -- not just one missing level.
    """
    src = tmp_path / "fix_stock_ledger.py"
    src.write_text("def execute():\n    pass\n")
    fb = tmp_path / "ctx" / "frappe-bench"
    fb.mkdir(parents=True)

    dest_rel = "apps/erpnext/erpnext/patches/v15_0/fix_stock_ledger.py"
    _manager(tmp_path)._apply_includes(fb, [f"{src}:{dest_rel}"])  # noqa: SLF001

    assert (fb / dest_rel).read_text() == "def execute():\n    pass\n"
