"""``fm create --db-ca``, ``fm update --db-ca``, ``fm ssl add --cert/--key/--ca`` and
``fm maintenance --page`` all declare a bare ``Path`` typer Option. Typer's default for that
annotation is ``click.Path(exists=False, readable=True, ...)``, and click's own converter stats
the file and fails with ITS OWN wording ("Path '<p>' is not readable.") before the command body --
and any hand-written check in it -- ever runs. Every one of these commands has its own curated
validation for exactly this case; declaring ``readable=False`` on the Option is what lets that
hand-written check actually fire. The ``*ReadableGate`` classes below pin exactly that: an
existing, unreadable file must reach fm's own check, not click's.

The ``*ExistenceGuard`` classes alongside them pin a DIFFERENT thing that happens to share the
same fixtures: ``create``'s ``_validated_ca`` and ``update``'s ``db_tls._validated_ca_source``
also refuse a missing path or a directory, entirely independent of ``readable=False`` (click's
own ``exists=False`` default lets both through unblocked regardless of that flag). No other test
in the suite drives those two hand checks through the CLI, so they are kept here rather than
deleted, filed separately from the readable-gate cases they do not exercise.

Exercised through the real Typer app via CliRunner: this is a CLI-argument-parsing-layer defect,
invisible to a test that calls the command function directly with an already-typed ``Path`` value
(click never touches such a call).

Messages are pinned by SOURCE, not full prose: a click-authored refusal always reads
"Invalid value for '--flag': ..." (a bound parameter) or, for a click.Path failure specifically,
ends in "is not readable." (capitalized, trailing period). fm's own refusals never look like that.
"""

import os
from contextlib import contextmanager
from unittest.mock import MagicMock, patch

import pytest
import typer
from typer.testing import CliRunner

from frappe_manager.commands.create import create
from frappe_manager.commands.maintenance import maintenance
from frappe_manager.commands.ssl.add import add_certificate
from frappe_manager.commands.update import update
from frappe_manager.site_manager.bench_config import BenchRuntime

runner = CliRunner()

requires_non_root = pytest.mark.skipif(
    hasattr(os, "geteuid") and os.geteuid() == 0,
    reason="root ignores the permission bit chmod(0o000) relies on",
)


def _is_click_path_failure(output: str) -> bool:
    """click.Path's own wording: 'Invalid value for '--flag': Path '<p>' is not readable.'"""
    return "Invalid value for" in output and "is not readable." in output


@contextmanager
def _null_spinner(*_args, **_kwargs):
    yield


# --------------------------------------------------------------------- fm create --db-ca


@pytest.fixture
def create_cli():
    app = typer.Typer()
    app.command("create")(create)
    return app


def _invoke_create(cli, tmp_path, db_ca_arg):
    with (
        patch("frappe_manager.utils.callbacks.CLI_BENCHES_DIRECTORY", tmp_path),
        patch("frappe_manager.commands.create.BenchService") as bench_service_cls,
    ):
        result = runner.invoke(
            cli,
            [
                "freshbench",
                "--db-host",
                "h",
                "--db-name",
                "n",
                "--db-password",
                "pw",
                "--db-ca",
                str(db_ca_arg),
            ],
            obj={"services": MagicMock(), "verbose": False, "fm_config_manager": MagicMock()},
        )
    return result, bench_service_cls


class TestCreateDbCaExistenceGuard:
    """``_validated_ca``'s existence check, not the readable=False fix: click's own
    ``exists=False`` default lets a missing path or a directory through unblocked regardless of
    ``readable``, so these two do not discriminate that flag. Kept anyway because no other test
    drives ``_validated_ca`` through the CLI at all."""

    def test_missing_file_is_refused_by_fm_not_click(self, create_cli, tmp_path):
        result, bench_service_cls = _invoke_create(create_cli, tmp_path, tmp_path / "nope.pem")

        assert result.exit_code != 0
        assert not _is_click_path_failure(result.output)
        assert "no such file" in result.output
        assert bench_service_cls.return_value.create_bench.called is False

    def test_a_directory_is_refused_the_same_way_as_a_missing_file(self, create_cli, tmp_path):
        """``is_file()``, not ``exists()``: stricter than click's own ``dir_okay=True`` default,
        so a directory is caught too -- with the same "no such file" wording a missing path gets."""
        a_directory = tmp_path / "a-directory"
        a_directory.mkdir()

        result, bench_service_cls = _invoke_create(create_cli, tmp_path, a_directory)

        assert result.exit_code != 0
        assert not _is_click_path_failure(result.output)
        assert "no such file" in result.output
        assert bench_service_cls.return_value.create_bench.called is False


