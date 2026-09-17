"""`fm create --dry-run` answers what the flag surface cannot: what will this bench BE?

`fm create` composes its config in three layers -- create defaults, then each `--config` in order,
then the explicit flags (`_build_bench_config`). The result was only observable by creating the
bench and reading `bench_config.toml` afterwards, which is a poor way to find out that a `--config`
key you meant to apply was overridden by a flag default.

This is a preview, not a plan/apply split: `fm create` does not need one. It cannot leave a
half-built bench (`--remove-on-failure`, plus a removal prompt, plus every refusal raised before
the bench directory exists), so there is no partially-applied state for a plan to prevent.

Rendered through `export_to_toml` -- the same writer create uses -- so the preview cannot disagree
with what lands on disk, and every field fm refuses to persist is absent here for free.
"""

from unittest.mock import MagicMock, patch

import pytest
import typer
from typer.testing import CliRunner

from frappe_manager.commands.create import create

runner = CliRunner()


@pytest.fixture
def benches(tmp_path):
    root = tmp_path / "sites"
    root.mkdir(parents=True)
    with patch("frappe_manager.utils.callbacks.CLI_BENCHES_DIRECTORY", root):
        yield root


@pytest.fixture
def cli():
    test_app = typer.Typer()
    test_app.command("create")(create)
    return test_app


def _invoke(cli, args):
    with patch("frappe_manager.commands.create.BenchService") as bench_service_cls:
        result = runner.invoke(
            cli,
            [*args, "--dry-run"],
            obj={"services": MagicMock(), "verbose": False, "fm_config_manager": MagicMock()},
        )
    return result, bench_service_cls


@pytest.mark.timeout(20)
def test_it_creates_nothing(cli, benches):
    result, bench_service_cls = _invoke(cli, ["new.localhost"])

    assert result.exit_code == 0, result.output
    assert bench_service_cls.return_value.create_bench.called is False
    assert not (benches / "new.localhost").exists()


@pytest.mark.timeout(20)
def test_it_prints_the_resolved_config(cli, benches):
    result, _ = _invoke(cli, ["new.localhost"])

    assert 'name = "new.localhost"' in result.output
    assert "runtime =" in result.output


@pytest.mark.timeout(20)
def test_an_explicit_flag_beats_a_config_key(cli, benches):
    """The layering that is otherwise invisible: flags win over `--config`, and the config keys a
    flag does NOT cover must survive."""
    result, _ = _invoke(
        cli,
        ["new.localhost", "--config", 'environment="prod"', "--config", 'upload_limit="1G"', "-e", "dev"],
    )

    assert 'environment = "dev"' in result.output
    assert 'upload_limit = "1G"' in result.output


@pytest.mark.timeout(20)
def test_the_github_token_is_never_echoed(cli, benches):
    """It IS written to disk, so the writer keeps it -- but a preview lands in a scrollback buffer
    and in any CI log that captures stdout, and it is the user's own credential."""
    result, _ = _invoke(cli, ["new.localhost", "--github-token", "ghp_SUPERSECRET"])

    assert "ghp_SUPERSECRET" not in result.output
    assert "<redacted>" in result.output


@pytest.mark.timeout(20)
def test_provisioning_credentials_are_absent_entirely(cli, benches):
    """`NOT_WRITTEN_TO_DISK` covers the admin credentials and the generated DB password, and the
    preview inherits that from the real writer rather than re-listing them."""
    result, _ = _invoke(cli, ["new.localhost", "--admin-pass", "hunter2"])

    assert "hunter2" not in result.output
    assert "admin_pass" not in result.output
