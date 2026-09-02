"""`fm create` must refuse a bench name whose directory already exists.

Nothing further down the create path checks: `_phase1_prepare_structure` opens with
`bench.path.mkdir(parents=True, exist_ok=True)` and then rewrites `docker-compose.yml`
unconditionally, and `_handle_creation_failure` offers to remove the directory it just clobbered.
The only other gate is `validate_domains_unique`, which is a *domain* check and is switched off by
`--allow-domain-conflicts` (or by `validation.enforce_domain_uniqueness = false`), so it cannot
stand in for a directory guard.

The guard therefore has to be the parse-time argument callback, and these tests pin it there: it
must fire before the body constructs anything, and it must fire independently of
`--allow-domain-conflicts`.
"""

from unittest.mock import MagicMock, patch

import pytest
import typer
from typer.testing import CliRunner

from frappe_manager.commands.create import create

runner = CliRunner()


@pytest.fixture
def benches(tmp_path):
    """Bench root containing one existing bench, and nothing else."""
    root = tmp_path / "sites"
    (root / "existing.localhost").mkdir(parents=True)
    with patch("frappe_manager.utils.callbacks.CLI_BENCHES_DIRECTORY", root):
        yield root


@pytest.fixture
def cli():
    test_app = typer.Typer()
    test_app.command("create")(create)
    return test_app


def _invoke(cli, args):
    """Invoke with a context object, so a name that passes the guard reaches the body."""
    with patch("frappe_manager.commands.create.BenchService") as bench_service_cls:
        result = runner.invoke(
            cli,
            args,
            obj={"services": MagicMock(), "verbose": False, "fm_config_manager": MagicMock()},
        )
    return result, bench_service_cls


def _said(result) -> str:
    """The output with rich's line breaks removed.

    Rich reflows a panel to the terminal width, so a refusal that reads "already exists" on one
    machine is split across two lines on another. Asserting on the raw output makes a test hostage
    to the LENGTH of unrelated strings: these three broke when the `create` metavar grew from
    `BENCHNAME` to `BENCH(/SITE)` and pushed "exists" onto the next line.

    The panel's box-drawing characters go too, because they sit BETWEEN the words a wrap split.
    """
    text = (result.output or "").translate({ord(c): " " for c in "\u2502\u2500\u256d\u256e\u2570\u256f"})
    return " ".join(text.split())


def test_existing_bench_directory_aborts_create(cli, benches):
    result, bench_service_cls = _invoke(cli, ["existing.localhost"])

    assert result.exit_code != 0
    assert "already exists" in _said(result)
    # The abort happens before the body builds anything, so nothing can be overwritten.
    assert bench_service_cls.called is False


def test_allow_domain_conflicts_does_not_disable_the_directory_guard(cli, benches):
    """`--allow-domain-conflicts` is a domain-only override; it must not license an overwrite."""
    result, bench_service_cls = _invoke(cli, ["existing.localhost", "--allow-domain-conflicts"])

    assert result.exit_code != 0
    assert "already exists" in _said(result)
    assert bench_service_cls.called is False
    assert bench_service_cls.return_value.create_bench.called is False


def test_bare_name_is_normalised_before_the_existence_check(cli, benches):
    """The callback validates first, so `existing` resolves to `existing.localhost` and is caught."""
    result, bench_service_cls = _invoke(cli, ["existing"])

    assert result.exit_code != 0
    assert "already exists" in _said(result)
    assert bench_service_cls.called is False


def test_a_free_bench_name_passes_the_guard_into_the_body(cli, benches):
    """Negative control: the guard is directory-specific, not a blanket refusal."""
    result, bench_service_cls = _invoke(cli, ["fresh.localhost"])

    assert "already exists" not in _said(result)
    # The body's first act is constructing BenchService, so this proves the guard let the name past.
    assert bench_service_cls.called is True
