"""`fm create BENCH/SITE` adds a site to a bench that already exists and may be serving.

The ORDER is the content of this feature, and it is deliberately not the order a fresh create uses.
A create can bring routing up early because nothing is serving yet. Here the bench's other sites are
live, so the compose re-render and the nginx recreate go LAST, after the new site is known to work:
doing them first takes every existing site down for the duration of a `new-site` that may fail.

The other half is the failure path. A failed CREATE calls `remove_bench`, which here would destroy a
bench full of working sites because one addition failed.
"""

import importlib
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from frappe_manager.commands.create import _add_site_to_bench
from frappe_manager.site_manager.bench_config import SiteConfig

BENCH = "shop"
FIRST = "shop.localhost"
SECOND = "b.example.com"


@pytest.fixture
def events() -> list[str]:
    return []


@pytest.fixture
def bench(events):
    """A bench already serving one site, with every outward call recorded in order."""
    b = MagicMock()
    b.name = BENCH
    b.path = MagicMock()
    b.bench_config = MagicMock()
    b.bench_config.sites = {FIRST: SiteConfig(database=None)}
    b.bench_config.environment_type = SimpleNamespace(value="prod")
    b.bench_config.export_to_compose_inputs.return_value = {}
    b.bench_config.site_names = [FIRST, SECOND]

    b.site_manager.create_bench_site.side_effect = lambda **kw: events.append(f"new-site:{kw.get('site')}")
    b.app_manager.install_apps_to_site.side_effect = lambda site: events.append(f"install-apps:{site}")
    b.generate_compose.side_effect = lambda _inputs: events.append("generate-compose")
    b.restart_nginx_service.side_effect = lambda force=False: events.append(f"restart-nginx:force={force}")
    b.save_bench_config.side_effect = lambda **_kw: events.append("save-config")
    return b


@pytest.fixture
def run(bench, monkeypatch):
    """Invoke the site-add with the bench above, and with output silenced."""
    # The MODULE. `frappe_manager.commands.__init__` rebinds the name `create` to the command
    # function, so plain attribute access and `import ... as` both hand back the function.
    create_mod = importlib.import_module("frappe_manager.commands.create")

    service = MagicMock()
    service.get_bench.return_value = bench
    monkeypatch.setattr(create_mod, "BenchService", lambda *_a, **_kw: service)
    monkeypatch.setattr(create_mod, "get_global_output_handler", MagicMock)

    def _run(apps: tuple[str, ...] = ("erpnext",)):
        _add_site_to_bench(
            benchname=BENCH,
            site=SECOND,
            services_manager=MagicMock(),
            verbose=False,
            apps=list(apps),
        )

    return _run


# --------------------------------------------------------------------------- the order


def test_the_site_is_created_before_its_address_is_published(run, events):
    """Publishing first would take the bench's existing sites down for the duration of a `new-site`
    that may fail, for a site that might never work."""
    run()

    assert events.index(f"new-site:{SECOND}") < events.index("generate-compose")


def test_the_apps_are_installed_before_the_address_is_published(run, events):
    """A site reachable before its apps are in is a site serving errors to real traffic."""
    run()

    assert events.index(f"install-apps:{SECOND}") < events.index("generate-compose")


def test_nginx_is_recreated_after_the_compose_is_rewritten(run, events):
    """The site map reaches nginx as an environment variable read at container start, so the order
    the other way round would recreate the container against the old map."""
    run()

    assert events.index("generate-compose") < events.index("restart-nginx:force=True")


def test_nginx_is_forced_rather_than_reloaded(run, events):
    """A reload rereads config files; the site map is an env var, which only a new container picks
    up."""
    run()

    assert "restart-nginx:force=True" in events


def test_the_config_is_saved_before_the_compose_is_rendered(run, events):
    """`export_to_compose_inputs` reads the recorded sites, so the record has to be on disk and in
    the object before the render, or the new site's domain is absent from VIRTUAL_HOST."""
    run()

    assert events.index("save-config") < events.index("generate-compose")


# ------------------------------------------------------------------- what it records


def test_the_new_site_is_recorded_alongside_the_existing_one(run, bench):
    """Recorded, not replaced: the bench serves both."""
    run()

    assert set(bench.bench_config.sites) == {FIRST, SECOND}


def test_the_added_site_does_not_become_the_bench_default(run, bench):
    """`bench use` writes a bench-WIDE default, so setting it would move every bare `bench` command
    and every unmatched request onto the new site."""
    run()

    assert bench.site_manager.create_bench_site.call_args.kwargs["set_default"] is False


def test_the_added_site_gets_a_schema_of_its_own(run, bench):
    """Never the bench's `db_name`, which names the first site's schema."""
    run()

    schema = bench.site_manager.create_bench_site.call_args.kwargs["db_name"]
    assert schema.startswith("fm_b_example_com_")


# --------------------------------------------------------------------- the failure path


def test_a_failed_site_leaves_the_bench_alone(run, bench):
    """The cleanup a failed CREATE runs is `remove_bench`. Running it here would destroy a bench full
    of working sites because one addition failed."""
    bench.site_manager.create_bench_site.side_effect = RuntimeError("new-site exploded")

    with pytest.raises(RuntimeError):
        run()

    bench.remove_bench.assert_not_called()


def test_a_failed_site_is_never_published(run, bench, events):
    """Compose is not rewritten and nginx is not touched, so no traffic is ever routed at a site that
    does not work."""
    bench.site_manager.create_bench_site.side_effect = RuntimeError("new-site exploded")

    with pytest.raises(RuntimeError):
        run()

    assert "generate-compose" not in events
    assert not any(e.startswith("restart-nginx") for e in events)


def test_a_failed_site_is_not_recorded_on_disk(run, bench):
    """The record is written to disk only after the site works, so a failed addition leaves nothing
    in `bench_config.toml` pointing at a half-made site."""
    bench.site_manager.create_bench_site.side_effect = RuntimeError("new-site exploded")

    with pytest.raises(RuntimeError):
        run()

    bench.save_bench_config.assert_not_called()


def test_a_failed_app_install_also_leaves_the_bench_alone(run, bench, events):
    """The site exists by then, so this is the case where the temptation to "clean up" is strongest
    and would be the most destructive."""
    bench.app_manager.install_apps_to_site.side_effect = RuntimeError("install exploded")

    with pytest.raises(RuntimeError):
        run()

    bench.remove_bench.assert_not_called()
    assert "generate-compose" not in events
