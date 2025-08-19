import os

def get_frappe_repo_path() -> str:
    """
    Returns the frappe repository path from env var FRAPPE_REPO_PATH.
    Defaults to './repos/frappe' if not set.
    """
    return os.getenv("FRAPPE_REPO_PATH", "./repos/frappe")
