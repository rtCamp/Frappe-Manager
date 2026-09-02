"""A bench whose nginx base config is missing must repair itself on the next start.

`create_compose_dirs` (which seeds `configs/nginx/conf/` from the nginx image) runs only at
bench CREATION. A bench created while the seeding was guarded on the `conf/` directory
existing -- rather than on the `nginx.conf` marker file -- ended up with fm's two overlay
files and none of the image's base config: nginx died with `/etc/nginx/nginx.conf: No such
file or directory` and the site served HTTP 503, and no command a user ran ever re-seeded it.

`Bench.start` therefore runs the seeding on the way in, before any container comes up. It
also refreshes fm's own overlay there, for the same reason at one remove: `real-ip.conf` is
written by `generate_compose`, so a bench whose compose has not been regenerated since that
landed never receives it, and every request then reaches the app carrying the global proxy's
address instead of the visitor's -- one IP for the entire internet, which silently defeats
frappe's per-IP rate limiting and makes the Activity Log useless.

These tests defend the properties that makes safe: it heals a broken bench, it is invisible on
a healthy one (a hand-tuned base conf is never touched, the seeding is not even entered, and an
up-to-date overlay causes no reload churn), and a failure is reported but never blocks the start.
"""

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from frappe_manager.site_manager.bench_config import BenchRuntime
from frappe_manager.site_manager.modules.bench_docker import BenchDockerOps
from frappe_manager.site_manager.modules.realip import build_bench_realip_conf
from frappe_manager.site_manager.site import Bench

# `bench.name` reaches these tests as a DOMAIN, not a bench name: the overlay refresh writes one
# nginx-proxy `vhostd/<domain>` file per entry of `Bench.domains`, which delegates to
# `BenchConfig.domains` -- each site's own name followed by that site's aliases. A config with no
# `[sites]` table serves one site named after the bench, so that list is `[bench.name]` here.
# The bench name and the served domain are one string until phase 3 splits them, so the vhostd
# assertions below key off this constant rather than repeating the literal: when the domain stops
# coming from `bench.name`, they move with it instead of still matching by accident.
DOMAIN = "heal.localhost"

pytestmark = pytest.mark.timeout(15)

IMAGE_FILES = {
    "nginx.conf": "events {}\n",
    "mime.types": "types {}\n",
    "conf.d/default.conf": "server { listen 80; }\n",
}


def _fake_image_copy(calls: list[dict]):
    """Stand in for `host_run_cp`: materialise the image's /etc/nginx at the destination."""

    def _copy(image, source, destination, docker):
        calls.append({"image": image, "source": source, "destination": destination})
        dest = Path(destination)
        for rel, body in IMAGE_FILES.items():
            target = dest / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(body)
        (dest / "modules").symlink_to("/usr/lib/nginx/modules")

    return _copy


def _real_ops(path: Path) -> BenchDockerOps:
    """Real docker ops (so the production seeding runs) with only docker itself mocked."""
    ops = object.__new__(BenchDockerOps)
    ops.docker_client = MagicMock()
    ops.output = MagicMock()
    ops.path = path
    ops.config = SimpleNamespace(runtime=BenchRuntime.mount)
    ops.compose_file_manager = MagicMock()
    ops.compose_file_manager.yml = {"services": {"nginx": {"image": "nginx:test"}, "frappe": {"image": "frappe:test"}}}
    return ops


def _bench(path: Path, docker_ops) -> Bench:
    bench = Bench.__new__(Bench)  # bypass __init__: no docker, no compose, no services
    bench.name = DOMAIN
    bench.path = path
    bench.logger = MagicMock()
    bench.output = MagicMock()
    bench.docker_ops = docker_ops
    bench.orchestrator = MagicMock()
    # `upload_limit` is read by the same overlay refresh these tests exercise, and it always has a
    # value on a real BenchConfig (default 50M). Omitting it here made the refresh raise
    # AttributeError, which the best-effort start path then swallowed as a warning -- so every
    # assertion below about real-ip.conf passed or failed for the wrong reason.
    # `domains` is what `Bench.domains` delegates to now that aliases hang off a site rather than
    # the bench, and it is what the vhostd half of the refresh iterates. One site, DOMAIN, with no
    # aliases of its own -- the same list a real BenchConfig with an empty `[sites]` table returns.
    # `site_names` is the same story as `upload_limit` above, and it bit in the same way: the refresh
    # now writes `max_file_size` into EVERY site's site_config.json rather than only the bench's own,
    # so a stand-in without it raised AttributeError, start() warned, and the vhostd assertions below
    # failed on a missing file instead of a wrong one. Empty, which is what a real BenchConfig with no
    # `[sites]` table returns, so this bench falls back to its single `site_name` exactly as before.
    bench.bench_config = SimpleNamespace(
        auth=None, admin_tools=False, upload_limit="50M", sites=None, domains=[DOMAIN], site_names=[]
    )
    bench.bench_nginx_controller = MagicMock()
    # The overlay refresh reads the subnet from the services compose, which is the pinned
    # source of truth. Wiring it keeps these tests off the live-docker fallback, which would
    # otherwise make them depend on whether fm's network happens to be up on this machine.
    bench.services = MagicMock()
    bench.services.compose_file_manager.yml = {
        "networks": {"global-frontend-network": {"ipam": {"config": [{"subnet": "10.1.0.0/16"}]}}}
    }
    return bench


