"""fm runs a database server only when a site on this host lives on it.

A site records which engine it is on and whose server (`[sites."<site>".database]`), so the
host-wide answer is a SET of fm's own engines, read off disk. Each of fm's database services is
switched off with the `disabled` compose profile while its engine is absent from that set -- the
same mechanism that already suppresses a bench's own redis containers when it points at a redis
fm does not own.

What these tests defend is the DIRECTION of every uncertain answer. Running a database nobody
queries wastes memory; stopping one a site depends on takes that site down. So every case the
scan cannot read confidently answers "mariadb", the engine every bench predating this had, and
the answer comes from what is RECORDED, never from what happens to be running -- a stopped bench
still owns its schema.
"""

from pathlib import Path
from unittest import mock

import pytest

from frappe_manager.services_manager.services import ServicesManager

# A config that validates: `BenchConfig` is strict, and an incomplete one would answer through the
# unreadable branch rather than the branch each test means to exercise.
_BASE = 'name = "shop"\ndeveloper_mode = false\nadmin_tools = false\nenvironment = "prod"\n'

FM_MARIADB = _BASE + '[sites."shop.localhost"]\n'
FM_POSTGRES = _BASE + '[sites."shop.localhost".database]\ntype = "postgres"\n'
EXTERNAL_MARIADB = _BASE + '[sites."shop.localhost".database]\nhost = "db.example.com"\nname = "s"\n'
EXTERNAL_POSTGRES = (
    _BASE + '[sites."shop.localhost".database]\ntype = "postgres"\nhost = "pg.example.com"\nname = "s"\n'
)


@pytest.fixture
def benches_dir(tmp_path, monkeypatch):
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


class TestWhichEnginesAreInUse:
    def test_a_host_with_no_benches_uses_none(self, benches_dir, tmp_path):
        assert _manager(tmp_path).engines_in_use() == set()

    def test_a_site_on_fms_mariadb_counts(self, benches_dir, tmp_path):
        _bench(benches_dir, "shop", FM_MARIADB)

        assert _manager(tmp_path).engines_in_use() == {"mariadb"}

    def test_a_site_on_fms_postgres_counts(self, benches_dir, tmp_path):
        """The reason this is a set rather than a boolean: a postgres-only host must run postgres
        and NOT mariadb, which one flag cannot say."""
        _bench(benches_dir, "shop", FM_POSTGRES)

        assert _manager(tmp_path).engines_in_use() == {"postgres"}

    def test_both_engines_can_be_in_use_at_once(self, benches_dir, tmp_path):
        _bench(benches_dir, "shop", FM_MARIADB)
        _bench(benches_dir, "blog", FM_POSTGRES)

        assert _manager(tmp_path).engines_in_use() == {"mariadb", "postgres"}

    @pytest.mark.parametrize("config", [EXTERNAL_MARIADB, EXTERNAL_POSTGRES])
    def test_a_site_on_someone_elses_server_counts_for_nothing(self, benches_dir, tmp_path, config):
        """Whose server, not which engine: an external postgres site is not a reason to run one."""
        _bench(benches_dir, "shop", config)

        assert _manager(tmp_path).engines_in_use() == set()

    def test_an_unreadable_config_answers_mariadb(self, benches_dir, tmp_path):
        """It may hold a site on fm's mariadb, and stopping a server under a live site is far worse
        than running one nobody uses, so every uncertainty resolves the safe way."""
        _bench(benches_dir, "broken", "this is not toml {{{")

        assert _manager(tmp_path).engines_in_use() == {"mariadb"}

    def test_a_missing_config_answers_mariadb(self, benches_dir, tmp_path):
        _bench(benches_dir, "shop", None)

        assert _manager(tmp_path).engines_in_use() == {"mariadb"}

    def test_a_bench_recording_no_sites_answers_mariadb(self, benches_dir, tmp_path):
        """What an unmigrated (pre-`[sites]`) config looks like from here, and mariadb is the only
        engine such a bench could have been on."""
        _bench(benches_dir, "old", 'name = "old"\n')

        assert _manager(tmp_path).engines_in_use() == {"mariadb"}

    def test_one_unreadable_bench_does_not_hide_the_others(self, benches_dir, tmp_path):
        """A broken neighbour must not switch off the engine a working bench is on."""
        _bench(benches_dir, "broken", "not toml {{{")
        _bench(benches_dir, "shop", FM_POSTGRES)

        assert _manager(tmp_path).engines_in_use() == {"mariadb", "postgres"}

    def test_a_directory_that_is_not_a_bench_is_ignored(self, benches_dir, tmp_path):
        """No docker-compose.yml, so not a bench: an archive folder must not pin an engine on by
        looking unreadable."""
        (benches_dir / "notabench").mkdir()

        assert _manager(tmp_path).engines_in_use() == set()

    def test_the_answer_ignores_whether_anything_is_running(self, benches_dir, tmp_path):
        """A stopped bench still owns its schema. Deciding on running containers would switch the
        server off while a bench was down and leave it unable to start again."""
        _bench(benches_dir, "shop", FM_MARIADB)

        manager = _manager(tmp_path)
        with mock.patch.object(ServicesManager, "is_service_running", return_value=False):
            assert manager.engines_in_use() == {"mariadb"}


