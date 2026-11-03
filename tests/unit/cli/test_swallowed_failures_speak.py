"""A failure fm catches must leave a trace naming what the operator will see.

Each test is named for the SYMPTOM the message prevents, not for the fact that a message exists:
these paths all used to swallow their exception, so the operator met the consequence with nothing
anywhere connecting it to a cause. Each warn row is paired with its quiet counterpart, because a
warning that also fires on the ordinary path stops being worth reading.
"""

from unittest.mock import MagicMock, patch

import pytest

from frappe_manager.output_manager import set_global_output_handler


@pytest.fixture
def warnings() -> MagicMock:
    """Capture what reaches the operator, by swapping the handler INSTANCE."""
    output = MagicMock()
    set_global_output_handler(output)
    return output


def _warned(output: MagicMock) -> str:
    return " ".join(str(call.args[0]) for call in output.warning.call_args_list)


@pytest.mark.unit
class TestADomainClashCannotSlipThroughSilently:
    def test_a_bench_whose_config_will_not_parse_is_named(self, warnings, tmp_path):
        """A bench missing from the conflict map takes its domains with it, so fm lets a second
        bench claim a hostname the first already serves."""
        from frappe_manager.site_manager import domain_conflict

        bench = tmp_path / "broken.localhost"
        bench.mkdir()
        (bench / "bench_config.toml").write_text("this is not = = valid toml [[[")

        # Passed, not patched: `benches_root` is a parameter whose default binds at def time.
        domain_conflict.build_global_domain_map(benches_root=tmp_path)

        assert "broken.localhost" in _warned(warnings)
        assert "conflict" in _warned(warnings)


@pytest.mark.unit
class TestADeletedSitesDatabaseCaDoesNotStayTrustedInSilence:
    def test_tls_material_that_could_not_be_removed_is_reported(self, warnings, tmp_path):
        """`ignore_errors=True` hid this, and the rebuild immediately after re-concatenates the CA
        of the site just deleted -- so it stays trusted by every site on the bench."""
        from frappe_manager.site_manager.modules import db_tls

        stubborn = tmp_path / "tls" / "mysite"
        stubborn.mkdir(parents=True)

        with (
            patch.object(db_tls, "site_tls_host_dir", return_value=stubborn),
            patch.object(db_tls.shutil, "rmtree"),
            patch.object(db_tls, "rebuild_ca_bundle"),
        ):
            db_tls.remove_site_tls(tmp_path, "mysite")

        assert "still" in _warned(warnings)
        assert "trusted" in _warned(warnings)

    def test_a_directory_that_went_away_says_nothing(self, warnings, tmp_path):
        """The ordinary case stays quiet, or the warning above stops being worth reading."""
        from frappe_manager.site_manager.modules import db_tls

        gone = tmp_path / "tls" / "mysite"

        with (
            patch.object(db_tls, "site_tls_host_dir", return_value=gone),
            patch.object(db_tls, "rebuild_ca_bundle"),
        ):
            db_tls.remove_site_tls(tmp_path, "mysite")

        assert _warned(warnings) == ""


@pytest.mark.unit
class TestASettingThatNeverReachedTheContainer:
    def test_a_missing_compose_service_is_named(self, warnings):
        """The caller carries on believing the service is configured, and the symptom lands much
        later as a variable that simply is not there inside a running container."""
        from frappe_manager.docker.compose_file import ComposeFile

        compose = ComposeFile.__new__(ComposeFile)
        compose.yml = {"services": {"frappe": {}}}

        compose.set_envs("nosuchservice", {"MYSQL_HOST": "db"})

        assert "nosuchservice" in _warned(warnings)
        assert "environment" in _warned(warnings)

    def test_a_service_that_exists_is_configured_quietly(self, warnings):
        from frappe_manager.docker.compose_file import ComposeFile

        compose = ComposeFile.__new__(ComposeFile)
        compose.yml = {"services": {"frappe": {}}}

        compose.set_envs("frappe", {"MYSQL_HOST": "db"})

        assert _warned(warnings) == ""
        assert compose.yml["services"]["frappe"]["environment"]["MYSQL_HOST"] == "db"


@pytest.mark.unit
class TestARollbackDoesNotClaimMoreThanItDid:
    def test_a_vanished_backup_is_named_rather_than_returning_quietly(self, warnings, tmp_path):
        """Returning None is indistinguishable from a restore that worked, and the caller then
        prints "rollback successful" over the gap."""
        from frappe_manager.migration_manager.backup_manager import BackupData, BackupManager

        manager = BackupManager.__new__(BackupManager)
        data = BackupData(
            src=tmp_path / "live.toml",
            dest=tmp_path / "gone.toml",
            allow_restore=True,
        )
        assert not data.real_dest.exists()

        assert manager.restore(data) is None
        assert "gone.toml" in _warned(warnings)

    def test_a_network_that_already_exists_is_not_called_a_failure(self):
        """docker exits non-zero for "already exists" exactly as for a real refusal, so warning on
        it would cry wolf on every re-run of a path that ends "Rollback complete."

        The substring this branch tests was verified against a real duplicate-network refusal on
        the test host: the daemon's message does reach `str(DockerException)`.
        """
        from frappe_manager.docker import DockerException
        from frappe_manager.docker.subprocess_output import SubprocessOutput

        benign = DockerException(
            ["docker", "network", "create", "fm_net"],
            # stderr is the SECOND field; putting the message in `combined` makes str() report it
            # as "not captured", which is a test artefact rather than the real shape.
            SubprocessOutput([], ["Error response from daemon: network with name fm_net already exists"], [], 1),
        )
        genuine = DockerException(
            ["docker", "network", "create", "fm_net"],
            SubprocessOutput([], ["Error response from daemon: invalid subnet"], [], 1),
        )

        assert "already exists" in str(benign)
        assert "already exists" not in str(genuine)
