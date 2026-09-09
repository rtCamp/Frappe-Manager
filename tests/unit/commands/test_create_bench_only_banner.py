"""`fm create BENCH --bench-only` used to announce a site it would never create.

The banner at the end of the fresh-bench path in `create.py` says "Bench X will serve the site Y."
whenever the bench name and the derived site name differ, with no check of `--bench-only` at all.
That flag skips `record_site` (see the comment above it: "an empty table is the correct record of a
bench with no sites") and the orchestrator's `_run_creation` skips phase 4 (site creation) and phase
6 (app install) entirely, so the banner announced a site this invocation would never create -- and it
fired before phase 1 had even checked the Docker images, so it was premature on top of that.

Fixed by suppressing the banner for `--bench-only` rather than replacing it, because the orchestrator
already tells the truth once the bench-only work actually finishes: `_report_bench_only_created`
prints "Created bench: X" after phase 5. A second, earlier announcement here would either duplicate
that accurate completion message or promise something before any work has started.
"""

import importlib
from unittest.mock import MagicMock, patch

import pytest
import typer
from typer.testing import CliRunner

from frappe_manager.commands.create import create

runner = CliRunner()


@pytest.fixture
def benches(tmp_path):
    """An empty bench root, so neither address below collides with an existing directory."""
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
    """Invoke with BenchService mocked out, so the body runs for real up to (but not through)
    the actual creation call -- exactly far enough for the banner to have printed or not."""
    with patch("frappe_manager.commands.create.BenchService") as bench_service_cls:
        result = runner.invoke(
            cli,
            args,
            obj={"services": MagicMock(), "verbose": False, "fm_config_manager": MagicMock()},
        )
    return result, bench_service_cls


def _said(result) -> str:
    """The output with rich's box-drawing/line-wrap noise removed. See test_create_guards.py."""
    text = (result.output or "").translate({ord(c): " " for c in "\u2502\u2500\u256d\u256e\u2570\u256f"})
    return " ".join(text.split())


def test_bench_only_does_not_claim_it_will_serve_a_site(cli, benches):
    result, bench_service_cls = _invoke(cli, ["fresh", "--bench-only"])

    # Reached and called create_bench, so the banner had its chance to print and did not.
    assert bench_service_cls.return_value.create_bench.called is True, _said(result)
    assert "will serve" not in _said(result)


def test_a_plain_create_still_announces_its_site(cli, benches):
    """The negative control: the guard above must be `--bench-only`-specific, not a blanket
    removal of the banner. A plain `fm create BENCH` still creates a site and must still say so."""
    result, bench_service_cls = _invoke(cli, ["fresh"])

    assert bench_service_cls.return_value.create_bench.called is True, _said(result)
    said = _said(result)
    assert "will serve" in said
    assert "fresh" in said
    assert "fresh.localhost" in said


def test_add_site_still_announces_the_site_and_bench_by_name(monkeypatch):
    """`fm create BENCH/SITE` goes through `_add_site_to_bench`, not the banner above -- a
    completely separate print. Pinned here as the third leg of the "other paths" check, so a
    later change to one message does not silently drift the other two out of sync."""
    create_mod = importlib.import_module("frappe_manager.commands.create")

    printed: list[str] = []
    output = MagicMock()
    output.print.side_effect = lambda msg, **_kw: printed.append(msg)

    bench = MagicMock()
    bench.bench_config.sites = {}
    bench.bench_config.site_names = ["shop.localhost", "second.example.com"]

    service = MagicMock()
    service.get_bench.return_value = bench
    monkeypatch.setattr(create_mod, "BenchService", lambda *_a, **_kw: service)
    monkeypatch.setattr(create_mod, "get_global_output_handler", lambda: output)

    create_mod._add_site_to_bench(
        benchname="shop",
        site="second.example.com",
        services_manager=MagicMock(),
        verbose=False,
        apps=[],
    )

    said = " ".join(printed)
    assert "second.example.com" in said
    assert "shop" in said