class TestReconciling:
    """`docker compose` cannot address a service whose profile is inactive, and never stops one
    already running -- both learned from the redis side (update.py:463). So the profile flip is
    not enough on its own: the container has to be started or removed by name here.
    """

    def _manager(self, tmp_path, *, disabled: set[str]):
        manager = _manager(tmp_path)
        manager.compose_file_manager = mock.MagicMock()
        manager.compose_file_manager.is_service_profile_disabled.side_effect = lambda s: s in disabled
        manager.docker_client = mock.MagicMock()
        manager.database_manager = mock.MagicMock()
        return manager

    def test_an_unused_engine_is_switched_off_and_its_container_removed(self, tmp_path):
        manager = self._manager(tmp_path, disabled={"postgres"})

        manager.reconcile_database_services(set())

        manager.compose_file_manager.set_service_disabled.assert_called_once_with("mariadb", disabled=True)
        manager.docker_client.compose.rm.assert_called_once_with(services=["mariadb"], stop=True, force=True)

    def test_a_newly_used_engine_is_switched_on_and_started(self, tmp_path):
        """The profile must be cleared BEFORE the up, or compose ignores the service entirely."""
        manager = self._manager(tmp_path, disabled={"postgres"})

        manager.reconcile_database_services({"mariadb", "postgres"})

        manager.compose_file_manager.set_service_disabled.assert_called_once_with("postgres", disabled=False)
        assert manager.docker_client.compose.up.call_args.kwargs["services"] == ["postgres"]

    def test_switching_one_engine_on_does_not_switch_the_other_off(self, tmp_path):
        """Both may be in use at once, and a bench coming up on postgres must not take the mariadb
        its neighbour is serving from down with it."""
        manager = self._manager(tmp_path, disabled={"postgres"})

        manager.reconcile_database_services({"mariadb", "postgres"})

        manager.docker_client.compose.rm.assert_not_called()

    def test_an_already_correct_state_touches_nothing(self, tmp_path):
        """Called from the readiness path of every create, so the ordinary host must not rewrite
        its compose file or bounce its database on every command."""
        manager = self._manager(tmp_path, disabled={"postgres"})

        manager.reconcile_database_services({"mariadb"})

        manager.compose_file_manager.set_service_disabled.assert_not_called()
        manager.compose_file_manager.write_to_file.assert_not_called()
        manager.docker_client.compose.up.assert_not_called()
        manager.docker_client.compose.rm.assert_not_called()

    def test_a_caller_can_override_the_scan(self, tmp_path, benches_dir):
        """`fm create` enables the server before `bench new-site` reaches for it, at which point
        the site that needs it is not on disk yet and the scan would answer with the old set."""
        manager = self._manager(tmp_path, disabled={"postgres"})

        manager.reconcile_database_services({"postgres"})

        manager.compose_file_manager.set_service_disabled.assert_any_call("postgres", disabled=False)


