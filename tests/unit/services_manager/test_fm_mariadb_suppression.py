"""fm's shared mariadb runs only when a site on this host actually uses it.

A bench points at an external database by carrying `[sites."<site>".database]`; absence means
that site lives on fm's own `mariadb` container, and there is no second switch (bench_config.py
:1387). So the host-wide question is "does any bench record a site with no `[database]` table",
and on a host where every site is external the answer is No and the server is switched off with
the `disabled` compose profile -- the same mechanism that already suppresses a bench's own redis
containers when it points at a redis fm does not own.

What these tests defend is the DIRECTION of every uncertain answer. Running a database nobody
queries wastes memory; stopping one a site depends on takes that site down. So every case the
scan cannot read confidently must answer "needed", and the answer must come from what is
RECORDED on disk, never from what happens to be running -- a stopped bench still owns its schema.
"""

from pathlib import Path
from unittest import mock

import pytest

from frappe_manager.services_manager.services import ServicesManager

# A config that validates: `BenchConfig` is strict, and an incomplete one would answer "needed"
# through the unreadable branch rather than the branch each test means to exercise.
_BASE = 'name = "shop"\ndeveloper_mode = false\nadmin_tools = false\nenvironment = "prod"\n'

FM_DB_SITE = _BASE + '[sites."shop.localhost"]\n'

EXTERNAL_DB_SITE = _BASE + (
    '[sites."shop.localhost".database]\nhost = "db.example.com"\nname = "shop_schema"\n'
)


@pytest.fixture
def benches_dir(tmp_path, monkeypatch):
    """A benches directory the predicate will scan, with nothing else on the host."""
    benches = tmp_path / "sites"
    benches.mkdir()
    monkeypatch.setattr("frappe_manager.CLI_BENCHES_DIRECTORY", benches)
    return benches


def _bench(benches_dir: Path, name: str, config_text: str | None) -> Path:
    """A bench directory as the scan recognises one: a docker-compose.yml plus its config."""
    path = benches_dir / name
    path.mkdir()
    (path / "docker-compose.yml").write_text("services: {}\n")
    if config_text is not None:
        (path / "bench_config.toml").write_text(config_text)
    return path


def _manager(tmp_path) -> ServicesManager:
    return ServicesManager(path=tmp_path / "services", output_handler=mock.MagicMock())


class TestTheHostWidePredicate:
    def test_a_host_with_no_benches_does_not_need_it(self, benches_dir, tmp_path):
        assert _manager(tmp_path).fm_mariadb_is_needed() is False

    def test_one_site_on_fm_s_database_is_enough(self, benches_dir, tmp_path):
        _bench(benches_dir, "shop", FM_DB_SITE)

        assert _manager(tmp_path).fm_mariadb_is_needed() is True

    def test_every_site_external_means_it_is_not_needed(self, benches_dir, tmp_path):
        _bench(benches_dir, "shop", EXTERNAL_DB_SITE)
        _bench(benches_dir, "blog", EXTERNAL_DB_SITE)

        assert _manager(tmp_path).fm_mariadb_is_needed() is False

    def test_one_fm_site_among_external_ones_keeps_it(self, benches_dir, tmp_path):
        """The answer is per SITE, not per bench: a single holdout keeps the server on."""
        _bench(benches_dir, "blog", EXTERNAL_DB_SITE)
        _bench(benches_dir, "shop", FM_DB_SITE)

        assert _manager(tmp_path).fm_mariadb_is_needed() is True

    def test_an_unreadable_config_answers_needed(self, benches_dir, tmp_path):
        """It may hold a site on fm's database. Stopping a server under a live site is far worse
        than running one nobody uses, so every uncertainty resolves the safe way."""
        _bench(benches_dir, "broken", "this is not toml {{{")

        assert _manager(tmp_path).fm_mariadb_is_needed() is True

    def test_a_missing_config_answers_needed(self, benches_dir, tmp_path):
        _bench(benches_dir, "shop", None)

        assert _manager(tmp_path).fm_mariadb_is_needed() is True

    def test_a_bench_recording_no_sites_answers_needed(self, benches_dir, tmp_path):
        """What an unmigrated (pre-`[sites]`) config looks like from here: fm cannot tell what it
        is on, so it assumes the answer that keeps that bench working."""
        _bench(benches_dir, "old", 'name = "old"\n')

        assert _manager(tmp_path).fm_mariadb_is_needed() is True

    def test_a_directory_that_is_not_a_bench_is_ignored(self, benches_dir, tmp_path):
        """No docker-compose.yml, so not a bench: an archive folder or a stray must not pin the
        database on by looking unreadable."""
        (benches_dir / "notabench").mkdir()

        assert _manager(tmp_path).fm_mariadb_is_needed() is False

    def test_the_answer_ignores_whether_anything_is_running(self, benches_dir, tmp_path):
        """A stopped bench still owns its schema. Deciding on running containers would switch the
        server off while a bench was down and leave it unable to start again."""
        _bench(benches_dir, "shop", FM_DB_SITE)

        manager = _manager(tmp_path)
        with mock.patch.object(ServicesManager, "is_service_running", return_value=False):
            assert manager.fm_mariadb_is_needed() is True


