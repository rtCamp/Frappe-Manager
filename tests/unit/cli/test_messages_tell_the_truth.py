"""Messages that assert an action must only fire when the action happened.

Every test here names a sentence fm used to print untruthfully. The shared mistake was reporting
the PLAN rather than the result: `len(plan.X)` while a failures list filled beside it, a size
measured before deletion, a success line outside the branch that does the work.
"""

import contextlib
from unittest.mock import MagicMock, patch

import pytest
import typer

from frappe_manager.commands.self.uninstall import uninstall
from frappe_manager.output_manager import set_global_output_handler
from frappe_manager.site_manager.modules.db_probe import CheckStatus
from frappe_manager.utils.uninstall import PathEntry, TeardownPlan


def _dead_docker():
    """A daemon that is not answering: server_running() False is how uninstall detects it."""
    dead = MagicMock()
    dead.server_running.return_value = False
    return dead


def _run_uninstall(plan: TeardownPlan, docker, tmp_path) -> tuple[list[str], list[str]]:
    """Drive the real command and return (printed lines, warned lines)."""
    output = MagicMock()
    output.prompt_ask.return_value = "uninstall"
    # The handler INSTANCE is swapped, never `get_global_output_handler` itself: the autouse
    # fixture installs a real one and the project's testing rules forbid patching the getter.
    set_global_output_handler(output)
    with (
        patch("frappe_manager.commands.self.uninstall.plan_teardown", return_value=plan),
        patch("frappe_manager.commands.self.uninstall.CLI_DIR", tmp_path / "nonexistent"),
        patch("frappe_manager.commands.self.uninstall.DockerClient", return_value=docker),
        patch("frappe_manager.commands.self.uninstall.TrustStoreManager") as trust,
    ):
        trust.return_value.uninstall.return_value = ([], [])
        # Exit 1 is expected whenever a removal failed; the summary lines are what is under test.
        with contextlib.suppress(typer.Exit):
            uninstall(only=None, keep_backups=False, keep_images=False, yes=True, dry_run=False)
    printed = [str(c.args[0]) for c in output.print.call_args_list]
    warned = [str(c.args[0]) for c in output.warning.call_args_list]
    return printed, warned


@pytest.mark.unit
class TestTheUninstallSummaryCountsWhatWent:
    def test_a_refused_container_is_not_counted_as_removed(self, tmp_path):
        """"Removed 2 container(s)" fired while one of them sat in the failure list below it."""
        docker = MagicMock()
        docker.rm.side_effect = [None, RuntimeError("still in use")]
        plan = TeardownPlan(containers=["fm_ok", "fm_bad"])

        printed, _warned = _run_uninstall(plan, docker, tmp_path)

        assert "Removed 1 container(s)" in printed
        assert "Removed 2 container(s)" not in printed

    def test_a_refused_volume_and_network_are_not_counted(self, tmp_path):
        docker = MagicMock()
        docker.volume_rm.side_effect = [True, False]
        docker.network_rm.side_effect = [False]
        plan = TeardownPlan(volumes=["v_ok", "v_bad"], networks=["n_bad"])

        printed, _warned = _run_uninstall(plan, docker, tmp_path)

        assert "Removed 1 volume(s)" in printed
        assert "Removed 0 network(s)" in printed

    def test_space_that_was_not_freed_is_not_reported_as_freed(self, tmp_path):
        """The root-owned mariadb directory is the documented failure, and its bytes were counted
        as reclaimed because the total came from the plan."""
        kept = tmp_path / "kept"
        kept.mkdir()
        plan = TeardownPlan(paths=[PathEntry(path=kept, size=5_000_000)])

        with patch("frappe_manager.commands.self.uninstall._remove_path") as remove:
            remove.side_effect = lambda _p, failures: failures.append("files: permission denied")
            printed, _warned = _run_uninstall(plan, MagicMock(), tmp_path)

        assert not [line for line in printed if "of files" in line and "0" not in line]

    def test_a_docker_less_host_is_not_told_its_state_is_gone(self, tmp_path):
        """With no daemon every docker block is SKIPPED rather than failing, so the failures list
        is empty and the clean-host line used to print over objects that are all still there."""
        gone = tmp_path / "files"
        gone.mkdir()
        plan = TeardownPlan(paths=[PathEntry(path=gone, size=10)])

        output = MagicMock()
        output.prompt_ask.return_value = "uninstall"
        set_global_output_handler(output)
        with (
                patch("frappe_manager.commands.self.uninstall.plan_teardown", return_value=plan),
            patch("frappe_manager.commands.self.uninstall.CLI_DIR", tmp_path / "nonexistent"),
            patch("frappe_manager.commands.self.uninstall.DockerClient", return_value=_dead_docker()),
            patch("frappe_manager.commands.self.uninstall.TrustStoreManager") as trust,
        ):
            trust.return_value.uninstall.return_value = ([], [])
            with contextlib.suppress(typer.Exit):
                uninstall(only=None, keep_backups=False, keep_images=False, yes=True, dry_run=False)

        printed = [str(c.args[0]) for c in output.print.call_args_list]
        warned = " ".join(str(c.args[0]) for c in output.warning.call_args_list)
        assert "fm's state is gone from this host." not in printed
        assert "docker was unreachable" in warned


