"""Custom worker (`common_site_config.json` `workers` key) safety.

Two contracts:
1. The `workers` dict is validated before it reaches the supervisor template,
   which subscripts entries blindly; malformed entries must fail loud at sync
   time, naming the queue and key.
2. Regenerating the split supervisor confs removes stale per-worker files, so
   a queue deleted from the config provably disappears from the generated
   worker services instead of running as an orphan forever.
"""

import configparser
from unittest.mock import MagicMock

import pytest

from frappe_manager.site_manager.modules.bench_supervisor import (
    BenchSupervisor,
    validate_custom_workers,
)


class TestValidateCustomWorkers:
    def test_none_means_no_custom_workers(self):
        assert validate_custom_workers(None) == {}

    def test_valid_entries_normalize_with_defaults(self):
        out = validate_custom_workers({"reports": {"timeout": 5000, "background_workers": 2}, "email": {}})
        assert out["reports"] == {"timeout": 5000, "background_workers": 2}
        # timeout defaults to 300 (frappe's queue default) instead of rendering
        # an empty stopwaitsecs; background_workers falls back to the global.
        assert out["email"] == {"timeout": 300, "background_workers": None}

    def test_workers_must_be_a_dict(self):
        with pytest.raises(ValueError, match="must be an object"):
            validate_custom_workers("myqueue")

    def test_entry_must_be_a_dict(self):
        with pytest.raises(ValueError, match=r"workers\.myqueue must be an object"):
            validate_custom_workers({"myqueue": 5000})

    def test_typoed_key_fails_naming_queue_and_key(self):
        with pytest.raises(ValueError, match=r"workers\.myqueue\.timout"):
            validate_custom_workers({"myqueue": {"timout": 5000}})

    def test_non_numeric_background_workers_fails(self):
        with pytest.raises(ValueError, match=r"workers\.myqueue\.background_workers"):
            validate_custom_workers({"myqueue": {"background_workers": "two"}})

    def test_zero_background_workers_fails(self):
        with pytest.raises(ValueError, match=r"workers\.myqueue\.background_workers"):
            validate_custom_workers({"myqueue": {"background_workers": 0}})

    @pytest.mark.parametrize("reserved", ["short", "long", "default", "schedule"])
    def test_reserved_queue_names_rejected(self, reserved):
        with pytest.raises(ValueError, match="reserved"):
            validate_custom_workers({reserved: {"timeout": 300}})

    def test_invalid_queue_name_rejected(self):
        with pytest.raises(ValueError, match="invalid custom worker queue name"):
            validate_custom_workers({"my queue!": {"timeout": 300}})


class TestSplitConfigStaleCleanup:
    @pytest.fixture
    def supervisor(self):
        return BenchSupervisor(docker_client=MagicMock(), config=MagicMock(), bench_name="x.localhost")

    def _parsed(self, sections):
        cfg = configparser.ConfigParser(allow_no_value=True, strict=False, interpolation=None)
        for section in sections:
            cfg.add_section(section)
            cfg.set(section, "command", "true")
        return cfg

    def test_stale_worker_conf_removed_on_regen(self, supervisor, tmp_path):
        # A queue that was removed from common_site_config.json left this behind.
        stale = tmp_path / "oldqueue-worker.workers.fm.supervisor.conf"
        stale.write_text("[program:frappe-bench-frappe-oldqueue-worker]\n")

        supervisor._write_split_configs(  # noqa: SLF001
            self._parsed(
                [
                    "program:frappe-bench-frappe-web",
                    "program:frappe-bench-frappe-short-worker",
                    "program:frappe-bench-frappe-myqueue-worker",
                    "group:frappe-bench-workers",
                ]
            ),
            tmp_path,
        )

        assert not stale.exists()
        assert (tmp_path / "myqueue-worker.workers.fm.supervisor.conf").exists()
        assert (tmp_path / "short-worker.workers.fm.supervisor.conf").exists()

    def test_non_worker_confs_never_touched(self, supervisor, tmp_path):
        # Cleanup is scoped to *.workers.fm.supervisor.conf; other supervisor
        # confs (web, schedule, socketio) must survive even when absent from
        # the rendered sections.
        schedule = tmp_path / "schedule.fm.supervisor.conf"
        schedule.write_text("[program:frappe-bench-frappe-schedule]\n")

        supervisor._write_split_configs(self._parsed(["program:frappe-bench-frappe-web"]), tmp_path)  # noqa: SLF001

        assert schedule.exists()
