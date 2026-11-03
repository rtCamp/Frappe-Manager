"""A fact the code already holds must reach a surface before it is thrown away.

Each test names the question the operator could not answer. The rule these follow: a destructive
or unrecoverable act is `print`, because you should not need a flag to learn it happened; detail
about how fm got there is `info`, shown with -v; and a no-op is never on the always-on channel.
"""

from unittest.mock import MagicMock

import pytest

from frappe_manager.output_manager import set_global_output_handler


@pytest.fixture
def said():
    output = MagicMock()
    set_global_output_handler(output)
    return output


def _printed(output: MagicMock) -> str:
    return " ".join(str(c.args[0]) for c in output.print.call_args_list)


def _infoed(output: MagicMock) -> str:
    return " ".join(str(c.args[0]) for c in output.info.call_args_list)


@pytest.mark.unit
class TestTheWorkerThatWillNotComeBack:
    def test_dropping_a_supervisor_conf_says_which_worker_goes(self, said, tmp_path):
        """Deleting the conf is what makes the container disappear on the next sync, and it was
        file-only -- so the worker vanished with no visible cause."""
        from frappe_manager.site_manager.modules.bench_supervisor import BenchSupervisor

        config_dir = tmp_path / "config"
        config_dir.mkdir()
        (config_dir / "gone.workers.fm.supervisor.conf").write_text("[program:gone]\n")

        supervisor = BenchSupervisor.__new__(BenchSupervisor)
        supervisor.output = said
        supervisor.logger = MagicMock()

        import configparser

        supervisor._write_split_configs(configparser.ConfigParser(), config_dir)

        assert "gone.workers.fm.supervisor.conf" in _infoed(said)
        assert "will not be recreated" in _infoed(said)
        assert not (config_dir / "gone.workers.fm.supervisor.conf").exists()


@pytest.mark.unit
class TestANoOpStaysOffTheAlwaysOnChannel:
    def test_an_untouched_newrelic_ini_explains_itself_at_minus_v(self, said, tmp_path):
        """"I enabled New Relic and nothing changed" -- the reason was file-only."""
        from frappe_manager.site_manager.modules.bench_supervisor import BenchSupervisor

        config_dir = tmp_path / "config"
        config_dir.mkdir()
        (config_dir / "newrelic.ini").write_text("[newrelic]\n")

        supervisor = BenchSupervisor.__new__(BenchSupervisor)
        supervisor.output = said
        supervisor.logger = MagicMock()

        supervisor._write_newrelic_config(config_dir, force=False)

        assert "--force-config" in _infoed(said), "the recovery flag must be one the command has"
        assert _printed(said) == "", "a no-op does not belong on the channel you cannot turn off"
