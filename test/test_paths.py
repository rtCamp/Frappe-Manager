import os
from frappe_manager import paths

def test_default_path(monkeypatch):
    # If env var is not set, should return default path
    monkeypatch.delenv("FRAPPE_REPO_PATH", raising=False)
    assert paths.get_frappe_repo_path() == "./repos/frappe"

def test_env_var_path(monkeypatch):
    # If env var is set, should return the custom path
    monkeypatch.setenv("FRAPPE_REPO_PATH", "/custom/frappe")
    assert paths.get_frappe_repo_path() == "/custom/frappe"