@pytest.mark.unit
class TestAProbeThatFailedIsNotAnAnswer:
    def test_an_unknown_database_is_not_reported_as_absent(self):
        """fm CREATES the database on "does not exist", so a failed existence query must never
        read as a negative answer."""
        from frappe_manager.site_manager.modules.db_probe_postgres import _schema_state

        _state, check = _schema_state(
            database_exists=False, exists_known=False, table_count=0, frappe_tables=(), installed_apps=()
        )

        assert check.status is CheckStatus.fail
        assert "could not determine" in check.detail

    def test_a_genuine_absence_still_reads_as_absent(self):
        from frappe_manager.site_manager.modules.db_probe_postgres import _schema_state

        _state, check = _schema_state(
            database_exists=False, exists_known=True, table_count=0, frappe_tables=(), installed_apps=()
        )

        assert check.status is CheckStatus.ok
        assert "does not exist yet" in check.detail

    def test_an_unaskable_tls_state_is_not_reported_as_plaintext(self, monkeypatch):
        """"Could not ask whether this connection is encrypted" and "it is not encrypted" were
        the same value, because the query's ok flag was dropped."""
        from types import SimpleNamespace

        from frappe_manager.site_manager.modules import db_probe_postgres as pg

        def runner(_cmd, **_kw):
            return True, ""

        # The banner query succeeds so the probe gets past the connect gate; everything after it
        # fails, which is what a dropped ok flag used to render as a confident "not encrypted".
        replies = iter([SimpleNamespace(ok=True, text="")])
        monkeypatch.setattr(
            pg,
            "run_query",
            lambda *_a, **_k: next(replies, SimpleNamespace(ok=False, text="server closed the connection")),
        )
        result = pg.probe_stage_one(
            runner, host="db", port=5432, user="fm", password=None, dbname="site_db", site_login=None
        )

        assert result.tls_in_force is False
        tls = [c for c in result.checks if c.name == pg.CHECK_TLS_IN_FORCE]
        assert tls, "an unaskable TLS state must be reported, not silently read as plaintext"
        assert "could not determine" in tls[0].detail


@pytest.mark.unit
class TestAReusedDirectoryIsNotAClone:
    def test_an_existing_app_directory_reports_itself_as_not_cloned(self, tmp_path):
        """A directory that already existed was returned as a successful clone, so the caller's
        count could say five for a run that cloned none -- and a stale wrong-branch checkout was
        announced as fresh."""
        from frappe_manager.site_manager.bench_config import AppConfig
        from frappe_manager.site_manager.modules.app_cloner import AppCloner

        apps = tmp_path / "apps"
        apps.mkdir()
        (apps / "erpnext").mkdir()

        cloner = AppCloner(apps_dir=apps)
        name, path, freshly_cloned = cloner._clone_app(
            AppConfig(name="erpnext", repo="https://github.com/frappe/erpnext")
        )

        assert (name, path) == ("erpnext", apps / "erpnext")
        assert freshly_cloned is False
        assert cloner.skipped_apps == []  # the caller records the skip, from the flag