class TestReconciling:
    """`docker compose` cannot address a service whose profile is inactive, and never stops one
    that is already running -- both learned from the redis side (update.py:463). So the profile
    flip is not enough on its own: the container has to be started or removed by name here.
    """

    def _manager_with_compose(self, tmp_path, *, currently_disabled: bool):
        manager = _manager(tmp_path)
        manager.compose_file_manager = mock.MagicMock()
        manager.compose_file_manager.is_service_profile_disabled.return_value = currently_disabled
        manager.docker_client = mock.MagicMock()
        manager.database_manager = mock.MagicMock()
        return manager

    def test_switching_it_off_removes_the_container_too(self, tmp_path):
        manager = self._manager_with_compose(tmp_path, currently_disabled=False)

        manager.reconcile_fm_mariadb(needed=False)

        manager.compose_file_manager.set_service_disabled.assert_called_once_with("mariadb", disabled=True)
        manager.docker_client.compose.rm.assert_called_once_with(services=["mariadb"], stop=True, force=True)

    def test_switching_it_on_starts_it_and_waits_for_it(self, tmp_path):
        """The profile must be cleared BEFORE the up, or compose ignores the service; and the
        caller that asked for it is about to connect, so a server still booting is waited for."""
        manager = self._manager_with_compose(tmp_path, currently_disabled=True)

        manager.reconcile_fm_mariadb(needed=True)

        manager.compose_file_manager.set_service_disabled.assert_called_once_with("mariadb", disabled=False)
        manager.docker_client.compose.up.assert_called_once()
        manager.database_manager.wait_till_db_start.assert_called_once()

    def test_an_already_correct_state_touches_nothing(self, tmp_path):
        """Called from the readiness path of every create, so the ordinary host must not rewrite
        its compose file or bounce its database on every command."""
        manager = self._manager_with_compose(tmp_path, currently_disabled=False)

        manager.reconcile_fm_mariadb(needed=True)

        manager.compose_file_manager.set_service_disabled.assert_not_called()
        manager.compose_file_manager.write_to_file.assert_not_called()
        manager.docker_client.compose.up.assert_not_called()
        manager.docker_client.compose.rm.assert_not_called()

    def test_a_caller_can_override_the_scan(self, tmp_path, benches_dir):
        """`fm create` enables the server before `bench new-site` reaches for it, at which point
        the site that needs it is not on disk yet and the scan would answer No."""
        manager = self._manager_with_compose(tmp_path, currently_disabled=True)

        manager.reconcile_fm_mariadb(needed=True)

        manager.compose_file_manager.set_service_disabled.assert_called_once_with("mariadb", disabled=False)
