"""The recently-used-bench cache must be self-provisioning.

``get_cache_file()`` is the single entry point every cache reader/writer goes through
(``update_sites_cache``, ``get_sorted_sites_list``), and it calls ``mkdir(parents=True,
exist_ok=True)`` on the cache directory. Both flags are load-bearing:

* ``parents=True`` — on a fresh machine ``~/.cache`` itself may not exist, and the mkdir is the
  only thing that creates the tree. Without it every bench-selection prompt raises
  FileNotFoundError, and it does so OUTSIDE the callers' ``try``, so the "fail silently if cache
  operations fail" contract does not protect the user.
* ``exist_ok=True`` — the function is called repeatedly within one command, so the second call
  must not blow up on the directory it just created.
"""

import json
from unittest.mock import patch

import pytest

from frappe_manager.utils import callbacks


@pytest.fixture
def cache_paths(tmp_path):
    """Point the module constants at a NESTED, non-existent cache dir (fresh-machine shape)."""
    cache_dir = tmp_path / "home" / ".cache" / "fm"
    cache_file = cache_dir / "recent_sites.json"
    with (
        patch.object(callbacks, "CLI_CACHE_PATH", cache_dir),
        patch.object(callbacks, "CLI_RECENT_USED_SITES_CACHE_PATH", cache_file),
    ):
        yield cache_dir, cache_file


class TestGetCacheFile:
    def test_creates_the_whole_missing_cache_directory_tree(self, cache_paths):
        cache_dir, cache_file = cache_paths
        assert not cache_dir.parent.exists()

        result = callbacks.get_cache_file()

        assert cache_dir.is_dir()
        assert result == cache_file
        assert not result.exists()  # only the directory is provisioned, not the file

    def test_is_idempotent_when_the_directory_already_exists(self, cache_paths):
        cache_dir, cache_file = cache_paths

        first = callbacks.get_cache_file()
        second = callbacks.get_cache_file()

        assert first == second == cache_file
        assert cache_dir.is_dir()

    def test_tolerates_a_pre_existing_directory_it_did_not_create(self, cache_paths):
        cache_dir, cache_file = cache_paths
        cache_dir.mkdir(parents=True)

        assert callbacks.get_cache_file() == cache_file


class TestCacheUsersOnAFreshMachine:
    def test_update_sites_cache_writes_entry_into_a_fresh_cache_dir(self, cache_paths):
        _, cache_file = cache_paths

        callbacks.update_sites_cache("alpha.localhost")

        payload = json.loads(cache_file.read_text())
        assert [entry["name"] for entry in payload["sites"]] == ["alpha.localhost"]

    def test_recently_used_bench_sorts_first_on_a_fresh_machine(self, cache_paths):
        callbacks.update_sites_cache("beta.localhost")

        assert callbacks.get_sorted_sites_list(["alpha.localhost", "beta.localhost"]) == [
            "beta.localhost",
            "alpha.localhost",
        ]

    def test_most_recent_bench_moves_to_the_front_without_duplicating(self, cache_paths):
        _, cache_file = cache_paths
        callbacks.update_sites_cache("alpha.localhost")
        callbacks.update_sites_cache("beta.localhost")
        callbacks.update_sites_cache("alpha.localhost")

        names = [entry["name"] for entry in json.loads(cache_file.read_text())["sites"]]
        assert names == ["alpha.localhost", "beta.localhost"]