class TestThePostgresMajorGuard:
    """Postgres has no equivalent of MariaDB's automatic datadir upgrade. Started against a
    datadir from another major it exits with "database files are incompatible with server", and
    `restart: always` turns that into a container that keeps coming back and never serves, with
    the real sentence buried in `docker logs`."""

    def _manager(self, tmp_path, *, on_disk: str | None, image: str):
        manager = _manager(tmp_path)
        manager.compose_file_manager = mock.MagicMock()
        manager.compose_file_manager.yml = {"services": {"postgres": {"image": image}}}
        manager.output = mock.MagicMock()
        manager.output.exit.side_effect = SystemExit(1)
        if on_disk is not None:
            marker = tmp_path / "services" / "postgres" / "data" / "pgdata" / "PG_VERSION"
            marker.parent.mkdir(parents=True)
            marker.write_text(f"{on_disk}\n")
        return manager

    def test_a_matching_major_starts(self, tmp_path):
        manager = self._manager(tmp_path, on_disk="17", image="postgres:17")

        manager.check_postgres_datadir_major()

        manager.output.exit.assert_not_called()

    def test_a_datadir_from_another_major_is_refused(self, tmp_path):
        manager = self._manager(tmp_path, on_disk="16", image="postgres:17")

        with pytest.raises(SystemExit):
            manager.check_postgres_datadir_major()

        assert "16" in manager.output.exit.call_args.args[0]

    def test_the_refusal_says_what_to_do(self, tmp_path):
        """There is no safe automatic move: going up a major is a dump by the OLD server and a
        restore into a new datadir, and fm must not do that silently on a start."""
        manager = self._manager(tmp_path, on_disk="16", image="postgres:17")

        with pytest.raises(SystemExit):
            manager.check_postgres_datadir_major()

        message = manager.output.exit.call_args.args[0]
        assert "Dump" in message

    def test_a_host_with_no_datadir_yet_is_not_refused(self, tmp_path):
        """First start on a fresh install: the datadir is what postgres is about to create."""
        manager = self._manager(tmp_path, on_disk=None, image="postgres:17")

        manager.check_postgres_datadir_major()

        manager.output.exit.assert_not_called()

    def test_a_minor_difference_is_not_a_mismatch(self, tmp_path):
        """`PG_VERSION` records the major alone, and an image tag may carry a minor. Comparing the
        whole strings would refuse every host on `postgres:17.2`."""
        manager = self._manager(tmp_path, on_disk="17", image="postgres:17.2")

        manager.check_postgres_datadir_major()

        manager.output.exit.assert_not_called()


class TestTheShippedServicesTemplate:
    """The rendered compose is the state of a host BEFORE anything reconciles it.

    A first install is performed by `fm create`, an observer, or a `services`/`self` command --
    every one of which is exempt from the reconcile pass, so whatever the template ships with is
    what the host runs until some later command happens not to be exempt.
    """

    @pytest.mark.parametrize("template", ["docker-compose.services.tmpl", "docker-compose.services.osx.tmpl"])
    def test_a_fresh_install_starts_no_database_server(self, tmp_path, template):
        """No bench exists yet, so nothing can be on either engine; `compose up` must bring up the
        proxy alone. mariadb shipped without the profile is what left one running on every new host."""
        from frappe_manager.docker import ComposeFile

        compose_path = tmp_path / "docker-compose.yml"
        ComposeFile(compose_path, template_name=template).write_to_file()
        rendered = ComposeFile(compose_path, template_name=template)

        assert rendered.is_service_profile_disabled("mariadb") is True
        assert rendered.is_service_profile_disabled("postgres") is True
        assert sorted(rendered.get_services_list(exclude_disabled=True)) == ["nginx-proxy"]
