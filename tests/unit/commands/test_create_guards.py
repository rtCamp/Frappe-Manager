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


# ----------------------------------- site-scoped flags on a path that would discard them


"""Three invocations used to exit 0 having thrown the operator's flag away.

`--bench-only` skips `record_site`, so an entire external database was accepted and the bench came
up on the global-db container instead: working, and pointed at the wrong server. `fm create
BENCH/SITE` reaches `_add_site_to_bench`, which has no database parameters at all. And `--bench-only`
beside a `BENCH/SITE` address is a contradiction that was resolved by ignoring the flag.

Silence is the bug in each case. A refusal that names the flags is the fix, and it has to name them:
"invalid combination" leaves the operator to guess which of eleven database flags was the problem.
"""


def test_bench_only_refuses_the_database_it_would_discard(cli, benches):
    result, bench_service_cls = _invoke(cli, ["fresh", "--bench-only", "--db-host", "h", "--db-name", "n"])

    assert result.exit_code != 0
    said = _said(result)
    assert "--db-host" in said
    assert "--db-name" in said
    assert "nothing to apply" in said
    assert bench_service_cls.called is False


def test_bench_only_refuses_aliases_it_would_discard(cli, benches):
    result, _ = _invoke(cli, ["fresh", "--bench-only", "--alias-domains", "x.example.com"])

    assert result.exit_code != 0
    assert "--alias-domains" in _said(result)


def test_bench_only_points_at_the_command_that_takes_those_flags(cli, benches):
    """The flags are not wrong, the path is: they belong to the invocation that creates a site."""
    result, _ = _invoke(cli, ["fresh", "--bench-only", "--db-host", "h"])

    assert "fm create BENCH/SITE" in _said(result)


def test_bench_only_alone_is_still_the_supported_way_to_make_an_empty_bench(cli, benches):
    """The control. A guard that refuses `--bench-only` outright would break the documented flow."""
    result, bench_service_cls = _invoke(cli, ["fresh", "--bench-only"])

    assert "nothing to apply" not in _said(result)
    assert bench_service_cls.called is True


def test_a_default_valued_flag_does_not_trip_the_guard(cli, benches):
    """`--db-port` defaults to 3306, so a source check rather than a truthiness check is required:
    reading the VALUE would refuse every `--bench-only` create ever run."""
    result, bench_service_cls = _invoke(cli, ["fresh", "--bench-only"])

    assert "--db-port" not in _said(result)
    assert bench_service_cls.called is True


def test_naming_a_site_and_saying_no_site_is_refused(cli, benches):
    """`--bench-only` used to win silently, so the site in the address was never created."""
    result, _ = _invoke(cli, ["existing/second.example.com", "--bench-only"])

    assert result.exit_code != 0
    said = _said(result)
    assert "names a site to create" in said
    assert "--bench-only" in said


def test_adding_a_site_refuses_the_database_flags_it_cannot_carry(cli, benches):
    """`_add_site_to_bench` takes `apps` and `alias_domains` and nothing else, and `record_site` is
    called with `None` for the database, so these were accepted and dropped."""
    result, _ = _invoke(cli, ["existing/second.example.com", "--db-host", "h", "--db-name", "n"])

    assert result.exit_code != 0
    said = _said(result)
    # The guard's own phrasing, not just the flag names: a help dump also lists every --db-* flag.
    assert "does not take --db-host, --db-name" in said


def test_adding_a_site_still_takes_the_aliases_it_does_forward(cli, benches):
    """`--alias-domains` IS passed through to `_add_site_to_bench`, so refusing it would remove a
    working feature. This is the line between the two halves of the guard."""
    result, _ = _invoke(cli, ["existing/second.example.com", "--alias-domains", "x.example.com"])

    assert "--alias-domains" not in _said(result)


# ----------------------------------- the BENCH/SITE dispatch itself


def test_an_address_with_a_site_part_reaches_the_add_site_path(cli, benches):
    """The dispatch, not just the guards around it.

    `fm create BENCH/SITE` raised `TypeError: _add_site_to_bench() got an unexpected keyword
    argument 'address'` for real, on a real bench, and 4618 tests did not notice: every test that
    touched this path stopped at a guard that refuses BEFORE the call, so the call itself was never
    made. The command's parameter was renamed to `address` and the rename swept this keyword with
    it, while the helper's own parameter stayed `benchname`.

    Asserting the keyword the helper actually declares is what makes a rename fail here rather than
    at the operator's terminal.
    """
    with patch("frappe_manager.commands.create._add_site_to_bench") as add_site:
        result = runner.invoke(
            cli,
            ["existing/second.example.com"],
            obj={"services": MagicMock(), "verbose": False, "fm_config_manager": MagicMock()},
        )

    assert result.exit_code == 0, _said(result)
    add_site.assert_called_once()
    # `existing` resolves to the bench DIRECTORY, which the legacy `.localhost` fallback finds.
    assert add_site.call_args.kwargs["benchname"] == "existing.localhost"
    assert add_site.call_args.kwargs["site"] == "second.example.com"


def test_the_add_site_call_matches_the_helpers_signature(cli, benches):
    """A rename that leaves the call and the definition disagreeing is invisible to a mock that
    accepts anything, so the recorded call is bound against the REAL signature."""
    import inspect

    from frappe_manager.commands.create import _add_site_to_bench

    with patch("frappe_manager.commands.create._add_site_to_bench") as add_site:
        runner.invoke(
            cli,
            ["existing/second.example.com"],
            obj={"services": MagicMock(), "verbose": False, "fm_config_manager": MagicMock()},
        )

    # Raises TypeError if the keywords the caller passes are not the ones the helper declares.
    inspect.signature(_add_site_to_bench).bind(**add_site.call_args.kwargs)