def _conf_dir(path: Path) -> Path:
    return path / "configs" / "nginx" / "conf"


def _break_bench(path: Path) -> Path:
    """The 739e629 damage: fm's overlay is on disk, the image's base config is not."""
    conf = _conf_dir(path)
    (conf / "custom").mkdir(parents=True)
    (conf / "custom" / "real-ip.conf").write_text("set_real_ip_from 10.0.0.0/8;\n")
    return conf


def test_a_bench_missing_nginx_conf_is_healed_on_start(tmp_path, monkeypatch):
    """The base config lands and fm's overlay survives, and the start still happens."""
    monkeypatch.setattr("frappe_manager.site_manager.modules.bench_docker.host_run_cp", _fake_image_copy([]))
    conf = _break_bench(tmp_path)
    bench = _bench(tmp_path, _real_ops(tmp_path))

    bench.start()

    assert (conf / "nginx.conf").read_text() == "events {}\n"
    assert (conf / "conf.d" / "default.conf").exists()
    # The overlay survives the seeding. Its CONTENT is fm's, not the placeholder written
    # above: start refreshes it, and the file itself says "generated by fm; do not edit".
    assert (conf / "custom" / "real-ip.conf").read_text() == build_bench_realip_conf("10.1.0.0/16")
    assert bench.orchestrator.start_bench.call_count == 1


def test_the_heal_lands_before_any_container_is_started(tmp_path, monkeypatch):
    """Ordering is the whole point: nginx must find its config when it boots, not after."""
    monkeypatch.setattr("frappe_manager.site_manager.modules.bench_docker.host_run_cp", _fake_image_copy([]))
    conf = _break_bench(tmp_path)
    bench = _bench(tmp_path, _real_ops(tmp_path))
    seen: list[bool] = []
    bench.orchestrator.start_bench.side_effect = lambda **_: seen.append((conf / "nginx.conf").exists())

    bench.start()

    assert seen == [True]


def test_a_hand_tuned_nginx_conf_is_never_touched(tmp_path, monkeypatch):
    """An operator's edited base config is not clobbered on every start."""
    tuned = "worker_processes 8;\nevents { worker_connections 4096; }\n"
    monkeypatch.setattr(
        "frappe_manager.site_manager.modules.bench_docker.host_run_cp",
        lambda **_: pytest.fail("a healthy bench must not be re-seeded from the image"),
    )
    conf = _conf_dir(tmp_path)
    conf.mkdir(parents=True)
    (conf / "nginx.conf").write_text(tuned)
    bench = _bench(tmp_path, _real_ops(tmp_path))

    bench.start()

    assert (conf / "nginx.conf").read_text() == tuned


def test_a_healthy_bench_does_not_enter_the_seeding_at_all(tmp_path):
    """No directory churn and no compose/image lookups on the common path."""
    conf = _conf_dir(tmp_path)
    conf.mkdir(parents=True)
    (conf / "nginx.conf").write_text("events {}\n")
    docker_ops = MagicMock()
    bench = _bench(tmp_path, docker_ops)

    bench.start()

    docker_ops.create_compose_dirs.assert_not_called()
    assert bench.orchestrator.start_bench.call_count == 1


def test_healing_does_not_recopy_the_prebaked_runtimes(tmp_path, monkeypatch):
    """Only nginx is copied out of an image: `.uv`/`.fnm` are hundreds of MB and already placed
    at creation (and deliberately absent from the host in image runtime)."""
    calls: list[dict] = []
    monkeypatch.setattr("frappe_manager.site_manager.modules.bench_docker.host_run_cp", _fake_image_copy(calls))
    _break_bench(tmp_path)
    bench = _bench(tmp_path, _real_ops(tmp_path))

    bench.start()

    assert [call["source"] for call in calls] == ["/etc/nginx"]
    assert not (tmp_path / "workspace" / "frappe-bench" / ".uv").exists()
    assert not (tmp_path / "workspace" / "frappe-bench" / ".fnm").exists()


