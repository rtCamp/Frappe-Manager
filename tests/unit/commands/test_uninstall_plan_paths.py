"""The teardown plan names real paths, not the default home spelled out."""

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from frappe_manager.commands.self.uninstall import _print_plan
from frappe_manager.utils.uninstall import PathEntry, TeardownPlan


def printed(plan: TeardownPlan, *, keep_backups: bool, keep_images: bool, cli_dir: Path) -> str:
    output = MagicMock()
    with patch("frappe_manager.commands.self.uninstall.CLI_DIR", cli_dir):
        _print_plan(output, plan, keep_backups=keep_backups, keep_images=keep_images)
    return "\n".join(str(call.args[0]) for call in output.print.call_args_list)


@pytest.mark.unit
class TestKeptLineFollowsFmHome:
    def test_kept_backups_names_the_configured_home_not_a_literal(self, tmp_path):
        """FRAPPE_MANAGER_HOME moves fm's home, so the one line claiming something SURVIVES must
        name the directory that actually survives -- a hardcoded ~/frappe points nowhere there."""
        text = printed(TeardownPlan(), keep_backups=True, keep_images=False, cli_dir=tmp_path)

        assert str(tmp_path / "backups") in text
        assert "~/frappe" not in text


@pytest.mark.unit
class TestPlanEnumeratesDockerObjects:
    def test_every_network_and_volume_is_named_before_removal(self, tmp_path):
        """A plan-first command may not remove an object it did not show: volumes hold data with
        no undo, and each name is what an operator checks against `docker volume ls`."""
        plan = TeardownPlan(
            networks=["fm__erp__site-network"],
            volumes=["fm__erp__fm-sockets", "fm-mariadb-data"],
            paths=[PathEntry(tmp_path / "sites" / "erp", 2048)],
        )

        text = printed(plan, keep_backups=False, keep_images=False, cli_dir=tmp_path)

        assert "fm__erp__site-network" in text
        assert "fm__erp__fm-sockets" in text
        assert "fm-mariadb-data" in text
        assert str(tmp_path / "sites" / "erp") in text
