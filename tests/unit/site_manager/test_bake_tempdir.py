"""`fm bake` writes its large temp trees to fm's data filesystem, not `/tmp`.

A real bake stages the whole provisioned `frappe-bench` (code, venv, built assets) in one temp
tree and a second copy of the assets in another for the nginx image -- several GB. `/tmp` is
commonly a RAM-backed tmpfs (systemd sizes it near half of RAM), so the interpreter default
overflows it with a misleading `[Errno 122] Disk quota exceeded` on a host with tens of GB free
elsewhere. `_bake_tempdir_base` therefore defaults to a directory under `CLI_DIR` (where benches
and images already live, so it is sized for this by definition) and pre-creates it so `mkdtemp`
can use it. An explicit `TMPDIR` still wins, because an operator who set it is pointing bake at
scratch space of their own.
"""

import frappe_manager.site_manager.modules.bake as bake_mod


def test_defaults_to_a_created_dir_on_the_data_filesystem(monkeypatch, tmp_path):
    monkeypatch.setattr(bake_mod, "CLI_DIR", tmp_path)
    monkeypatch.delenv("TMPDIR", raising=False)

    base = bake_mod._bake_tempdir_base()

    assert base == str(tmp_path / "cache" / "bake")
    # Pre-created, or mkdtemp(dir=...) would raise on the missing parent.
    assert (tmp_path / "cache" / "bake").is_dir()


def test_an_explicit_tmpdir_wins(monkeypatch, tmp_path):
    monkeypatch.setattr(bake_mod, "CLI_DIR", tmp_path)
    monkeypatch.setenv("TMPDIR", "/some/operator/scratch")

    # None lets tempfile honour TMPDIR itself, and nothing is created under CLI_DIR.
    assert bake_mod._bake_tempdir_base() is None
    assert not (tmp_path / "cache").exists()
