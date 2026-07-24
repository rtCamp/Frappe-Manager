"""Contract tests for `fm bake --source workspace` (workspace snapshot).

`BakeManager._copy_workspace` filtered-copies a bench's on-disk frappe-bench into
the build context: code + venv + built assets are kept, while dev/site state
(per-site data, its secrets, logs, pids, sockets, caches, .git) is excluded and
the sites files are reseeded clean.
"""

import json

from frappe_manager.site_manager.modules.bake import BakeManager


def _make_workspace(root):
    fb = root / "workspace" / "frappe-bench"
    # code + deps + assets we want to keep
    (fb / "apps" / "erpnext").mkdir(parents=True)
    (fb / "apps" / "erpnext" / "hooks.py").write_text("app_name = 'erpnext'")
    (fb / "apps" / "erpnext" / ".git").mkdir()
    (fb / "apps" / "erpnext" / ".git" / "config").write_text("[core]")
    (fb / "apps" / "erpnext" / "__pycache__").mkdir()
    (fb / "apps" / "erpnext" / "__pycache__" / "x.pyc").write_text("bytecode")
    (fb / "env" / "bin").mkdir(parents=True)
    (fb / "env" / "bin" / "python").write_text("#!/workspace/frappe-bench/env/bin/python")
    (fb / ".uv").mkdir()
    (fb / ".uv" / "cpython").write_text("py")
    # sites: keep assets/apps.txt/apps.json, drop site data + common_site_config
    sites = fb / "sites"
    sites.mkdir()
    (sites / "apps.txt").write_text("frappe\nerpnext\n")
    (sites / "apps.json").write_text("{}")
    (sites / "assets").mkdir()
    (sites / "assets" / "erpnext.bundle.js").write_text("built")
    (sites / "common_site_config.json").write_text(json.dumps({"db_host": "dev", "encryption_key": "SECRET"}))
    site = sites / "fm.localhost"
    site.mkdir()
    (site / "site_config.json").write_text(json.dumps({"encryption_key": "SITE_SECRET"}))
    # volatile
    (fb / "logs").mkdir()
    (fb / "logs" / "web.log").write_text("noise")
    (fb / "config" / "pids").mkdir(parents=True)
    (fb / "config" / "pids" / "x.pid").write_text("123")
    (fb / "config" / "supervisor.conf").write_text("[program]")
    return fb


def test_copy_keeps_code_venv_assets(tmp_path):
    src = _make_workspace(tmp_path / "bench")
    dest = tmp_path / "ctx" / "frappe-bench"
    BakeManager._copy_workspace(src, dest)  # noqa: SLF001

    assert (dest / "apps" / "erpnext" / "hooks.py").read_text() == "app_name = 'erpnext'"
    assert (dest / "env" / "bin" / "python").exists()
    assert (dest / ".uv" / "cpython").exists()
    assert (dest / "sites" / "assets" / "erpnext.bundle.js").read_text() == "built"
    assert (dest / "sites" / "apps.txt").read_text() == "frappe\nerpnext\n"  # real apps.txt preserved
    assert (dest / "sites" / "apps.json").exists()
    assert (dest / "config" / "supervisor.conf").exists()  # config kept (only pids cleared)


def test_copy_excludes_dev_and_site_state(tmp_path):
    src = _make_workspace(tmp_path / "bench")
    dest = tmp_path / "ctx" / "frappe-bench"
    BakeManager._copy_workspace(src, dest)  # noqa: SLF001

    # per-site data (with secrets) dropped
    assert not (dest / "sites" / "fm.localhost").exists()
    # common_site_config reseeded clean (no dev secrets)
    assert (dest / "sites" / "common_site_config.json").read_text() == "{}"
    # volatile + vcs + caches excluded
    assert not (dest / "apps" / "erpnext" / ".git").exists()
    assert not (dest / "apps" / "erpnext" / "__pycache__").exists()
    assert list((dest / "logs").iterdir()) == []
    assert list((dest / "config" / "pids").iterdir()) == []


def test_copy_reseeds_appstxt_when_missing(tmp_path):
    src = tmp_path / "bench" / "workspace" / "frappe-bench"
    (src / "sites").mkdir(parents=True)
    (src / "apps").mkdir()
    dest = tmp_path / "ctx" / "frappe-bench"
    BakeManager._copy_workspace(src, dest)  # noqa: SLF001
    assert (dest / "sites" / "apps.txt").read_text() == "frappe\n"
    assert (dest / "sites" / "common_site_config.json").read_text() == "{}"
