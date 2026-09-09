"""Phase 3: an ordinary invocation loads a bench's config at least twice -- once in the
parameter callback that resolves `BENCH[/SITE]` (`bench_site_callback` -> `_recorded_sites` ->
`BenchConfig.import_from_toml`), then again in the command body (`Bench.get_object` -> the same
`import_from_toml`). Before the fix, an unrecognised key warned once per load, so `fm reset
shop/site` painted the SAME typo twice on one invocation.

Deduplication lives in `bench_config.py` itself (`_should_warn_once`, keyed by path and the exact
unknown-key set found), not in `callbacks.py`: both call sites funnel through the same
`BenchConfig.import_from_toml`, so one choke point fixes every caller without callbacks.py having
to know anything about warning state. That also means a parameter callback never needs to catch
or suppress a warning by hand -- it already swallows a load FAILURE (`except Exception: return
[bench]`), and a mere warning was never something it had to special-case.
"""

from unittest.mock import MagicMock

from frappe_manager.output_manager import set_global_output_handler
from frappe_manager.output_manager.base import OutputHandler
from frappe_manager.site_manager.bench_config import BenchConfig
from frappe_manager.utils.callbacks import bench_site_callback
from frappe_manager.utils.helpers import get_current_fm_version

BENCH = "shop.localhost"


def _bench_with_a_typo(root, bench_dir_name: str) -> None:
    bench_dir = root / bench_dir_name
    bench_dir.mkdir(parents=True, exist_ok=True)
    (bench_dir / "bench_config.toml").write_text(
        f'name = "{bench_dir_name}"\n'
        "developer_mode = false\n"
        "admin_tools = false\n"
        'environment = "prod"\n'
        "typoed_top_level = true\n"
        "\n[migration_state]\n"
        f'migrated_to = "{get_current_fm_version()}"\n'
    )


def test_a_reset_shaped_invocation_warns_once_not_twice(tmp_path, monkeypatch):
    root = tmp_path / "sites"
    _bench_with_a_typo(root, BENCH)
    monkeypatch.setattr("frappe_manager.utils.callbacks.CLI_BENCHES_DIRECTORY", root)

    handler = MagicMock(spec=OutputHandler)
    set_global_output_handler(handler)
    try:
        # The parameter callback: resolves the address before the command body runs.
        ctx = MagicMock()
        ctx.obj = {}
        bench = bench_site_callback(ctx, BENCH)
        assert bench == BENCH

        # The command body's own load (Bench.get_object -> BenchConfig.import_from_toml),
        # the exact same file, unchanged, later in the same invocation.
        BenchConfig.import_from_toml(root / BENCH / "bench_config.toml")
    finally:
        set_global_output_handler(None)

    handler.warning.assert_called_once()
    assert "typoed_top_level" in handler.warning.call_args.args[0]


def test_a_fixed_typo_between_invocations_warns_again(tmp_path, monkeypatch):
    """The dedup key includes the unknown-key set, not just the path: a long-lived process (or a
    later, separate command) that reloads a bench whose file has since changed must not be stuck
    silent by a stale suppression from the prior content."""
    root = tmp_path / "sites"
    _bench_with_a_typo(root, BENCH)
    monkeypatch.setattr("frappe_manager.utils.callbacks.CLI_BENCHES_DIRECTORY", root)
    config_path = root / BENCH / "bench_config.toml"

    handler = MagicMock(spec=OutputHandler)
    set_global_output_handler(handler)
    try:
        BenchConfig.import_from_toml(config_path)
        assert handler.warning.call_count == 1

        # Operator fixes the typo, introduces a different one.
        config_path.write_text(config_path.read_text().replace("typoed_top_level", "another_typo"))
        BenchConfig.import_from_toml(config_path)
    finally:
        set_global_output_handler(None)

    assert handler.warning.call_count == 2
    assert "another_typo" in handler.warning.call_args.args[0]


def _clean_bench(root, bench_dir_name: str) -> None:
    """The same bench, same path, with the typo fixed -- used to prove the dedup cache does not
    outlive a clean load (see `_forget_stale_warning` in bench_config.py)."""
    bench_dir = root / bench_dir_name
    (bench_dir / "bench_config.toml").write_text(
        f'name = "{bench_dir_name}"\n'
        "developer_mode = false\n"
        "admin_tools = false\n"
        'environment = "prod"\n'
        "\n[migration_state]\n"
        f'migrated_to = "{get_current_fm_version()}"\n'
    )


def test_a_fix_then_the_same_typo_reappearing_warns_a_second_time(tmp_path):
    """The dedup cache used to only ever get written when `unknown_keys` was non-empty, so a
    load that found nothing (the clean rewrite in the middle) never touched it -- the SIGNATURE
    from load #1 survived untouched, and load #3 (the identical typo, back at the same path)
    matched that stale signature and stayed silent. `_forget_stale_warning` is what a clean load
    calls instead, so the stale entry cannot outlive the content that earned it."""
    root = tmp_path / "sites"
    _bench_with_a_typo(root, BENCH)
    config_path = root / BENCH / "bench_config.toml"

    handler = MagicMock(spec=OutputHandler)
    set_global_output_handler(handler)
    try:
        BenchConfig.import_from_toml(config_path)
        assert handler.warning.call_count == 1

        # Operator fixes the typo; the file is clean, so nothing warns here.
        _clean_bench(root, BENCH)
        BenchConfig.import_from_toml(config_path)
        assert handler.warning.call_count == 1

        # Operator re-introduces the EXACT SAME typo at the SAME path.
        _bench_with_a_typo(root, BENCH)
        BenchConfig.import_from_toml(config_path)
    finally:
        set_global_output_handler(None)

    assert handler.warning.call_count == 2
    assert "typoed_top_level" in handler.warning.call_args.args[0]


def test_a_clean_load_does_not_retain_a_dedup_cache_entry(tmp_path):
    """Whitebox on the cache itself: after a typo'd load followed by a clean reload of the same
    path, `_warned_unknown_keys` must not still be holding an entry for that path -- an entry
    surviving a clean load is exactly the staleness `_forget_stale_warning` exists to prevent,
    independent of whether a later reload happens to re-warn."""
    from frappe_manager.site_manager.bench_config import _warned_unknown_keys

    root = tmp_path / "sites"
    _bench_with_a_typo(root, BENCH)
    config_path = root / BENCH / "bench_config.toml"

    handler = MagicMock(spec=OutputHandler)
    set_global_output_handler(handler)
    try:
        BenchConfig.import_from_toml(config_path)
        assert str(config_path) in _warned_unknown_keys

        _clean_bench(root, BENCH)
        BenchConfig.import_from_toml(config_path)
    finally:
        set_global_output_handler(None)

    assert str(config_path) not in _warned_unknown_keys