def test_the_heal_asks_for_the_cheap_variant(tmp_path):
    """The runtime skip is a decision of the start path, not of the seeding it delegates to."""
    docker_ops = MagicMock()
    bench = _bench(tmp_path, docker_ops)

    bench.start()

    docker_ops.create_compose_dirs.assert_called_once_with(copy_runtimes=False)


def test_a_failed_heal_does_not_block_the_start_and_is_reported(tmp_path):
    """Healing is best effort, but never silent."""
    docker_ops = MagicMock()
    docker_ops.create_compose_dirs.side_effect = RuntimeError("nginx:test not present locally")
    bench = _bench(tmp_path, docker_ops)

    bench.start()

    assert bench.orchestrator.start_bench.call_count == 1
    warnings = " ".join(str(call.args[0]) for call in bench.output.warning.call_args_list)
    assert "nginx:test not present locally" in warnings


def test_a_bench_with_no_config_directory_at_all_still_starts(tmp_path, monkeypatch):
    """Nothing on disk and docker unreachable: the worst case must degrade, not raise."""
    monkeypatch.setattr(
        "frappe_manager.site_manager.modules.bench_docker.host_run_cp",
        MagicMock(side_effect=RuntimeError("cannot connect to the docker daemon")),
    )
    bench = _bench(tmp_path, _real_ops(tmp_path))

    bench.start()

    assert bench.orchestrator.start_bench.call_count == 1
    warnings = " ".join(str(call.args[0]) for call in bench.output.warning.call_args_list)
    assert "cannot connect to the docker daemon" in warnings


def _healthy_base(path: Path) -> Path:
    """A bench whose base config is intact, so the seeding is not entered at all."""
    conf = _conf_dir(path)
    (conf / "conf.d").mkdir(parents=True)
    (conf / "nginx.conf").write_text("events {}\n")
    (conf / "conf.d" / "default.conf").write_text("server { listen 80; }\n")
    return conf


def test_a_bench_missing_the_real_ip_overlay_gains_it_on_start(tmp_path):
    """The bug: nothing re-materialises real-ip.conf after creation, so a bench that missed it
    reports the proxy's address as every visitor's IP for the rest of its life."""
    conf = _healthy_base(tmp_path)
    bench = _bench(tmp_path, _real_ops(tmp_path))

    bench.start()

    written = (conf / "custom" / "real-ip.conf").read_text()
    assert "set_real_ip_from 10.1.0.0/16;" in written
    assert "real_ip_header X-Real-IP;" in written


def test_an_existing_bench_gains_its_upload_limit_on_start(tmp_path):
    """Benches created before the limit was applied at all have no proxy vhost entry, so the proxy
    refuses anything over its own 1M default and the bench answers 413 however permissive its own
    nginx conf is. `fm start` heals both halves, so no migration is needed.
    """
    conf = _healthy_base(tmp_path)
    vhostd = tmp_path / "services" / "nginx-proxy" / "vhostd"
    vhostd.mkdir(parents=True)
    bench = _bench(tmp_path, _real_ops(tmp_path))
    bench.services.path = tmp_path / "services"

    bench.start()

    assert (conf / "custom" / "upload-limit.conf").read_text() == "client_max_body_size 50m;\n"
    assert "client_max_body_size 50m;" in (vhostd / DOMAIN).read_text()


def test_the_proxy_is_not_reloaded_when_the_limit_already_matches(tmp_path):
    """The proxy is shared by every bench, so a start that changed nothing must not reload it."""
    _healthy_base(tmp_path)
    vhostd = tmp_path / "services" / "nginx-proxy" / "vhostd"
    vhostd.mkdir(parents=True)
    (vhostd / DOMAIN).write_text("\nclient_max_body_size 50m;\n")
    bench = _bench(tmp_path, _real_ops(tmp_path))
    bench.services.path = tmp_path / "services"

    bench.start()

    bench.services.nginx_controller.reload.assert_not_called()


def test_the_real_ip_overlay_lands_before_any_container_is_started(tmp_path):
    """Same ordering rule as the seeding: nginx reads its includes once, at boot."""
    conf = _healthy_base(tmp_path)
    bench = _bench(tmp_path, _real_ops(tmp_path))
    seen: list[bool] = []
    bench.orchestrator.start_bench.side_effect = lambda **_: seen.append((conf / "custom" / "real-ip.conf").exists())

    bench.start()

    assert seen == [True]


