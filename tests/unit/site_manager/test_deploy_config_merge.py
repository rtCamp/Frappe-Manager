"""Guards the merge semantics fm's deploy config-merge relies on (#323).

DeployOrchestrator._apply_config_merges writes [deploy].common_site_config /
site_config via set_common_bench_config / set_bench_site_config, which delegate
to save_dict_to_file. That MUST merge (preserve unrelated keys), not overwrite —
otherwise a deploy config-merge would wipe db/redis wiring from
common_site_config.json.
"""

import json

from frappe_manager.utils.helpers import save_dict_to_file


def test_save_dict_to_file_merges_preserving_existing(tmp_path):
    path = tmp_path / "common_site_config.json"
    path.write_text(json.dumps({"db_host": "mariadb", "redis_cache": "redis://x"}))

    save_dict_to_file({"maintenance_mode": 1, "db_host": "newhost"}, path)

    result = json.loads(path.read_text())
    assert result["redis_cache"] == "redis://x"  # untouched key preserved
    assert result["maintenance_mode"] == 1  # new key added
    assert result["db_host"] == "newhost"  # existing key overridden


def test_save_dict_to_file_nested_values(tmp_path):
    path = tmp_path / "site_config.json"
    path.write_text(json.dumps({"encryption_key": "k"}))

    save_dict_to_file({"limits": {"space_usage": 100}}, path)

    result = json.loads(path.read_text())
    assert result["encryption_key"] == "k"
    assert result["limits"] == {"space_usage": 100}