class TestCreateDbCaReadableGate:
    @requires_non_root
    def test_an_unreadable_existing_file_is_refused_by_fm_not_click(self, create_cli, tmp_path):
        """The actual defect: an existing, unreadable file used to fail at argument parsing with
        click's own message, before fm's os.access check ever ran."""
        ca = tmp_path / "ca.pem"
        ca.write_text("ca")
        ca.chmod(0o000)
        try:
            result, bench_service_cls = _invoke_create(create_cli, tmp_path, ca)
        finally:
            ca.chmod(0o644)

        assert result.exit_code != 0
        assert not _is_click_path_failure(result.output)
        assert "file is not readable" in result.output
        assert bench_service_cls.return_value.create_bench.called is False


# --------------------------------------------------------------------- fm update --db-ca


@pytest.fixture
def update_world(tmp_path):
    """Drives the real Typer-wrapped ``update`` far enough to reach
    ``db_tls.install_site_ca`` -- the actual hand-validation for ``--db-ca`` -- with every
    collaborator up to that point replaced at its seam. ``db_tls.install_site_ca`` itself is
    deliberately left real: that is what is under test.
    """
    app = typer.Typer()
    app.command()(update)

    bench_name = "mybench.localhost"
    bench_path = tmp_path / bench_name
    bench_path.mkdir(parents=True)

    bench = MagicMock(name="bench")
    bench.name = bench_name
    bench.site_name = bench_name
    bench.path = bench_path
    bench.running = True
    cfg = bench.bench_config
    cfg.runtime = BenchRuntime.mount
    cfg.deploy_state = None
    cfg.get_newrelic_config.return_value = None
    cfg.get_database_config.return_value = MagicMock(name="database_config")

    bench_cls = MagicMock(name="Bench")
    bench_cls.get_object.return_value = bench

    with (
        patch("frappe_manager.utils.callbacks.CLI_BENCHES_DIRECTORY", tmp_path),
        patch("frappe_manager.commands.update.CLI_BENCHES_DIRECTORY", tmp_path),
        patch("frappe_manager.commands.update.Bench", bench_cls),
        patch("frappe_manager.commands.update.check_bench_migration_required"),
        patch("frappe_manager.commands.update.spinner", _null_spinner),
    ):
        yield app, bench_name


def _invoke_update(update_world, db_ca_arg):
    app, bench_name = update_world
    return runner.invoke(
        app,
        [bench_name, "--db-ca", str(db_ca_arg)],
        obj={"services": MagicMock(), "fm_config_manager": MagicMock(), "site": None},
    )


class TestUpdateDbCaExistenceGuard:
    """``db_tls._validated_ca_source``'s existence check, not the readable=False fix: click's own
    ``exists=False`` default lets a missing path or a directory through unblocked regardless of
    ``readable``, so these two do not discriminate that flag. Kept anyway because no other test
    drives ``install_site_ca``'s real validation through the CLI; everywhere else in the suite
    ``install_site_ca`` is mocked out."""

    def test_missing_file_is_refused_by_fm_not_click(self, update_world, tmp_path):
        result = _invoke_update(update_world, tmp_path / "nope.pem")

        assert result.exit_code != 0
        assert not _is_click_path_failure(result.output)
        assert "CA file not found" in result.output

    def test_a_directory_is_refused_by_fm_not_click(self, update_world, tmp_path):
        a_directory = tmp_path / "a-directory"
        a_directory.mkdir()

        result = _invoke_update(update_world, a_directory)

        assert result.exit_code != 0
        assert not _is_click_path_failure(result.output)
        assert "CA path is not a file" in result.output