def test_an_up_to_date_overlay_is_not_rewritten_and_triggers_no_reload(tmp_path):
    """Every `fm start` would otherwise reload nginx for a file it did not change."""
    conf = _healthy_base(tmp_path)
    (conf / "custom").mkdir(parents=True)
    (conf / "custom" / "real-ip.conf").write_text(build_bench_realip_conf("10.1.0.0/16"))
    # Every fm-managed conf has to be current for "nothing changed" to mean anything; one stale
    # file is enough to trigger the reload this test says must not happen.
    (conf / "custom" / "upload-limit.conf").write_text("client_max_body_size 50m;\n")
    bench = _bench(tmp_path, _real_ops(tmp_path))

    bench.start()

    bench.bench_nginx_controller.reload.assert_not_called()


def test_a_failed_overlay_refresh_does_not_block_the_start_and_is_reported(tmp_path):
    """Best effort, like the seeding: a bench must still come up, and the operator must hear."""

    class _UnreadableConfig:
        """Every field the refresh reads raises, so the test does not depend on which is read
        first: reordering the refresh should not silently turn this into an AttributeError with a
        different message."""

        @property
        def auth(self):
            raise RuntimeError("bench config unreadable")

        @property
        def upload_limit(self):
            raise RuntimeError("bench config unreadable")

        @property
        def admin_tools(self):
            raise RuntimeError("bench config unreadable")

        @property
        def domains(self):
            # `Bench.domains` delegates here, so the vhostd half of the refresh reads the config
            # too: leaving this off would make that read an AttributeError with another message.
            raise RuntimeError("bench config unreadable")

        @property
        def site_names(self):
            # The refresh creates a per-site `custom/<site>/` drop-in directory for each site, so it
            # reads this too. Same reason as `domains` above.
            raise RuntimeError("bench config unreadable")

    _healthy_base(tmp_path)
    bench = _bench(tmp_path, _real_ops(tmp_path))
    bench.bench_config = _UnreadableConfig()

    bench.start()

    assert bench.orchestrator.start_bench.call_count == 1
    warnings = " ".join(str(call.args[0]) for call in bench.output.warning.call_args_list)
    assert "bench config unreadable" in warnings


# ------------------------- every site of a multisite bench, not just the bench's own


def test_the_upload_limit_reaches_every_site_not_only_the_benchs_own(tmp_path):
    """`max_file_size` is per-site data in Frappe, and the refresh used to write one site's file.

    On a bench serving several sites that left every site added later on Frappe's built-in default
    while both nginx layers and `fm info` advertised the bench's limit. Proven on a real two-site
    bench: an upload the bench allowed was accepted by nginx and then refused by the app, and the
    second site's site_config.json had no `max_file_size` key at all.
    """
    _healthy_base(tmp_path)
    (tmp_path / "services" / "nginx-proxy" / "vhostd").mkdir(parents=True)
    sites = tmp_path / "workspace" / "frappe-bench" / "sites"
    for site in (DOMAIN, "second.example.com"):
        (sites / site).mkdir(parents=True)
        (sites / site / "site_config.json").write_text("{}")

    bench = _bench(tmp_path, _real_ops(tmp_path))
    bench.services.path = tmp_path / "services"
    bench.bench_config.site_names = [DOMAIN, "second.example.com"]
    written: dict[str, dict] = {}
    bench.set_bench_site_config = lambda site, values: written.setdefault(site, {}).update(values)

    assert bench.apply_upload_limit() is True

    assert set(written) == {DOMAIN, "second.example.com"}
    assert written["second.example.com"]["max_file_size"] == 50 * 1024 * 1024


def test_a_site_with_the_limit_already_set_is_not_rewritten(tmp_path):
    """Idempotence has to survive per-site: rewriting an unchanged file would report a change and
    reload the shared global proxy on every start of every bench."""
    _healthy_base(tmp_path)
    (tmp_path / "services" / "nginx-proxy" / "vhostd").mkdir(parents=True)
    sites = tmp_path / "workspace" / "frappe-bench" / "sites"
    (sites / DOMAIN).mkdir(parents=True)
    (sites / DOMAIN / "site_config.json").write_text('{"max_file_size": 52428800}')

    bench = _bench(tmp_path, _real_ops(tmp_path))
    bench.services.path = tmp_path / "services"
    bench.bench_config.site_names = [DOMAIN]
    written: list[str] = []
    bench.set_bench_site_config = lambda site, values: written.append(site)

    bench.apply_upload_limit()

    assert written == []