class TestUpdateDbCaReadableGate:
    """``db_tls._validated_ca_source`` is the hand check; it used to be dead via the CLI for the
    same reason create's was: click's implicit readable=True intercepted first."""

    @requires_non_root
    def test_an_unreadable_existing_file_is_refused_by_fm_not_click(self, update_world, tmp_path):
        """The PermissionError branch db_tls.py:127-143 carries: dead via the CLI before
        ``readable=False``, still live today for bench_orchestrator's internal (non-CLI) caller."""
        ca = tmp_path / "ca.pem"
        ca.write_text("ca")
        ca.chmod(0o000)
        try:
            result = _invoke_update(update_world, ca)
        finally:
            ca.chmod(0o644)

        assert result.exit_code != 0
        assert not _is_click_path_failure(result.output)
        assert "CA file is not readable" in result.output


# --------------------------------------------------------------------- fm ssl add --cert/--key/--ca


ADD_MODULE = "frappe_manager.commands.ssl.add"
BENCH = "mybench"
DOMAIN = "example.com"


@pytest.fixture
def ssl_add_cli():
    app = typer.Typer()
    app.command()(add_certificate)
    return app


def _invoke_ssl_add(cli, cert_arg, key_arg):
    with patch(f"{ADD_MODULE}._add_bench_certificate") as issue:
        result = runner.invoke(
            cli,
            [f"{BENCH}/{DOMAIN}", "--custom", "--cert", str(cert_arg), "--key", str(key_arg)],
            obj={"services": MagicMock(name="services_manager")},
        )
    return result, issue


class TestSslAddCertReadableGate:
    """The downstream hand check (``CustomCertificateService._read_file``, which wraps a read
    failure into ``SSLCertificateGenerateFailed``) is exercised past ``add_certificate``, so what
    is pinned at this layer is that the CLI stops BLOCKING it: an unreadable --cert must reach
    ``_add_bench_certificate``, not die at argument parsing with click's own message.
    """

    @requires_non_root
    def test_an_unreadable_existing_cert_reaches_the_certificate_import_not_click(self, ssl_add_cli, tmp_path):
        cert = tmp_path / "a.crt"
        cert.write_text("c")
        key = tmp_path / "a.key"
        key.write_text("k")
        cert.chmod(0o000)
        try:
            result, issue = _invoke_ssl_add(ssl_add_cli, cert, key)
        finally:
            cert.chmod(0o644)

        assert not _is_click_path_failure(result.output)
        # readable=False means click no longer decides this: the mocked import call is reached
        # with the unreadable path, exactly as it would be for a readable one, and it is
        # CustomCertificateService._read_file (already fm's own wording) that has the final say.
        issue.assert_called_once()
        assert issue.call_args.kwargs["cert_path"] == cert


# --------------------------------------------------------------------- fm maintenance --page


MAINT_MODULE = "frappe_manager.commands.maintenance"


@pytest.fixture
def maintenance_world(tmp_path):
    app = typer.Typer()
    app.command()(maintenance)

    bench_name = "mybench.localhost"
    (tmp_path / bench_name).mkdir(parents=True)

    services = MagicMock(name="services")
    services.proxy_storage.dirs.vhostd.host = str(tmp_path / "vhostd")
    services.proxy_storage.dirs.html.host = str(tmp_path / "html")

    with (
        patch("frappe_manager.utils.callbacks.CLI_BENCHES_DIRECTORY", tmp_path),
        patch(f"{MAINT_MODULE}.check_bench_migration_required"),
    ):
        yield app, bench_name, services


def _invoke_maintenance(maintenance_world, page_arg):
    app, bench_name, services = maintenance_world
    return runner.invoke(app, [bench_name, "--page", str(page_arg)], obj={"services": services})


class TestMaintenancePageReadableGate:
    """``--page`` had only an existence check ("--page file not found"), already pinned via the
    CLI by ``test_auth_migrate_shell_maintenance_contract.py``; a readability check is added
    alongside it here so silencing click's implicit gate does not turn an unreadable page file
    into a raw, uncaught ``PermissionError`` traceback."""

    @requires_non_root
    def test_an_unreadable_existing_file_is_refused_by_fm_not_click(self, maintenance_world, tmp_path):
        page = tmp_path / "page.html"
        page.write_text("<html></html>")
        page.chmod(0o000)
        try:
            result = _invoke_maintenance(maintenance_world, page)
        finally:
            page.chmod(0o644)

        assert result.exit_code != 0
        assert not _is_click_path_failure(result.output)
        assert "--page file is not readable" in result.output

