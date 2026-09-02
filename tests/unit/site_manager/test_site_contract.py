"""Characterization tests for `frappe_manager.site_manager.site.Bench`.

`Bench` is the facade every CLI command drives. It owns almost no algorithm of its
own -- what it owns are *decisions*: which collaborator gets called, in what order,
under which condition, and which action is refused outright. Those decisions are the
contract this file pins, so a later refactor that reshuffles the facade can be proven
behaviour-preserving.

Defended here:
- construction wiring: the workers/admin-tools hooks at the end of `__init__`, the
  `not verbose` flip handed to `BenchWorkers`, the backward-compatible `proxy_manager`
  shim, and the SSL storage config assembled from the *global* proxy dirs plus this
  bench's own webroot;
- `get_object`: bare-name -> `.localhost` promotion, the not-created refusal, the
  defaults that differ from `__init__`, and the conditional `output_handler` kwarg;
- running-state semantics: a service suppressed via the `disabled` compose profile is
  excluded from `running` and from the services status map, and a container that is not
  one of this bench's own is ignored;
- create/start/stop sequencing: exact delegation arguments, and stop's ordered,
  conditional teardown (bench -> workers -> admin tools);
- `generate_compose` -> `ensure_fm_nginx_confs` coupling, and `create_compose_dirs`;
- `ensure_fm_nginx_confs`: what is written, what is removed, what is deliberately left
  alone, when the password is minted, and when nginx is reloaded;
- admin-tools and auth wiring, including `ensure_admin_tools_running_if_available` and
  `sync_bench_config_configuration`'s four-way admin-tools branch;
- the guards that refuse: unknown/idle service logs, missing config files, a bad upload
  limit, and a failed in-container command.

Suspicions found while writing these are pinned as-is (never "fixed") and are called
out in comments beginning with "SUSPICION".
"""

import json
import shutil
from pathlib import Path
from unittest.mock import MagicMock, call, patch

import pytest

from frappe_manager.docker import DockerVolumeMount, DockerVolumeType
from frappe_manager.docker.docker_exceptions import DockerException
from frappe_manager.docker.subprocess_output import SubprocessOutput
from frappe_manager.site_manager.bench_config import (
    AuthConfig,
    BenchConfig,
    BenchRuntime,
    DatabaseConfig,
    FMBenchEnvType,
    SiteConfig,
)
from frappe_manager.site_manager.exceptions import (
    BenchException,
    BenchNotFoundError,
    BenchRemoveDirectoryError,
    BenchServiceNotRunning,
)
from frappe_manager.site_manager.modules.auth import (
    MAP_CONF_NAME,
    SERVER_CONF_NAME,
    build_server_auth_conf,
    container_htpasswd_path,
    htpasswd_name,
)
from frappe_manager.site_manager.modules.realip import build_bench_realip_conf
from frappe_manager.site_manager.site import Bench, SiteSchema

SITE = "test.localhost"
SUBNET = "10.20.0.0/16"

# The bench nginx volumes ProxyStoragePaths reads to build `dirs`.
BENCH_NGINX_MOUNTS = (
    ("configs/nginx/conf", "/etc/nginx"),
    ("configs/nginx/html", "/usr/share/nginx/html"),
    ("configs/nginx/logs", "/var/log/nginx"),
)

# The global nginx-proxy dirs SSLStorageConfig is assembled from.
GLOBAL_PROXY_DIRS = ("ssl", "certs", "vhostd", "html", "conf")


def make_bench_config(root_path: Path, **overrides) -> BenchConfig:
    kwargs = {
        "name": SITE,
        "developer_mode": False,
        "admin_tools": True,
        "environment_type": FMBenchEnvType.dev,
        "root_path": root_path,
        "runtime": BenchRuntime.mount,
    }
    kwargs.update(overrides)
    return BenchConfig(**kwargs)


class BenchHarness:
    """A real `Bench` over tmp_path with mocked docker/services boundaries."""

    def __init__(self, bench, *, path, services, docker_client, compose_file_manager, config_toml):
        self.bench = bench
        self.path = path
        self.services = services
        self.docker_client = docker_client
        self.cfm = compose_file_manager
        self.config_toml = config_toml
        self.conf_dir = path / "configs" / "nginx" / "conf"

    @property
    def output(self):
        return self.bench.output


def build_bench(tmp_path, *, bench_config=None, subnet=SUBNET, neutralise_nginx=True, **bench_kwargs) -> BenchHarness:
    root = tmp_path
    bench_path = root / SITE
    bench_path.mkdir(parents=True, exist_ok=True)
    compose_path = bench_path / "docker-compose.yml"

    def mount(host: Path, container: str) -> DockerVolumeMount:
        return DockerVolumeMount(
            host=str(host), container=container, type=DockerVolumeType.bind, compose_path=compose_path
        )

    cfm = MagicMock(name="compose_file_manager")
    cfm.compose_path = compose_path
    cfm.get_service_volumes.return_value = [mount(bench_path / rel, cont) for rel, cont in BENCH_NGINX_MOUNTS]
    cfm.get_services_list.return_value = ["frappe", "nginx"]
    cfm.get_container_names.return_value = {"frappe": "fm-test-frappe", "nginx": "fm-test-nginx"}

    docker_client = MagicMock(name="docker_client")
    docker_client.compose.get_all_services_status.return_value = []

    services = MagicMock(name="services")
    services.path = root / "services"
    for name in GLOBAL_PROXY_DIRS:
        host = root / "services" / "nginx-proxy" / name
        host.mkdir(parents=True, exist_ok=True)
        getattr(services.proxy_storage.dirs, name).host = host
        getattr(services.proxy_storage.dirs, name).container = Path("/etc/nginx") / name
    # `_frontend_network_subnet` reads the services compose yml first; a real dict keeps
    # the lookup deterministic instead of walking into MagicMock attributes.
    ipam = {"config": [{"subnet": subnet}]} if subnet else {}
    services.compose_file_manager.yml = {"networks": {"global-frontend-network": {"ipam": ipam}}}

    config_toml = bench_path / "bench_config.toml"
    config = bench_config if bench_config is not None else make_bench_config(config_toml)

    kwargs = {
        "path": bench_path,
        "name": SITE,
        "bench_config": config,
        "compose_file_manager": cfm,
        "docker_client": docker_client,
        "services": services,
        "workers_check": False,
        "admin_tools_check": False,
        "verbose": False,
        "output_handler": MagicMock(name="output"),
    }
    kwargs.update(bench_kwargs)
    bench = Bench(**kwargs)

    if neutralise_nginx:
        # Real controller over a fake docker daemon: stub the two calls that would shell out.
        bench.bench_nginx_controller.reload = MagicMock(name="bench_nginx_reload")
        bench.bench_nginx_controller.restart = MagicMock(name="bench_nginx_restart")
    return BenchHarness(
        bench,
        path=bench_path,
        services=services,
        docker_client=docker_client,
        compose_file_manager=cfm,
        config_toml=config_toml,
    )


@pytest.fixture
def harness(tmp_path):
    return build_bench(tmp_path)


def status(service: str, name: str, state: str) -> dict:
    return {"Service": service, "Name": name, "State": state}


def put_site_on_disk(
    bench_path: Path, site: str, *, schema: str | None = None, raw: str | None = None, bench=None
) -> Path:
    """Write `sites/<site>/site_config.json`, which is where a site's schema name actually lives.

    Both halves are needed for a site to be one fm will act on. `[sites]` is the record of WHICH
    sites exist, and `sites/<site>/site_config.json` is the only place each schema name is written.
    Pass `bench` to record it as well as write it; without that the site is on disk and unrecorded,
    which is exactly the hand-made site `unmanaged_site_dirs()` reports and delete never touches.

    `raw` writes the file verbatim, for the unreadable case.
    """
    if bench is not None:
        recorded = getattr(bench.bench_config, "sites", None) or {}
        recorded[site] = MagicMock()
        bench.bench_config.sites = recorded
    site_dir = bench_path / "workspace" / "frappe-bench" / "sites" / site
    site_dir.mkdir(parents=True, exist_ok=True)
    body = raw if raw is not None else json.dumps({"db_name": schema} if schema else {})
    (site_dir / "site_config.json").write_text(body)
    return site_dir


def record_removal_steps(harness) -> list[tuple]:
    """Stub the three destructive steps of `remove_bench` and record the order they run in."""
    bench = harness.bench
    calls: list[tuple] = []
    bench.remove_certificate = MagicMock(side_effect=lambda: calls.append(("cert",)))
    bench.remove_database_and_user = MagicMock(side_effect=lambda site=None: calls.append(("drop", site)))
    bench.remove_containers_and_dirs = MagicMock(side_effect=lambda: calls.append(("dirs",)))
    return calls


# --------------------------------------------------------------------------------------
# Construction wiring
# --------------------------------------------------------------------------------------


class TestConstructionHooks:
    """The two opt-in reconciliation hooks that close `__init__`."""

    def test_workers_check_true_reconciles_workers_during_construction(self, tmp_path):
        with patch.object(Bench, "ensure_workers_running_if_available") as hook:
            build_bench(tmp_path, workers_check=True)
        hook.assert_called_once_with()

    def test_workers_check_false_leaves_workers_untouched(self, tmp_path):
        with patch.object(Bench, "ensure_workers_running_if_available") as hook:
            build_bench(tmp_path, workers_check=False)
        hook.assert_not_called()

    def test_admin_tools_check_true_reconciles_admin_tools_during_construction(self, tmp_path):
        with patch.object(Bench, "ensure_admin_tools_running_if_available") as hook:
            build_bench(tmp_path, admin_tools_check=True)
        hook.assert_called_once_with()

    def test_admin_tools_check_false_leaves_admin_tools_untouched(self, tmp_path):
        with patch.object(Bench, "ensure_admin_tools_running_if_available") as hook:
            build_bench(tmp_path, admin_tools_check=False)
        hook.assert_not_called()

    def test_both_hooks_run_workers_before_admin_tools(self, tmp_path):
        order = []
        with (
            patch.object(Bench, "ensure_workers_running_if_available", side_effect=lambda: order.append("workers")),
            patch.object(Bench, "ensure_admin_tools_running_if_available", side_effect=lambda: order.append("admin")),
        ):
            build_bench(tmp_path, workers_check=True, admin_tools_check=True)
        # Workers first: admin tools reconciliation consults `self.running`, which is
        # only meaningful once the worker containers have been reconciled.
        assert order == ["workers", "admin"]

    def test_bench_verbose_is_inverted_before_reaching_bench_workers(self, tmp_path):
        # SUSPICION: BenchWorkers' second positional parameter is named `verbose`, but
        # Bench passes `not verbose`. Pinned, not fixed -- flipping it would make every
        # non-verbose run chatty (or silence every verbose one).
        with patch("frappe_manager.site_manager.site.BenchWorkers") as workers_cls:
            build_bench(tmp_path, verbose=True)
        assert workers_cls.call_args.args[1] is False

        with patch("frappe_manager.site_manager.site.BenchWorkers") as workers_cls:
            build_bench(tmp_path, verbose=False)
        assert workers_cls.call_args.args[1] is True


class TestConstructionCollaborators:
    """Objects `__init__` synthesises rather than merely forwards."""

    def test_proxy_manager_shim_exposes_bench_nginx_dirs_and_controls(self, tmp_path):
        with patch("frappe_manager.site_manager.site.NginxController") as controller_cls:
            h = build_bench(tmp_path, neutralise_nginx=False)
        controller = controller_cls.return_value
        proxy = h.bench.proxy_manager
        assert proxy.dirs is h.bench.bench_proxy_storage.dirs
        # The shim binds the *controller's* restart/reload, which is what admin tools
        # reaches for; it must be this bench's nginx, never the global proxy.
        assert proxy.restart is controller.restart
        assert proxy.reload is controller.reload
        assert controller_cls.call_args.args[0] == "nginx"

    def test_ssl_storage_mixes_global_proxy_dirs_with_this_benchs_webroot(self, tmp_path):
        with patch("frappe_manager.site_manager.site.SSLStorageConfig") as storage_cls:
            h = build_bench(tmp_path)
        kwargs = storage_cls.call_args.kwargs
        proxy_dirs = h.services.proxy_storage.dirs
        assert kwargs["ssl_dir"] == proxy_dirs.ssl.host
        assert kwargs["certs_dir"] == proxy_dirs.certs.host
        assert kwargs["vhostd_dir"] == proxy_dirs.vhostd.host
        # The ACME webroot is the *bench's* html dir, not the global proxy's: the
        # http-01 challenge is served through this bench's vhost.
        assert kwargs["webroot_dir"] == h.bench.bench_proxy_storage.dirs.html.host
        assert kwargs["webroot_dir"] != proxy_dirs.html.host

    def test_certificate_manager_saves_through_the_bench_not_the_config_object(self, tmp_path):
        with patch("frappe_manager.site_manager.site.SSLCertificateManager") as mgr_cls:
            h = build_bench(tmp_path)
        kwargs = mgr_cls.call_args.kwargs
        assert kwargs["certificates"] is h.bench.bench_config.ssl_certificates
        assert kwargs["nginx_controller"] is h.services.nginx_controller
        # Persisting a renewed cert must go through Bench.save_bench_config so the
        # logging/print contract around config writes is not bypassed.
        assert kwargs["config_save_callback"] == h.bench.save_bench_config

    def test_backup_path_is_derived_from_the_bench_path(self, harness):
        assert harness.bench.backup_path == harness.path / "backups"

    def test_exists_tracks_the_bench_directory(self, harness):
        assert harness.bench.exists is True
        shutil.rmtree(harness.path)
        assert harness.bench.exists is False


# --------------------------------------------------------------------------------------
# get_object
# --------------------------------------------------------------------------------------


@pytest.fixture
def captured_get_object(monkeypatch):
    """Run `Bench.get_object` for real, capturing the kwargs it would construct with."""
    captured: dict = {}

    def fake_init(self, **kwargs):
        captured.clear()
        captured.update(kwargs)

    monkeypatch.setattr(Bench, "__init__", fake_init)
    monkeypatch.setattr("frappe_manager.site_manager.site.ComposeFile", MagicMock())
    monkeypatch.setattr("frappe_manager.site_manager.site.DockerClient", MagicMock())
    monkeypatch.setattr("frappe_manager.site_manager.site.BenchConfig", MagicMock())
    return captured


class TestGetObject:
    def test_bare_name_is_promoted_to_a_localhost_domain(self, tmp_path, captured_get_object):
        (tmp_path / "mybench.localhost").mkdir()
        Bench.get_object("mybench", MagicMock(), benches_path=tmp_path)
        assert captured_get_object["name"] == "mybench.localhost"
        assert captured_get_object["path"] == tmp_path / "mybench.localhost"

    def test_an_already_qualified_name_is_left_alone(self, tmp_path, captured_get_object):
        (tmp_path / "shop.example.com").mkdir()
        Bench.get_object("shop.example.com", MagicMock(), benches_path=tmp_path)
        assert captured_get_object["name"] == "shop.example.com"

    def test_a_bench_that_was_never_created_is_refused(self, tmp_path, captured_get_object):
        with pytest.raises(BenchNotFoundError):
            Bench.get_object("ghost", MagicMock(), benches_path=tmp_path)
        assert captured_get_object == {}

    def test_reconciliation_hooks_default_off_unlike_the_constructor(self, tmp_path, captured_get_object):
        (tmp_path / "mybench.localhost").mkdir()
        Bench.get_object("mybench", MagicMock(), benches_path=tmp_path)
        # `Bench.__init__` defaults both to True; `get_object` deliberately does not,
        # so merely looking a bench up never starts containers.
        assert captured_get_object["workers_check"] is False
        assert captured_get_object["admin_tools_check"] is False

    def test_output_handler_kwarg_is_omitted_when_none_so_the_default_applies(self, tmp_path, captured_get_object):
        (tmp_path / "mybench.localhost").mkdir()
        Bench.get_object("mybench", MagicMock(), benches_path=tmp_path)
        assert "output_handler" not in captured_get_object

    def test_output_handler_kwarg_is_forwarded_when_supplied(self, tmp_path, captured_get_object):
        (tmp_path / "mybench.localhost").mkdir()
        handler = MagicMock()
        Bench.get_object("mybench", MagicMock(), benches_path=tmp_path, output_handler=handler)
        assert captured_get_object["output_handler"] is handler

    def test_config_is_read_from_the_named_file_inside_the_bench(self, tmp_path, monkeypatch):
        (tmp_path / "mybench.localhost").mkdir()
        monkeypatch.setattr(Bench, "__init__", lambda self, **kwargs: None)
        monkeypatch.setattr("frappe_manager.site_manager.site.ComposeFile", MagicMock())
        monkeypatch.setattr("frappe_manager.site_manager.site.DockerClient", MagicMock())
        config_cls = MagicMock()
        monkeypatch.setattr("frappe_manager.site_manager.site.BenchConfig", config_cls)

        Bench.get_object("mybench", MagicMock(), benches_path=tmp_path, bench_config_file_name="other.toml")

        config_cls.import_from_toml.assert_called_once_with(tmp_path / "mybench.localhost" / "other.toml")

    def test_lookup_tags_the_ambient_logging_context_with_the_resolved_name(self, tmp_path, captured_get_object):
        from frappe_manager.logger import current_context, reset_context

        (tmp_path / "mybench.localhost").mkdir()
        reset_context()
        try:
            Bench.get_object("mybench", MagicMock(), benches_path=tmp_path)
            # The promoted name, not the bare one the caller typed.
            assert current_context().bench == "mybench.localhost"
        finally:
            reset_context()


# --------------------------------------------------------------------------------------
# Running state
# --------------------------------------------------------------------------------------


class TestRunningState:
    """`running` drives every "is this bench up?" guard in the CLI."""

    def _with_profiles(self, harness, *, enabled, all_services):
        def services_list(exclude_disabled=False):
            return list(enabled) if exclude_disabled else list(all_services)

        harness.cfm.get_services_list.side_effect = services_list

    def test_all_services_running_reports_running(self, harness):
        harness.docker_client.compose.get_all_services_status.return_value = [
            status("frappe", "fm-test-frappe", "running"),
            status("nginx", "fm-test-nginx", "running"),
        ]
        assert harness.bench.running is True

    def test_one_stopped_service_reports_not_running(self, harness):
        harness.docker_client.compose.get_all_services_status.return_value = [
            status("frappe", "fm-test-frappe", "running"),
            status("nginx", "fm-test-nginx", "exited"),
        ]
        assert harness.bench.running is False

    def test_a_service_suppressed_by_the_disabled_profile_does_not_break_running(self, harness):
        # A bench on an external redis never starts redis-cache; counting it would
        # report a perfectly healthy bench as broken.
        self._with_profiles(harness, enabled=["frappe", "nginx"], all_services=["frappe", "nginx", "redis-cache"])
        harness.cfm.get_container_names.return_value = {
            "frappe": "fm-test-frappe",
            "nginx": "fm-test-nginx",
            "redis-cache": "fm-test-redis-cache",
        }
        harness.docker_client.compose.get_all_services_status.return_value = [
            status("frappe", "fm-test-frappe", "running"),
            status("nginx", "fm-test-nginx", "running"),
        ]
        assert harness.bench.running is True
        assert harness.cfm.get_services_list.call_args.kwargs == {"exclude_disabled": True}

    def test_a_container_from_another_project_is_ignored(self, harness):
        # Statuses are filtered by this bench's own container names first, so a
        # same-named service belonging to a different compose project cannot vouch
        # for -- or condemn -- this bench.
        harness.docker_client.compose.get_all_services_status.return_value = [
            status("frappe", "someone-elses-frappe", "running"),
            status("nginx", "fm-test-nginx", "running"),
        ]
        assert harness.bench.running is False

    def test_docker_failure_is_reported_as_not_running(self, harness):
        harness.docker_client.compose.get_all_services_status.side_effect = DockerException(["docker"], MagicMock())
        assert harness.bench.running is False

    def test_is_service_running_matches_on_service_name_and_state(self, harness):
        harness.docker_client.compose.get_all_services_status.return_value = [
            status("frappe", "fm-test-frappe", "running"),
            status("nginx", "fm-test-nginx", "exited"),
        ]
        assert harness.bench._is_service_running("frappe") is True
        assert harness.bench._is_service_running("nginx") is False
        assert harness.bench._is_service_running("socketio") is False

    def test_services_status_map_omits_suppressed_services(self, harness):
        self._with_profiles(harness, enabled=["frappe", "nginx"], all_services=["frappe", "nginx", "redis-cache"])
        harness.cfm.get_container_names.return_value = {
            "frappe": "fm-test-frappe",
            "nginx": "fm-test-nginx",
            "redis-cache": "fm-test-redis-cache",
        }
        harness.docker_client.compose.get_all_services_status.return_value = [
            status("frappe", "fm-test-frappe", "running"),
            status("nginx", "fm-test-nginx", "running"),
            # A leftover container from before redis was disabled.
            status("redis-cache", "fm-test-redis-cache", "exited"),
        ]
        assert harness.bench._get_services_running_status() == {"frappe": "running", "nginx": "running"}


# --------------------------------------------------------------------------------------
# create / start / stop sequencing
# --------------------------------------------------------------------------------------


class TestLifecycleSequencing:
    def test_create_delegates_the_bench_only_flag_to_the_orchestrator(self, harness):
        harness.bench.orchestrator = MagicMock()
        harness.bench.create(bench_only=True)
        harness.bench.orchestrator.create_bench.assert_called_once_with(True)

    def test_create_defaults_to_a_full_bench(self, harness):
        harness.bench.orchestrator = MagicMock()
        harness.bench.create()
        harness.bench.orchestrator.create_bench.assert_called_once_with(False)

    def test_create_failure_propagates_rather_than_being_swallowed(self, harness):
        harness.bench.orchestrator = MagicMock()
        harness.bench.orchestrator.create_bench.side_effect = RuntimeError("boom")
        with pytest.raises(RuntimeError, match="boom"):
            harness.bench.create()

    def test_start_forwards_every_switch_by_keyword(self, harness):
        # docker_ops is stubbed so any pre-start repair step stays inert; this test is
        # about which switches reach the orchestrator, nothing else.
        harness.bench.docker_ops = MagicMock()
        harness.bench.orchestrator = MagicMock()
        harness.bench.start(
            force=True,
            reconfigure_workers=True,
            include_default_workers=True,
            include_custom_workers=True,
            reconfigure_supervisor=True,
            reconfigure_common_site_config=True,
            sync_dev_packages=True,
        )
        harness.bench.orchestrator.start_bench.assert_called_once_with(
            force=True,
            reconfigure_workers=True,
            include_default_workers=True,
            include_custom_workers=True,
            reconfigure_supervisor=True,
            reconfigure_common_site_config=True,
            sync_dev_packages=True,
        )

    def test_start_defaults_are_all_off(self, harness):
        harness.bench.docker_ops = MagicMock()
        harness.bench.orchestrator = MagicMock()
        harness.bench.start()
        assert harness.bench.orchestrator.start_bench.call_args.kwargs == {
            "force": False,
            "reconfigure_workers": False,
            "include_default_workers": False,
            "include_custom_workers": False,
            "reconfigure_supervisor": False,
            "reconfigure_common_site_config": False,
            "sync_dev_packages": False,
        }

    def test_start_failure_propagates(self, harness):
        harness.bench.docker_ops = MagicMock()
        harness.bench.orchestrator = MagicMock()
        harness.bench.orchestrator.start_bench.side_effect = RuntimeError("nope")
        with pytest.raises(RuntimeError, match="nope"):
            harness.bench.start()

    def _stoppable(self, harness, *, workers=True, admin=True):
        bench = harness.bench
        bench.docker_ops = MagicMock(name="docker_ops")
        bench.workers = MagicMock(name="workers")
        bench.workers.compose_file_manager.exists.return_value = workers
        bench.admin_tools = MagicMock(name="admin_tools")
        bench.admin_tools.compose_file_manager.exists.return_value = admin
        return bench

    def test_stop_takes_the_bench_down_before_workers_and_admin_tools(self, harness):
        bench = self._stoppable(harness)
        order = []
        bench.docker_ops.stop.side_effect = lambda **kw: order.append("bench")
        bench.workers.docker_client.compose.stop.side_effect = lambda **kw: order.append("workers")
        bench.admin_tools.stop.side_effect = lambda: order.append("admin")

        bench.stop()

        assert order == ["bench", "workers", "admin"]
        bench.docker_ops.stop.assert_called_once_with(timeout=10)
        bench.workers.docker_client.compose.stop.assert_called_once_with(services=[], timeout=10)
        bench.admin_tools.stop.assert_called_once_with()

    def test_stop_skips_workers_when_their_compose_file_is_absent(self, harness):
        bench = self._stoppable(harness, workers=False)
        bench.stop()
        bench.workers.docker_client.compose.stop.assert_not_called()
        bench.admin_tools.stop.assert_called_once_with()

    def test_stop_skips_admin_tools_when_their_compose_file_is_absent(self, harness):
        bench = self._stoppable(harness, admin=False)
        bench.stop()
        bench.admin_tools.stop.assert_not_called()
        bench.workers.docker_client.compose.stop.assert_called_once()

    def test_stop_aborts_when_the_bench_itself_will_not_stop(self, harness):
        bench = self._stoppable(harness)
        bench.docker_ops.stop.side_effect = RuntimeError("stuck")
        with pytest.raises(RuntimeError, match="stuck"):
            bench.stop()
        # Workers are never stopped behind a bench that failed to stop.
        bench.workers.docker_client.compose.stop.assert_not_called()
        bench.admin_tools.stop.assert_not_called()


class TestComposeGeneration:
    def test_generate_compose_refreshes_the_fm_nginx_confs_afterwards(self, harness):
        bench = harness.bench
        bench.docker_ops = MagicMock()
        order = []
        bench.docker_ops.generate_compose.side_effect = lambda inputs: order.append("compose")
        with patch.object(Bench, "ensure_fm_nginx_confs", side_effect=lambda: order.append("confs")):
            bench.generate_compose({"environment": {}})
        # The confs depend on the topology compose generation just materialised
        # (the frontend subnet), so they must be rewritten after, never before.
        assert order == ["compose", "confs"]
        bench.docker_ops.generate_compose.assert_called_once_with({"environment": {}})

    def test_create_compose_dirs_forwards_copy_runtimes_and_returns_its_verdict(self, harness):
        bench = harness.bench
        bench.docker_ops = MagicMock()
        bench.docker_ops.create_compose_dirs.return_value = True
        assert bench.create_compose_dirs() is True
        bench.docker_ops.create_compose_dirs.assert_called_once_with(copy_runtimes=True)

        bench.docker_ops.create_compose_dirs.reset_mock()
        bench.docker_ops.create_compose_dirs.return_value = False
        assert bench.create_compose_dirs(copy_runtimes=False) is False
        bench.docker_ops.create_compose_dirs.assert_called_once_with(copy_runtimes=False)

    def test_sync_bench_common_site_config_delegates_to_the_database_module(self, harness):
        harness.bench.database = MagicMock()
        harness.bench.sync_bench_common_site_config()
        harness.bench.database.sync_common_site_config.assert_called_once_with()


# --------------------------------------------------------------------------------------
# ensure_fm_nginx_confs
# --------------------------------------------------------------------------------------


class TestEnsureFmNginxConfs:
    """The fm-owned bench nginx confs: written, removed, or deliberately preserved."""

    def test_real_ip_conf_is_written_from_the_detected_frontend_subnet(self, harness):
        harness.bench.ensure_fm_nginx_confs()
        conf = harness.conf_dir / "custom" / "real-ip.conf"
        assert conf.read_text() == build_bench_realip_conf(SUBNET)
        harness.bench.bench_nginx_controller.reload.assert_called_once_with()

    def test_no_detectable_subnet_writes_no_real_ip_conf(self, tmp_path):
        h = build_bench(tmp_path, subnet=None)
        with patch("frappe_manager.utils.network.detect_running_network", return_value=None):
            h.bench.ensure_fm_nginx_confs()
        assert not (h.conf_dir / "custom" / "real-ip.conf").exists()

    def test_an_existing_real_ip_conf_survives_an_undetectable_subnet(self, tmp_path):
        bench_path = tmp_path / SITE
        bench_path.mkdir(parents=True, exist_ok=True)
        config = make_bench_config(bench_path / "bench_config.toml", auth=AuthConfig(web=False, tools=False))
        h = build_bench(tmp_path, subnet=None, bench_config=config)
        conf = h.conf_dir / "custom" / "real-ip.conf"
        conf.parent.mkdir(parents=True, exist_ok=True)
        conf.write_text(build_bench_realip_conf("192.168.0.0/16"))
        # Already correct on disk, so this pass has nothing to write. Without it the upload conf
        # would be the change that triggers the reload, and the assertion below would say nothing
        # about the subnet.
        (h.conf_dir / "custom" / "upload-limit.conf").write_text(
            f"client_max_body_size {h.bench.bench_config.upload_limit.lower()};\n"
        )
        with patch("frappe_manager.utils.network.detect_running_network", return_value=None):
            h.bench.ensure_fm_nginx_confs()
        # real-ip.conf is never removed: losing it silently would make every request
        # look like it came from the proxy. Nothing else changed either, so no reload.
        assert conf.read_text() == build_bench_realip_conf("192.168.0.0/16")
        h.bench.bench_nginx_controller.reload.assert_not_called()

    def test_the_upload_limit_conf_is_written_from_the_config(self, harness):
        """The bug: nothing wrote this file at create, so a bench advertised its configured
        upload_limit while nginx enforced its own 1M default and refused larger uploads with a 413.
        It is an fm-managed conf like the others, so it is written from bench_config and nowhere
        else -- there is no argument to forget to pass."""
        harness.bench.ensure_fm_nginx_confs()
        conf = harness.conf_dir / "custom" / "upload-limit.conf"
        assert conf.read_text() == f"client_max_body_size {harness.bench.bench_config.upload_limit.lower()};\n"

    def test_the_upload_limit_conf_tracks_a_changed_config(self, tmp_path):
        bench_path = tmp_path / SITE
        bench_path.mkdir(parents=True, exist_ok=True)
        config = make_bench_config(bench_path / "bench_config.toml", auth=AuthConfig(web=False, tools=False))
        config.upload_limit = "512M"
        h = build_bench(tmp_path, bench_config=config)
        h.bench.ensure_fm_nginx_confs()
        assert (h.conf_dir / "custom" / "upload-limit.conf").read_text() == "client_max_body_size 512m;\n"

    def test_nginx_wants_the_limit_lowercased(self, tmp_path):
        """`50M` is what the config carries and what fm prints; nginx wants `50m`."""
        bench_path = tmp_path / SITE
        bench_path.mkdir(parents=True, exist_ok=True)
        config = make_bench_config(bench_path / "bench_config.toml", auth=AuthConfig(web=False, tools=False))
        config.upload_limit = "1G"
        h = build_bench(tmp_path, bench_config=config)
        h.bench.ensure_fm_nginx_confs()
        assert "1g;" in (h.conf_dir / "custom" / "upload-limit.conf").read_text()

    def test_a_second_pass_that_changes_nothing_does_not_reload_nginx(self, harness):
        harness.bench.ensure_fm_nginx_confs()
        harness.bench.bench_nginx_controller.reload.reset_mock()
        harness.bench.ensure_fm_nginx_confs()
        harness.bench.bench_nginx_controller.reload.assert_not_called()

    def test_a_reload_failure_on_a_configured_nginx_is_reported(self, harness):
        """The confs are already written when the reload runs, so they stay. What changed is that the
        failure is no longer swallowed by a bare `except: pass`: `fm auth --protect web` used to
        print the surface as protected and exit 0 while nginx kept serving it UNAUTHENTICATED.

        The premise of that refusal is that nginx HAS a config, so it is still serving the old one.
        `nginx.conf` on disk is what says so, because `configs/nginx/conf` is bind-mounted at
        `/etc/nginx`."""
        harness.conf_dir.mkdir(parents=True, exist_ok=True)
        (harness.conf_dir / "nginx.conf").write_text("worker_processes 1;\n")
        harness.bench.bench_nginx_controller.reload.side_effect = RuntimeError("rejected: invalid directive")

        with pytest.raises(BenchException, match="nginx rejected the updated configuration"):
            harness.bench.ensure_fm_nginx_confs()

        assert (harness.conf_dir / "custom" / "real-ip.conf").exists()

    def test_a_reload_failure_with_no_nginx_conf_at_all_is_not_fatal(self, harness):
        """A bench being created has an empty `configs/nginx/conf`, so its nginx container can be up
        with NO configuration: `/etc/nginx/nginx.conf` does not exist inside it and every reload
        exits non-zero. Nothing is being served, so there is nothing stale to warn about, and the
        confs apply when the container next starts.

        This aborted every `fm create` whose nginx container came up before the confs were written,
        with "nginx rejected the updated configuration and is still serving the previous one", and
        then offered to roll the new bench back."""
        assert not (harness.conf_dir / "nginx.conf").exists()
        harness.bench.bench_nginx_controller.reload.side_effect = RuntimeError(
            'open() "/etc/nginx/nginx.conf" failed (2: No such file or directory)'
        )

        harness.bench.ensure_fm_nginx_confs()  # no raise

        assert (harness.conf_dir / "custom" / "real-ip.conf").exists()

    def _auth_bench(self, tmp_path, **auth_kwargs):
        bench_path = tmp_path / SITE
        bench_path.mkdir(parents=True, exist_ok=True)
        config = make_bench_config(
            bench_path / "bench_config.toml",
            auth=AuthConfig(**auth_kwargs),
            admin_tools=auth_kwargs.pop("_admin_tools", True),
        )
        return build_bench(tmp_path, bench_config=config)

    def test_web_auth_writes_the_server_conf_and_the_shared_htpasswd(self, tmp_path):
        h = self._auth_bench(tmp_path, web=True, password="s3cret")
        h.bench.ensure_fm_nginx_confs()
        server_conf = h.conf_dir / "custom" / SERVER_CONF_NAME
        assert server_conf.read_text() == build_server_auth_conf(container_htpasswd_path(SITE), [], [])
        assert (h.conf_dir / "http_auth" / htpasswd_name(SITE)).exists()

    def test_allow_paths_additionally_writes_the_realm_map(self, tmp_path):
        h = self._auth_bench(tmp_path, web=True, password="s3cret", allow_paths=["/api/method/ping"])
        h.bench.ensure_fm_nginx_confs()
        map_conf = h.conf_dir / "conf.d" / MAP_CONF_NAME
        assert "/api/method/ping" in map_conf.read_text()

    def test_both_exemptions_move_the_ip_allow_list_into_the_realm_map(self, tmp_path):
        # D23: the server conf must not carry `deny all` alongside the variable realm
        # (the access module would 403 the exempt path), so the IP exemption has to
        # arrive in the map file instead -- which only happens if allow_ips is
        # actually handed to build_auth_map_conf.
        h = self._auth_bench(
            tmp_path,
            web=True,
            password="s3cret",
            allow_ips=["203.0.113.0/24"],
            allow_paths=["/api/method/payment_webhook"],
        )
        h.bench.ensure_fm_nginx_confs()
        assert "deny all;" not in (h.conf_dir / "custom" / SERVER_CONF_NAME).read_text()
        map_text = (h.conf_dir / "conf.d" / MAP_CONF_NAME).read_text()
        assert "geo $fm_auth_ip_exempt {" in map_text
        assert "    203.0.113.0/24 1;" in map_text
        assert "    ~^/api/method/payment_webhook 1;" in map_text

    def test_no_allow_paths_writes_no_realm_map(self, tmp_path):
        h = self._auth_bench(tmp_path, web=True, password="s3cret")
        h.bench.ensure_fm_nginx_confs()
        assert not (h.conf_dir / "conf.d" / MAP_CONF_NAME).exists()

    def test_tools_only_auth_mints_the_htpasswd_but_gates_no_server_context(self, tmp_path):
        h = self._auth_bench(tmp_path, web=False, tools=True, password="s3cret")
        h.bench.ensure_fm_nginx_confs()
        # The tools surface carries its own directives inside admin-tools.conf; a
        # server-context include would gate the whole site.
        assert not (h.conf_dir / "custom" / SERVER_CONF_NAME).exists()
        assert (h.conf_dir / "http_auth" / htpasswd_name(SITE)).exists()

    def test_tools_auth_needs_admin_tools_enabled_to_mint_credentials(self, tmp_path):
        bench_path = tmp_path / SITE
        bench_path.mkdir(parents=True, exist_ok=True)
        config = make_bench_config(
            bench_path / "bench_config.toml",
            admin_tools=False,
            auth=AuthConfig(web=False, tools=True, password="s3cret"),
        )
        h = build_bench(tmp_path, bench_config=config)
        h.bench.ensure_fm_nginx_confs()
        # Nothing serves the tools paths, so there is no surface to protect.
        assert not (h.conf_dir / "http_auth" / htpasswd_name(SITE)).exists()

    def test_a_missing_password_is_minted_once_and_persisted(self, tmp_path):
        h = self._auth_bench(tmp_path, web=True)
        assert h.bench.bench_config.auth.password is None
        with patch.object(Bench, "save_bench_config") as save:
            h.bench.ensure_fm_nginx_confs()
        minted = h.bench.bench_config.auth.password
        assert minted
        # Saved silently: this is a side effect of another command, not a config edit
        # the user asked for.
        save.assert_called_once_with(print_message=False)

        with patch.object(Bench, "save_bench_config") as save_again:
            h.bench.ensure_fm_nginx_confs()
        assert h.bench.bench_config.auth.password == minted
        save_again.assert_not_called()

    def test_no_auth_configured_mints_nothing(self, tmp_path):
        bench_path = tmp_path / SITE
        bench_path.mkdir(parents=True, exist_ok=True)
        config = make_bench_config(bench_path / "bench_config.toml", auth=AuthConfig(web=False, tools=False))
        h = build_bench(tmp_path, bench_config=config)
        with patch.object(Bench, "save_bench_config") as save:
            h.bench.ensure_fm_nginx_confs()
        assert h.bench.bench_config.auth.password is None
        save.assert_not_called()

    def test_disabling_auth_removes_the_htpasswd_and_the_fm_written_confs(self, tmp_path):
        enabled = self._auth_bench(tmp_path, web=True, password="s3cret", allow_paths=["/ping"])
        enabled.bench.ensure_fm_nginx_confs()
        htpasswd = enabled.conf_dir / "http_auth" / htpasswd_name(SITE)
        server_conf = enabled.conf_dir / "custom" / SERVER_CONF_NAME
        map_conf = enabled.conf_dir / "conf.d" / MAP_CONF_NAME
        assert htpasswd.exists()
        assert server_conf.exists()
        assert map_conf.exists()

        enabled.bench.bench_config.auth = AuthConfig(web=False, tools=False)
        enabled.bench.bench_nginx_controller.reload.reset_mock()
        enabled.bench.ensure_fm_nginx_confs()

        assert not htpasswd.exists()
        assert not server_conf.exists()
        assert not map_conf.exists()
        enabled.bench.bench_nginx_controller.reload.assert_called_once_with()

    def test_a_hand_written_conf_at_an_fm_path_is_never_deleted(self, tmp_path):
        h = self._auth_bench(tmp_path, web=False, tools=False)
        server_conf = h.conf_dir / "custom" / SERVER_CONF_NAME
        server_conf.parent.mkdir(parents=True, exist_ok=True)
        server_conf.write_text("auth_basic off;  # mine, not fm's\n")

        h.bench.ensure_fm_nginx_confs()

        # Only files carrying fm's marker are fm's to remove.
        assert server_conf.read_text() == "auth_basic off;  # mine, not fm's\n"

    def test_a_stale_fm_conf_is_rewritten_when_its_content_drifts(self, tmp_path):
        h = self._auth_bench(tmp_path, web=True, password="s3cret")
        server_conf = h.conf_dir / "custom" / SERVER_CONF_NAME
        server_conf.parent.mkdir(parents=True, exist_ok=True)
        server_conf.write_text("# fm:auth stale\n")

        h.bench.ensure_fm_nginx_confs()

        assert server_conf.read_text() == build_server_auth_conf(container_htpasswd_path(SITE), [], [])

    def test_admin_tools_conf_is_refreshed_when_admin_tools_are_enabled(self, harness):
        conf = harness.conf_dir / "custom" / "admin-tools.conf"
        conf.parent.mkdir(parents=True, exist_ok=True)
        conf.write_text("old\n")
        harness.bench.admin_tools.save_nginx_location_config = MagicMock(side_effect=lambda: conf.write_text("new\n"))

        harness.bench.ensure_fm_nginx_confs()

        harness.bench.admin_tools.save_nginx_location_config.assert_called_once_with()
        assert conf.read_text() == "new\n"

    def test_admin_tools_conf_is_left_alone_when_admin_tools_are_disabled(self, tmp_path):
        bench_path = tmp_path / SITE
        bench_path.mkdir(parents=True, exist_ok=True)
        config = make_bench_config(bench_path / "bench_config.toml", admin_tools=False)
        h = build_bench(tmp_path, bench_config=config)
        conf = h.conf_dir / "custom" / "admin-tools.conf"
        conf.parent.mkdir(parents=True, exist_ok=True)
        conf.write_text("old\n")
        h.bench.admin_tools.save_nginx_location_config = MagicMock()

        h.bench.ensure_fm_nginx_confs()

        h.bench.admin_tools.save_nginx_location_config.assert_not_called()

    def test_a_failing_admin_tools_refresh_does_not_abort_the_rest(self, harness):
        conf = harness.conf_dir / "custom" / "admin-tools.conf"
        conf.parent.mkdir(parents=True, exist_ok=True)
        conf.write_text("old\n")
        harness.bench.admin_tools.save_nginx_location_config = MagicMock(side_effect=RuntimeError("template blew up"))

        harness.bench.ensure_fm_nginx_confs()

        assert (harness.conf_dir / "custom" / "real-ip.conf").exists()


class TestFrontendSubnetDetection:
    def test_the_pinned_services_compose_is_preferred_over_a_live_inspect(self, harness):
        with patch("frappe_manager.utils.network.detect_running_network") as detect:
            assert harness.bench._frontend_network_subnet() == SUBNET
        detect.assert_not_called()

    def test_a_services_compose_without_ipam_falls_back_to_inspecting_the_network(self, tmp_path):
        h = build_bench(tmp_path, subnet=None)
        with patch(
            "frappe_manager.utils.network.detect_running_network", return_value={"subnet_cidr": "172.30.0.0/16"}
        ):
            assert h.bench._frontend_network_subnet() == "172.30.0.0/16"

    def test_both_sources_failing_yields_no_subnet(self, tmp_path):
        h = build_bench(tmp_path, subnet=None)
        with patch("frappe_manager.utils.network.detect_running_network", side_effect=RuntimeError("no daemon")):
            assert h.bench._frontend_network_subnet() is None


# --------------------------------------------------------------------------------------
# Admin tools + config sync
# --------------------------------------------------------------------------------------


class TestEnsureAdminToolsRunningIfAvailable:
    def _admin(self, harness, *, compose_exists=True, services_list=None, statuses=None, bench_running=True):
        bench = harness.bench
        bench.docker_ops = MagicMock()
        bench.docker_ops.is_running.return_value = bench_running
        bench.admin_tools = MagicMock(name="admin_tools")
        bench.admin_tools.compose_file_manager.exists.return_value = compose_exists
        bench.admin_tools.compose_file_manager.get_services_list.return_value = services_list or ["adminer", "mailpit"]
        bench.admin_tools.compose_file_manager.get_container_names.return_value = {
            "adminer": "fm-test-adminer",
            "mailpit": "fm-test-mailpit",
        }
        bench.admin_tools.docker_client.compose.get_all_services_status.return_value = statuses or []
        return bench

    def test_nothing_happens_without_an_admin_tools_compose_file(self, harness):
        bench = self._admin(harness, compose_exists=False)
        bench.ensure_admin_tools_running_if_available()
        bench.admin_tools.enable.assert_not_called()
        bench.admin_tools.disable.assert_not_called()

    def test_wanted_and_fully_running_needs_no_action(self, harness):
        bench = self._admin(
            harness,
            statuses=[
                status("adminer", "fm-test-adminer", "running"),
                status("mailpit", "fm-test-mailpit", "running"),
            ],
        )
        bench.ensure_admin_tools_running_if_available()
        bench.admin_tools.enable.assert_not_called()

    def test_wanted_but_partly_down_is_enabled_when_the_bench_is_up(self, harness):
        bench = self._admin(
            harness,
            statuses=[
                status("adminer", "fm-test-adminer", "running"),
                status("mailpit", "fm-test-mailpit", "exited"),
            ],
        )
        bench.ensure_admin_tools_running_if_available()
        bench.admin_tools.enable.assert_called_once_with()

    def test_wanted_but_partly_down_is_left_alone_when_the_bench_is_down(self, harness):
        bench = self._admin(
            harness,
            statuses=[status("adminer", "fm-test-adminer", "exited")],
            bench_running=False,
        )
        bench.ensure_admin_tools_running_if_available()
        # Starting admin tools alongside a stopped bench would leave them pointing at
        # nothing; the guard refuses.
        bench.admin_tools.enable.assert_not_called()

    def test_a_docker_error_while_probing_is_treated_as_not_running(self, harness):
        bench = self._admin(harness)
        bench.admin_tools.docker_client.compose.get_all_services_status.side_effect = RuntimeError("daemon gone")
        bench.ensure_admin_tools_running_if_available()
        bench.admin_tools.enable.assert_called_once_with()

    def test_unwanted_admin_tools_are_not_disabled_even_while_running(self, harness):
        # SUSPICION: the "disable leftovers" branch iterates the *keys* of the service
        # status map and compares each service name to the literal "running", so a
        # normally named service can never satisfy it and disable() is effectively
        # dead. Pinned as-is; the fix belongs in a behaviour change, not a test.
        bench = self._admin(harness)
        bench.bench_config.admin_tools = False
        bench.admin_tools.docker_client.compose.get_all_services_status.return_value = [
            status("adminer", "fm-test-adminer", "running"),
            status("mailpit", "fm-test-mailpit", "running"),
        ]
        bench.ensure_admin_tools_running_if_available()
        bench.admin_tools.disable.assert_not_called()

    def test_a_service_literally_named_running_is_what_triggers_the_disable(self, harness):
        # The other half of the same suspicion, pinned so the dead branch's real
        # trigger condition is documented rather than guessed at.
        bench = self._admin(harness, services_list=["running"])
        bench.bench_config.admin_tools = False
        bench.admin_tools.compose_file_manager.get_container_names.return_value = {"running": "fm-test-running"}
        bench.admin_tools.docker_client.compose.get_all_services_status.return_value = [
            status("running", "fm-test-running", "exited"),
        ]
        bench.ensure_admin_tools_running_if_available()
        bench.admin_tools.disable.assert_called_once_with()


class TestSyncBenchConfigConfiguration:
    def _syncable(self, harness, *, admin_tools=True, compose_exists=True):
        bench = harness.bench
        bench.bench_config.admin_tools = admin_tools
        bench.set_common_bench_config = MagicMock()
        bench.update_certificate = MagicMock(return_value=False)
        bench.restart_supervisor_service = MagicMock()
        bench.sync_admin_tools_compose = MagicMock()
        bench.admin_tools = MagicMock(name="admin_tools")
        bench.admin_tools.compose_file_manager.compose_path.exists.return_value = compose_exists
        return bench

    def test_developer_mode_is_pushed_into_the_common_site_config(self, harness):
        bench = self._syncable(harness)
        bench.bench_config.developer_mode = True
        bench.sync_bench_config_configuration()
        bench.set_common_bench_config.assert_called_once_with({"developer_mode": True})

    def test_certificate_sync_never_raises_out_of_the_config_sync(self, harness):
        bench = self._syncable(harness)
        bench.sync_bench_config_configuration()
        assert bench.update_certificate.call_args.kwargs == {"raise_error": False}

    def test_admin_tools_wanted_without_a_compose_file_generates_one(self, harness):
        bench = self._syncable(harness, admin_tools=True, compose_exists=False)
        bench.sync_bench_config_configuration()
        bench.sync_admin_tools_compose.assert_called_once_with()
        bench.admin_tools.enable.assert_not_called()

    def test_admin_tools_wanted_with_a_compose_file_is_reconfigured_in_place(self, harness):
        bench = self._syncable(harness, admin_tools=True, compose_exists=True)
        bench.sync_bench_config_configuration()
        bench.admin_tools.enable.assert_called_once_with(force_configure=True)
        bench.sync_admin_tools_compose.assert_not_called()

    def test_admin_tools_unwanted_without_a_compose_file_is_a_no_op(self, harness):
        bench = self._syncable(harness, admin_tools=False, compose_exists=False)
        bench.sync_bench_config_configuration()
        bench.admin_tools.disable.assert_not_called()
        bench.admin_tools.enable.assert_not_called()

    def test_admin_tools_unwanted_with_a_compose_file_is_disabled(self, harness):
        bench = self._syncable(harness, admin_tools=False, compose_exists=True)
        bench.sync_bench_config_configuration()
        bench.admin_tools.disable.assert_called_once_with()

    def test_the_frappe_server_is_restarted_last(self, harness):
        bench = self._syncable(harness)
        order = []
        bench.set_common_bench_config.side_effect = lambda cfg: order.append("common")
        bench.admin_tools.enable.side_effect = lambda **kw: order.append("admin")
        bench.restart_supervisor_service.side_effect = lambda svc: order.append(f"restart:{svc}")
        bench.sync_bench_config_configuration()
        assert order == ["common", "admin", "restart:frappe"]


class TestSyncAdminToolsCompose:
    def test_compose_is_generated_then_enabled_with_a_forced_recreate(self, harness):
        bench = harness.bench
        bench.admin_tools = MagicMock()
        bench.admin_tools.enable.return_value = True
        order = []
        bench.admin_tools.generate_compose.side_effect = lambda: order.append("generate")
        bench.admin_tools.enable.side_effect = lambda **kw: order.append("enable") or True

        assert bench.sync_admin_tools_compose() is True
        assert order == ["generate", "enable"]
        bench.admin_tools.enable.assert_called_once_with(force_recreate_container=True)


# --------------------------------------------------------------------------------------
# Guards and refusals
# --------------------------------------------------------------------------------------


class TestGuards:
    def test_logs_for_a_stopped_service_are_refused_without_touching_docker(self, harness):
        """Raises rather than printing and returning: this exited 0 after saying it could show
        nothing, while `fm shell` exits 1 for the identical condition."""
        bench = harness.bench
        bench.docker_ops = MagicMock()
        bench.docker_ops._is_service_running.return_value = False

        with pytest.raises(BenchServiceNotRunning) as excinfo:
            bench.logs(follow=False, service="socketio")

        bench.docker_ops.logs.assert_not_called()
        assert "socketio" in str(excinfo.value)
        assert SITE in str(excinfo.value)

    def test_logs_for_a_running_service_are_streamed(self, harness):
        bench = harness.bench
        bench.docker_ops = MagicMock()
        bench.docker_ops._is_service_running.return_value = True
        bench.logs(follow=True, service="frappe")
        bench.docker_ops.logs.assert_called_once_with(services=["frappe"], follow=True)

    def test_logs_without_a_service_reads_the_host_side_files_instead(self, harness):
        bench = harness.bench
        bench.docker_ops = MagicMock()
        with patch.object(Bench, "handle_frappe_server_file_logs") as file_logs:
            bench.logs(follow=False)
        file_logs.assert_called_once_with(follow=False)
        bench.docker_ops.logs.assert_not_called()

    def test_setting_common_config_on_a_bench_without_one_is_refused(self, harness):
        with pytest.raises(BenchException) as excinfo:
            harness.bench.set_common_bench_config({"developer_mode": True})
        assert "common_site_config.json" in str(excinfo.value)

    def test_setting_common_config_writes_when_the_file_exists(self, harness):
        target = harness.path / "workspace/frappe-bench/sites/common_site_config.json"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("{}")
        harness.bench.set_common_bench_config({"developer_mode": True})
        assert '"developer_mode": true' in target.read_text()

    def test_setting_site_config_on_a_bench_without_one_is_refused(self, harness):
        with pytest.raises(BenchException) as excinfo:
            harness.bench.set_bench_site_config(SITE, {"admin_password": "x"})
        assert "site_config.json" in str(excinfo.value)

    def test_create_bench_site_config_makes_the_directory_and_the_file(self, harness):
        harness.bench.create_bench_site_config({"db_name": "abc"})
        path = harness.path / "workspace/frappe-bench/sites" / SITE / "site_config.json"
        assert '"db_name": "abc"' in path.read_text()

    def test_create_bench_site_config_merges_into_an_existing_file(self, harness):
        path = harness.path / "workspace/frappe-bench/sites" / SITE / "site_config.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text('{"keep": 1}')
        harness.bench.create_bench_site_config({"db_name": "abc"})
        text = path.read_text()
        assert '"keep": 1' in text
        assert '"db_name": "abc"' in text

    def test_a_failed_in_container_command_is_reraised_as_a_bench_exception(self, harness):
        harness.docker_client.compose.exec.side_effect = DockerException(["docker"], MagicMock())
        with pytest.raises(BenchException) as excinfo:
            harness.bench.frappe_service_run_command("bench version")
        assert "bench version" in str(excinfo.value)

    def test_a_successful_in_container_command_runs_as_the_frappe_user(self, harness):
        harness.bench.frappe_service_run_command("bench version")
        harness.docker_client.compose.exec.assert_called_once_with(
            "frappe", "bench version", user="frappe", stream=False
        )

    @pytest.mark.parametrize("bad", ["", "50", "M", "50MB", "1T", "-5M", "50 M"])
    def test_a_malformed_upload_limit_is_refused(self, harness, bad):
        with pytest.raises(BenchException) as excinfo:
            harness.bench.update_upload_limit(bad)
        assert "Invalid upload limit format" in str(excinfo.value)

    @pytest.mark.parametrize(
        ("size", "expected"),
        [("50M", 50 * 1024 * 1024), ("1G", 1024**3), ("1g", 1024**3), ("100m", 100 * 1024 * 1024)],
    )
    def test_upload_sizes_are_binary_not_decimal(self, harness, size, expected):
        assert harness.bench._parse_size_to_bytes(size) == expected

    @pytest.mark.parametrize("bad", ["50K", "abc", "50", "1TB"])
    def test_a_malformed_size_is_refused(self, harness, bad):
        with pytest.raises(BenchException):
            harness.bench._parse_size_to_bytes(bad)


class TestPerSiteSiteConfig:
    """`set_bench_site_config` writes to the site it is GIVEN, not to the bench's own.

    The argument exists because a deploy merges `[switch].site_config` into every site of the
    bench. Every other caller in the tests here passes the bench's own site, so a method that
    silently substituted the primary would look identical.

    The bench is named `shop` and serves `shop.localhost` beside `analytics.localhost`: a fixture
    whose bench name equals its site name cannot tell the two lookups apart.
    """

    BENCH = "shop"
    PRIMARY = "shop.localhost"
    OTHER = "analytics.localhost"

    def _multi_site(self, tmp_path):
        harness = build_bench(tmp_path, name=self.BENCH)
        harness.bench.bench_config.sites = {self.PRIMARY: SiteConfig(), self.OTHER: SiteConfig()}
        put_site_on_disk(harness.path, self.PRIMARY, schema="fm_shop_a1")
        put_site_on_disk(harness.path, self.OTHER, schema="fm_analytics_b2")
        return harness

    def _site_config(self, harness, site) -> dict:
        return json.loads((harness.path / "workspace/frappe-bench/sites" / site / "site_config.json").read_text())

    def test_a_write_lands_in_the_named_site_and_leaves_the_primary_alone(self, tmp_path):
        harness = self._multi_site(tmp_path)
        assert harness.bench.site_name == self.PRIMARY  # the one that would be substituted

        harness.bench.set_bench_site_config(self.OTHER, {"maintenance_mode": 1})

        assert self._site_config(harness, self.OTHER) == {"db_name": "fm_analytics_b2", "maintenance_mode": 1}
        # Both directions: the wrong file must not have gained the key either.
        assert self._site_config(harness, self.PRIMARY) == {"db_name": "fm_shop_a1"}


class TestUpdateUploadLimit:
    def test_the_limit_lands_in_all_of_site_config_bench_config_and_nginx(self, harness, tmp_path):
        bench = harness.bench
        bench.docker_ops = MagicMock()
        site_config = harness.path / "workspace/frappe-bench/sites" / SITE / "site_config.json"
        site_config.parent.mkdir(parents=True, exist_ok=True)
        site_config.write_text("{}")
        vhostd = harness.services.path / "nginx-proxy" / "vhostd"
        vhostd.mkdir(parents=True, exist_ok=True)
        # `alias.example.com` is an alternate FOR the site `test.localhost`, which is where the list
        # lives; the limit has to reach every hostname the bench serves, aliases included, because
        # a domain with no vhost entry gets nginx-proxy's own 1M default.
        bench.bench_config.sites = {SITE: SiteConfig(alias_domains=["alias.example.com"])}
        harness.services.is_service_running.return_value = True

        with patch.object(BenchConfig, "export_to_compose_inputs", return_value={"environment": {}}):
            bench.update_upload_limit("100m")

        assert '"max_file_size": 104857600' in site_config.read_text()
        # Normalised upward in config, downward in the nginx directive.
        assert bench.bench_config.upload_limit == "100M"
        custom = harness.path / "configs" / "nginx" / "conf" / "custom" / "upload-limit.conf"
        assert custom.read_text() == "client_max_body_size 100m;\n"
        # Both hostnames the bench serves carry the cap: the site's own name and its alias. The
        # directive is appended to whatever vhost fragment is already there, hence the substring.
        assert "client_max_body_size 100m;" in (vhostd / SITE).read_text()
        assert "client_max_body_size 100m;" in (vhostd / "alias.example.com").read_text()
        bench.bench_nginx_controller.reload.assert_called()
        harness.services.nginx_controller.reload.assert_called_once_with()

    def test_the_global_proxy_is_not_reloaded_when_it_is_not_running(self, harness):
        bench = harness.bench
        bench.docker_ops = MagicMock()
        site_config = harness.path / "workspace/frappe-bench/sites" / SITE / "site_config.json"
        site_config.parent.mkdir(parents=True, exist_ok=True)
        site_config.write_text("{}")
        harness.services.is_service_running.return_value = False

        with patch.object(BenchConfig, "export_to_compose_inputs", return_value={}):
            bench.update_upload_limit("50M")

        harness.services.nginx_controller.reload.assert_not_called()

    def test_vhostd_is_skipped_when_the_global_proxy_has_no_vhostd_dir(self, harness):
        bench = harness.bench
        bench.docker_ops = MagicMock()
        site_config = harness.path / "workspace/frappe-bench/sites" / SITE / "site_config.json"
        site_config.parent.mkdir(parents=True, exist_ok=True)
        site_config.write_text("{}")
        harness.services.is_service_running.return_value = False
        shutil.rmtree(harness.services.path / "nginx-proxy" / "vhostd")

        with (
            patch.object(BenchConfig, "export_to_compose_inputs", return_value={}),
            patch("frappe_manager.site_manager.site.UploadLimitManager") as mgr,
        ):
            bench.update_upload_limit("50M")

        mgr.assert_not_called()


# --------------------------------------------------------------------------------------
# Removal
# --------------------------------------------------------------------------------------


class TestRemoveBench:
    def _removable(self, harness, answer="yes"):
        bench = harness.bench
        bench.output.prompt_ask.return_value = answer
        bench.remove_certificate = MagicMock()
        # Returns the per-site outstanding list now, and an empty one is what lets the directory go.
        bench._handle_database_deletion = MagicMock(return_value=[])
        bench.remove_containers_and_dirs = MagicMock()
        return bench

    def test_declining_the_prompt_removes_nothing(self, harness):
        bench = self._removable(harness, answer="no")
        assert bench.remove_bench() is False
        bench.remove_certificate.assert_not_called()
        bench.remove_containers_and_dirs.assert_not_called()

    def test_default_choice_preselects_no(self, harness):
        bench = self._removable(harness, answer="no")
        bench.remove_bench(default_choice=True)
        assert bench.output.prompt_ask.call_args.kwargs["default"] == "no"

    def test_default_choice_false_offers_no_default(self, harness):
        bench = self._removable(harness, answer="no")
        bench.remove_bench(default_choice=False)
        assert "default" not in bench.output.prompt_ask.call_args.kwargs

    def test_accepting_removes_cert_then_database_then_containers(self, harness):
        bench = self._removable(harness)
        order = []
        bench.remove_certificate.side_effect = lambda: order.append("cert")
        bench._handle_database_deletion.side_effect = lambda pref: order.append(f"db:{pref}")
        bench.remove_containers_and_dirs.side_effect = lambda: order.append("dirs")

        assert bench.remove_bench(delete_db_from_global_db=True) is True

        assert order == ["cert", "db:True", "dirs"]

    def test_a_failing_certificate_removal_only_warns(self, harness):
        bench = self._removable(harness)
        bench.remove_certificate.side_effect = RuntimeError("acme is down")
        assert bench.remove_bench() is True
        bench.remove_containers_and_dirs.assert_called_once_with()
        assert any("acme is down" in str(c) for c in bench.output.warning.call_args_list)

    def test_an_outstanding_site_keeps_the_bench_and_raises(self, harness):
        """The gate. The bench directory carries the only record of the schema, so removing it after
        a failed drop leaves a database in global-db that nothing points at and no way to find its
        name. This used to warn "Continuing with bench removal", delete the directory anyway, and
        exit 0."""
        bench = self._removable(harness)
        put_site_on_disk(harness.path, SITE, schema="fm_test_ab12", bench=bench)
        entry = SiteSchema(site=SITE, schema="fm_test_ab12", external_host=None)
        bench._handle_database_deletion.return_value = [(entry, "db unreachable")]

        with pytest.raises(BenchException, match="Database deletion failed for 1 of 1 site") as excinfo:
            bench.remove_bench()

        bench.remove_containers_and_dirs.assert_not_called()
        assert "fm_test_ab12" in str(excinfo.value)

    def test_an_unexpected_failure_of_the_whole_deletion_step_keeps_the_bench_too(self, harness):
        """A per-site failure comes back as an outstanding entry rather than an exception, so nothing
        routine raises out of the step any more. The catch stays because the directory must survive
        an unexpected failure as well."""
        bench = self._removable(harness)
        put_site_on_disk(harness.path, SITE, schema="fm_test_ab12", bench=bench)
        bench._handle_database_deletion.side_effect = RuntimeError("db unreachable")

        with pytest.raises(BenchException, match="Database deletion failed"):
            bench.remove_bench()

        bench.remove_containers_and_dirs.assert_not_called()

    def test_a_failing_directory_removal_aborts(self, harness):
        bench = self._removable(harness)
        bench.remove_containers_and_dirs.side_effect = BenchRemoveDirectoryError(SITE, harness.path)
        with pytest.raises(BenchRemoveDirectoryError):
            bench.remove_bench()


class TestHandleDatabaseDeletion:
    def _bench(self, harness, *, schema="fm_test_ab12", external=None):
        """One site on disk, because the loop enumerates the filesystem rather than the config.

        `external` is that site's `[sites."<site>".database]` entry, whose mere presence is the
        switch for "this schema is not fm's to drop".
        """
        bench = harness.bench
        put_site_on_disk(harness.path, SITE, schema=schema, bench=bench)
        bench.bench_config.sites = {SITE: SiteConfig(database=external)}
        bench.remove_database_and_user = MagicMock()
        return bench

    def test_an_external_schema_is_never_dropped_and_is_never_prompted_for(self, harness):
        bench = self._bench(harness, schema="shopdb", external=DatabaseConfig(host="db.example.com", name="shopdb"))

        # Deliberately left counts as resolved: nothing is orphaned by surprise.
        assert bench._handle_database_deletion(None) == []

        bench.remove_database_and_user.assert_not_called()
        bench.output.prompt_ask.assert_not_called()
        printed = [str(c) for c in bench.output.print.call_args_list]
        # Where the data was left, so manual cleanup is possible.
        assert any("db.example.com" in line and "shopdb" in line for line in printed)

    def test_an_explicit_yes_drops_without_prompting(self, harness):
        bench = self._bench(harness)
        assert bench._handle_database_deletion(True) == []
        bench.remove_database_and_user.assert_called_once_with(SITE)
        bench.output.prompt_ask.assert_not_called()

    def test_an_explicit_no_keeps_the_schema_without_prompting(self, harness):
        bench = self._bench(harness)
        assert bench._handle_database_deletion(False) == []
        bench.remove_database_and_user.assert_not_called()
        bench.output.prompt_ask.assert_not_called()

    def test_no_preference_prompts_and_a_yes_drops(self, harness):
        bench = self._bench(harness)
        bench.output.prompt_ask.return_value = "yes"
        bench._handle_database_deletion(None)
        bench.remove_database_and_user.assert_called_once_with(SITE)
        assert bench.output.prompt_ask.call_args.kwargs["default"] == "yes"
        # The question names the SITE whose schema is about to go, not the bench.
        assert SITE in str(bench.output.prompt_ask.call_args.kwargs["prompt"])

    def test_no_preference_prompts_and_anything_but_yes_keeps(self, harness):
        bench = self._bench(harness)
        bench.output.prompt_ask.return_value = "no"
        # Declining is a resolution: the operator chose to keep the schema.
        assert bench._handle_database_deletion(None) == []
        bench.remove_database_and_user.assert_not_called()

    def test_external_database_config_is_looked_up_for_this_site(self, harness):
        harness.bench.bench_config = MagicMock()
        # A bare MagicMock answers `len() == 0` and `in` as False, so `site_name` cannot resolve
        # and refuses. Recording the bench's own site is what production does.
        harness.bench.bench_config.sites = {SITE: MagicMock()}
        harness.bench.external_database_config()
        harness.bench.bench_config.get_database_config.assert_called_once_with(SITE)


class TestMultiSiteRemoval:
    """A bench holds N sites, which is the whole reason the removal path became per-site.

    Driven through `remove_bench` rather than through the handler: what matters is WHEN the
    directory goes, and that decision is the gate in `remove_bench`. Order is read off a recorded
    call list, because "both schemas were dropped" and "they were dropped before the directory
    holding their names was destroyed" are different claims.
    """

    ALIAS = "shop.example.com"  # sorts before `test.localhost`, so it is enumerated FIRST

    def _bench(self, harness, sites, *, externals=None):
        """`sites` maps site name -> its `db_name` on disk; `externals` site name -> DatabaseConfig."""
        externals = externals or {}
        for site, schema in sites.items():
            put_site_on_disk(harness.path, site, schema=schema)
        harness.bench.bench_config.sites = {site: SiteConfig(database=externals.get(site)) for site in sites}
        return harness.bench

    def test_a_two_site_bench_drops_both_schemas_before_removing_the_directory(self, harness):
        bench = self._bench(harness, {self.ALIAS: "fm_shop_a1", SITE: "fm_test_b2"})
        calls = record_removal_steps(harness)

        assert bench.remove_bench(delete_db_from_global_db=True, prompt=False) is True

        assert calls.count(("dirs",)) == 1
        dirs = calls.index(("dirs",))
        assert calls.index(("drop", self.ALIAS)) < dirs
        assert calls.index(("drop", SITE)) < dirs

    def test_an_external_site_beside_a_global_db_one_drops_exactly_one_and_still_removes_the_directory(self, harness):
        """A deliberately left schema counts as resolved: it is not fm's to drop, so nothing is
        outstanding and the directory goes. The operator is told where it was left."""
        bench = self._bench(
            harness,
            {self.ALIAS: "shop_prod", SITE: "fm_test_b2"},
            externals={self.ALIAS: DatabaseConfig(host="mydb.abc.rds.amazonaws.com", name="shop_prod")},
        )
        calls = record_removal_steps(harness)

        assert bench.remove_bench(delete_db_from_global_db=True, prompt=False) is True

        assert [c for c in calls if c[0] == "drop"] == [("drop", SITE)]
        assert calls.index(("dirs",)) > calls.index(("drop", SITE))
        printed = [str(c) for c in bench.output.print.call_args_list]
        assert any("mydb.abc.rds.amazonaws.com" in line and "shop_prod" in line for line in printed)

    def test_the_second_site_is_still_attempted_after_the_first_one_fails(self, harness):
        """One broken site must not leave the others unaccounted for, so the loop catches per site
        instead of aborting. The directory stays for the failed site's sake alone: its schema name
        exists only inside it."""
        bench = self._bench(harness, {self.ALIAS: "fm_shop_a1", SITE: "fm_test_b2"})
        calls = record_removal_steps(harness)

        def drop(site=None):
            calls.append(("drop", site))
            if site == self.ALIAS:
                raise RuntimeError("global-db unreachable")

        bench.remove_database_and_user.side_effect = drop

        with pytest.raises(BenchException, match="Database deletion failed for 1 of 2 site") as excinfo:
            bench.remove_bench(delete_db_from_global_db=True, prompt=False)

        assert ("drop", SITE) in calls
        assert ("dirs",) not in calls
        message = str(excinfo.value)
        assert self.ALIAS in message
        assert "fm_shop_a1" in message  # the statements that finish the job by hand

    def test_an_unreadable_site_config_blocks_the_directory_removal(self, harness):
        """Nothing raised: fm just cannot tell which schema this site uses, and the file that could
        answer is inside the directory, so the directory stays."""
        bench = self._bench(harness, {SITE: "fm_test_b2"})
        put_site_on_disk(harness.path, self.ALIAS, raw="{not json", bench=bench)
        calls = record_removal_steps(harness)

        with pytest.raises(BenchException, match="Database deletion failed for 1 of 2 site") as excinfo:
            bench.remove_bench(delete_db_from_global_db=True, prompt=False)

        assert ("dirs",) not in calls
        assert ("drop", SITE) in calls  # the readable site was still accounted for
        config = harness.path / "workspace" / "frappe-bench" / "sites" / self.ALIAS / "site_config.json"
        assert str(config) in str(excinfo.value)

    def test_a_bench_with_no_sites_on_disk_removes_cleanly_without_prompting(self, harness):
        """A `--bench-only` bench: no site directory, so no schema to account for and nothing to
        ask about."""
        calls = record_removal_steps(harness)

        assert harness.bench.remove_bench(prompt=False) is True

        assert calls == [("cert",), ("dirs",)]
        harness.bench.output.prompt_ask.assert_not_called()


class TestRemoveContainersAndDirs:
    def _bench(self, harness, *, main=True, workers=True, admin=True, leftover=()):
        bench = harness.bench
        bench.docker_ops = MagicMock()
        bench.compose_file_manager.exists.return_value = main
        bench.workers = MagicMock()
        bench.workers.compose_file_manager.exists.return_value = workers
        bench.admin_tools = MagicMock()
        bench.admin_tools.compose_file_manager.exists.return_value = admin
        # Docker's own record of what exists, which is what the sweep consults. Two calls: the
        # containers found, then what survived the removal.
        remaining = list(leftover)
        bench.docker_client.container_names.side_effect = lambda _prefix: list(remaining)
        bench.docker_client.rm.side_effect = lambda name, **_kw: remaining.remove(name)
        return bench

    def test_all_three_compose_projects_are_torn_down_then_the_directory(self, harness):
        bench = self._bench(harness)
        bench.remove_containers_and_dirs()
        bench.docker_ops.remove_containers.assert_called_once_with(remove_volumes=True, timeout=5)
        bench.workers.docker_client.compose.down.assert_called_once_with(
            remove_orphans=True, volumes=True, timeout=5, stream=True
        )
        bench.admin_tools.docker_client.compose.down.assert_called_once_with(
            remove_orphans=True, volumes=True, timeout=5, stream=True
        )
        assert not harness.path.exists()

    def test_containers_without_a_compose_file_are_removed_by_name(self, harness):
        """The bug this replaces: a missing compose file only produced a warning, the containers were
        left running, and the directory was deleted anyway. That destroyed the compose files that
        were fm's only route back to them, so they ran forever under names no command could find and
        the next create of the same name adopted them with every bind mount pointing at nothing."""
        bench = self._bench(harness, main=False, workers=False, admin=False, leftover=["fm__x__nginx", "fm__x__frappe"])

        bench.remove_containers_and_dirs()

        assert [c.args[0] for c in bench.docker_client.rm.call_args_list] == ["fm__x__nginx", "fm__x__frappe"]
        assert not harness.path.exists()

    def test_a_container_that_survives_removal_keeps_the_directory(self, harness):
        """Same gate as the schema one, for the same reason: the directory is the only link back."""
        bench = self._bench(harness, leftover=["fm__x__nginx"])
        bench.docker_client.rm.side_effect = RuntimeError("docker is wedged")

        with pytest.raises(BenchException) as refusal:
            bench.remove_containers_and_dirs()

        assert harness.path.exists()
        assert "fm__x__nginx" in str(refusal.value)
        assert "docker rm -f -v fm__x__nginx" in str(refusal.value)

    def test_nothing_left_over_is_the_quiet_path(self, harness):
        """The common case must not print about containers it did not have to chase."""
        bench = self._bench(harness)

        bench.remove_containers_and_dirs()

        bench.docker_client.rm.assert_not_called()
        assert not any("no compose file" in str(c) for c in bench.output.print.call_args_list)

    def test_a_failing_admin_tools_teardown_does_not_block_removal(self, harness):
        bench = self._bench(harness)
        bench.admin_tools.docker_client.compose.down.side_effect = RuntimeError("gone")
        bench.remove_containers_and_dirs()
        assert not harness.path.exists()

    def test_root_owned_files_are_chowned_in_a_container_then_removed(self, harness):
        bench = self._bench(harness)
        bench.compose_file_manager.get_all_images.return_value = {"frappe": {"name": "ghcr.io/fm/frappe", "tag": "v1"}}
        calls = []
        with patch(
            "frappe_manager.site_manager.site.shutil.rmtree",
            side_effect=[PermissionError("root owned"), None],
        ) as rmtree:
            harness.docker_client.run.side_effect = lambda **kw: calls.append(kw)
            bench.remove_containers_and_dirs()

        assert rmtree.call_count == 2
        assert calls[0]["image"] == "ghcr.io/fm/frappe:v1"
        assert calls[0]["volume"] == [f"{harness.path}/workspace:/workspace"]

    def test_a_permission_error_with_no_frappe_image_is_swallowed_and_nothing_is_removed(self, harness):
        # SUSPICION: with no frappe image to chown with, the inner block completes
        # without raising, so the PermissionError is dropped and the caller is told
        # "Removed all bench files and directories" over a bench that is still there.
        bench = self._bench(harness)
        bench.compose_file_manager.get_all_images.return_value = {}
        with patch("frappe_manager.site_manager.site.shutil.rmtree", side_effect=PermissionError("root owned")):
            bench.remove_containers_and_dirs()
        assert harness.path.exists()
        assert any("Removed all bench files" in str(c) for c in bench.output.print.call_args_list)

    def test_a_permission_error_the_chown_cannot_fix_raises_the_named_error(self, harness):
        bench = self._bench(harness)
        bench.compose_file_manager.get_all_images.side_effect = RuntimeError("compose unreadable")
        with (
            patch("frappe_manager.site_manager.site.shutil.rmtree", side_effect=PermissionError("root owned")),
            pytest.raises(BenchRemoveDirectoryError),
        ):
            bench.remove_containers_and_dirs()


# --------------------------------------------------------------------------------------
# Readiness probe, reset, restarts, misc delegation
# --------------------------------------------------------------------------------------


class TestIsBenchCreated:
    def test_a_200_response_means_created(self, harness):
        result = MagicMock()
        result.stdout = ["HTTP/1.1 200 OK", "Server: nginx"]
        harness.docker_client.compose.exec.return_value = result
        assert harness.bench.is_bench_created(retry=3, interval=0) is True
        assert harness.docker_client.compose.exec.call_count == 1

    def test_a_non_200_response_exhausts_the_retries_and_reports_not_created(self, harness):
        result = MagicMock()
        result.stdout = ["HTTP/1.1 502 Bad Gateway"]
        harness.docker_client.compose.exec.return_value = result
        assert harness.bench.is_bench_created(retry=3, interval=0) is False
        assert harness.docker_client.compose.exec.call_count == 3

    def test_exec_failures_are_retried_with_a_sleep_between_attempts(self, harness):
        harness.docker_client.compose.exec.side_effect = RuntimeError("not up")
        with patch("frappe_manager.site_manager.site.time.sleep") as sleep:
            assert harness.bench.is_bench_created(retry=2, interval=7) is False
        assert sleep.call_args_list == [call(7), call(7)]

    def test_a_prod_bench_probes_through_its_own_host_header(self, harness):
        harness.bench.bench_config.environment_type = FMBenchEnvType.prod
        result = MagicMock()
        result.stdout = ["HTTP/1.1 200 OK"]
        harness.docker_client.compose.exec.return_value = result
        harness.bench.is_bench_created(retry=1, interval=0)
        command = harness.docker_client.compose.exec.call_args.kwargs["command"]
        # In prod the bench nginx routes by Host; a bare localhost probe would 404.
        assert f"-H 'Host: {SITE}'" in command

    def test_a_dev_bench_probes_bare_localhost(self, harness):
        result = MagicMock()
        result.stdout = ["HTTP/1.1 200 OK"]
        harness.docker_client.compose.exec.return_value = result
        harness.bench.is_bench_created(retry=1, interval=0)
        command = harness.docker_client.compose.exec.call_args.kwargs["command"]
        assert "-H 'Host:" not in command
        # SUSPICION: the retry *count* is also interpolated as curl's --max-time and
        # --connect-timeout. Pinned, not fixed.
        assert "--max-time 1" in command


class TestReset:
    def _resettable(self, harness):
        bench = harness.bench
        bench.site_manager = MagicMock()
        bench.set_bench_site_config = MagicMock()
        bench.get_bench_site_config = MagicMock(return_value={})
        bench.get_common_bench_config = MagicMock(return_value={})
        return bench

    def test_an_explicit_password_wins_over_every_stored_one(self, harness):
        bench = self._resettable(harness)
        bench.get_bench_site_config.return_value = {"admin_password": "from-site"}
        bench.reset(admin_password="explicit")
        # The site travels by keyword, and defaults to the bench's own primary site.
        bench.site_manager.reset_bench_site.assert_called_once_with("explicit", site=SITE)
        bench.set_bench_site_config.assert_called_once_with(SITE, {"admin_password": "explicit"})

    def test_site_config_beats_common_site_config(self, harness):
        bench = self._resettable(harness)
        bench.get_bench_site_config.return_value = {"admin_password": "from-site"}
        bench.get_common_bench_config.return_value = {"admin_password": "from-common"}
        bench.reset()
        bench.site_manager.reset_bench_site.assert_called_once_with("from-site", site=SITE)

    def test_common_site_config_is_the_fallback(self, harness):
        bench = self._resettable(harness)
        bench.get_common_bench_config.return_value = {"admin_password": "from-common"}
        bench.reset()
        bench.site_manager.reset_bench_site.assert_called_once_with("from-common", site=SITE)

    def test_with_nothing_stored_the_user_is_prompted(self, harness):
        bench = self._resettable(harness)
        bench.output.prompt_ask.return_value = "typed"
        bench.reset()
        bench.site_manager.reset_bench_site.assert_called_once_with("typed", site=SITE)
        assert bench.output.prompt_ask.call_args.kwargs["required_flag"] == "--admin-pass"
        # The password being asked for belongs to a SITE, so the prompt names one.
        assert SITE in str(bench.output.prompt_ask.call_args.kwargs["prompt"])

    def test_the_password_used_is_written_back_into_site_config(self, harness):
        bench = self._resettable(harness)
        bench.get_common_bench_config.return_value = {"admin_password": "from-common"}
        bench.reset()
        bench.set_bench_site_config.assert_called_once_with(SITE, {"admin_password": "from-common"})

    def test_an_explicit_site_is_the_one_reset_and_the_one_named(self, harness):
        """A bench holds N sites, so `reset` takes the one it means. Reinstalling the bench's
        primary when the operator named another would drop the wrong schema."""
        bench = self._resettable(harness)
        bench.reset(admin_password="explicit", site="b.example.com")
        bench.site_manager.reset_bench_site.assert_called_once_with("explicit", site="b.example.com")
        printed = [str(c) for c in bench.output.print.call_args_list]
        assert any("b.example.com" in line for line in printed)

    def test_the_named_site_is_the_one_whose_password_is_read_and_recorded(self, harness):
        """Both halves used to reach for the bench's own site while resetting another.

        `fm reset shop/b.example.com` read shop.localhost's recorded `admin_password` as its
        fallback, reset b.example.com with it, then wrote it back over shop.localhost's record. The
        operator ended up with b reset to a password they were never shown and shop's stored
        password replaced, both silently.
        """
        bench = self._resettable(harness)
        bench.get_bench_site_config.return_value = {"admin_password": "b-own-password"}

        bench.reset(site="b.example.com")

        bench.get_bench_site_config.assert_called_once_with("b.example.com")
        bench.site_manager.reset_bench_site.assert_called_once_with("b-own-password", site="b.example.com")
        bench.set_bench_site_config.assert_called_once_with(
            "b.example.com", {"admin_password": "b-own-password"}
        )


class TestRestarts:
    def test_web_restart_defaults_to_cycling_supervisor_processes(self, harness):
        bench = harness.bench
        bench.docker_ops = MagicMock()
        bench.restart_supervisor_service = MagicMock(return_value=True)
        bench.restart_web_containers_services()
        assert [c.args[0] for c in bench.restart_supervisor_service.call_args_list] == ["frappe", "socketio"]
        bench.docker_ops.restart_services.assert_not_called()

    def test_web_restart_can_cycle_the_containers_instead(self, harness):
        bench = harness.bench
        bench.docker_ops = MagicMock()
        bench.restart_supervisor_service = MagicMock()
        bench.restart_web_containers_services(use_container_restart=True, force=True)
        bench.docker_ops.restart_services.assert_called_once_with(["frappe", "socketio"], force=True)
        bench.restart_supervisor_service.assert_not_called()

    def test_a_supervisor_process_that_did_not_restart_is_not_reported_as_restarted(self, harness):
        bench = harness.bench
        bench.docker_ops = MagicMock()
        bench.restart_supervisor_service = MagicMock(return_value=False)
        bench.restart_web_containers_services()
        printed = [str(c) for c in bench.output.print.call_args_list]
        assert not any("supervisor processes" in p for p in printed)

    def test_an_external_redis_bench_restarts_no_redis_container(self, harness):
        bench = harness.bench
        bench.docker_ops = MagicMock()
        bench.bench_config.redis = MagicMock()
        bench.restart_redis_services_containers()
        bench.docker_ops.restart_services.assert_not_called()

    def test_an_fm_managed_redis_bench_restarts_both_redis_containers(self, harness):
        bench = harness.bench
        bench.docker_ops = MagicMock()
        bench.bench_config.redis = None
        bench.restart_redis_services_containers()
        bench.docker_ops.restart_services.assert_called_once_with(["redis-cache", "redis-queue"])

    def test_nginx_restart_forwards_the_force_flag(self, harness):
        bench = harness.bench
        bench.docker_ops = MagicMock()
        bench.restart_nginx_service(force=True)
        bench.docker_ops.restart_services.assert_called_once_with(["nginx"], force=True)


class TestCertificateFacade:
    def test_creating_a_certificate_persists_the_config_afterwards(self, harness):
        bench = harness.bench
        bench.ssl = MagicMock()
        order = []
        bench.ssl.create_individual_certificates.side_effect = lambda: order.append("mint")
        with patch.object(Bench, "save_bench_config", side_effect=lambda *a, **k: order.append("save")):
            bench.create_certificate()
        assert order == ["mint", "save"]

    def test_removing_certificates_clears_the_config_list_then_saves(self, harness):
        bench = harness.bench
        bench.ssl = MagicMock()
        bench.bench_config.ssl_certificates = [MagicMock()]
        with patch.object(Bench, "save_bench_config") as save:
            bench.remove_certificate()
        bench.ssl.remove_all_certificates.assert_called_once_with()
        assert bench.bench_config.ssl_certificates == []
        save.assert_called_once_with()

    def test_a_successful_update_promotes_the_certificate_to_primary(self, harness):
        bench = harness.bench
        bench.ssl = MagicMock(**{"update_certificate.return_value": True})
        bench.bench_config = MagicMock()
        cert = MagicMock()
        assert bench.update_certificate(cert) is True
        bench.bench_config.set_primary_certificate.assert_called_once_with(cert)

    def test_a_no_op_update_leaves_the_primary_certificate_alone(self, harness):
        bench = harness.bench
        bench.ssl = MagicMock(**{"update_certificate.return_value": False})
        bench.bench_config = MagicMock()
        assert bench.update_certificate(MagicMock(), raise_error=False) is False
        bench.bench_config.set_primary_certificate.assert_not_called()

    def test_renew_returns_the_ssl_modules_verdict(self, harness):
        harness.bench.ssl = MagicMock(**{"renew_certificate.return_value": "renewed"})
        assert harness.bench.renew_certificate() == "renewed"


class TestSaveBenchConfig:
    def test_saving_writes_the_config_to_its_own_root_path(self, harness):
        harness.bench.save_bench_config()
        assert harness.config_toml.exists()

    def test_quiet_saves_print_nothing(self, harness):
        harness.bench.save_bench_config(print_message=False)
        harness.bench.output.change_head.assert_not_called()
        harness.bench.output.print.assert_not_called()

    def test_loud_saves_announce_themselves(self, harness):
        harness.bench.save_bench_config(print_message=True)
        harness.bench.output.change_head.assert_called_once()
        harness.bench.output.print.assert_called_once()


class TestHostSideLogFiles:
    def test_no_log_files_warns_and_returns(self, harness):
        harness.bench.info_display = MagicMock()
        harness.bench.info_display.get_log_file_paths.return_value = []
        harness.bench.handle_frappe_server_file_logs(follow=False)
        printed = harness.bench.output.print.call_args.args[0]
        assert "No log files found" in printed

    @pytest.mark.timeout(15)
    def test_files_are_printed_whole_one_after_another_not_interleaved(self, harness, capsys):
        a = harness.path / "a.log"
        b = harness.path / "b.log"
        a.write_text("a1\na2\n")
        b.write_text("b1\nb2\n")
        harness.bench.info_display = MagicMock()
        harness.bench.info_display.get_log_file_paths.return_value = [a, b]

        harness.bench.handle_frappe_server_file_logs(follow=False)

        assert capsys.readouterr().out == "a1\na2\nb1\nb2\n"

    @pytest.mark.timeout(15)
    def test_host_log_files_that_do_not_exist_warn_instead_of_raising(self, harness):
        """Regression: with a REAL BenchInfo the expected web log is simply absent until the
        web program has run once (fresh bench, dev->prod switch, image bench before its
        first deploy). `handle_frappe_server_file_logs` open()s every path it is handed, so
        an unfiltered path list turned `fm logs <bench>` into an 'Unexpected Error [Errno 2]
        No such file or directory' with exit 1 -- and made the 'No log files found' guard
        directly above it dead code.
        """
        (harness.path / "workspace" / "frappe-bench" / "logs").mkdir(parents=True)

        harness.bench.logs(follow=False)

        printed = harness.bench.output.print.call_args.args[0]
        assert "No log files found" in printed

    @pytest.mark.timeout(15)
    def test_only_the_log_files_present_on_disk_are_printed(self, harness, capsys):
        """The dev log exists, so it is read: the filter drops absent paths, not real ones."""
        logs = harness.path / "workspace" / "frappe-bench" / "logs"
        logs.mkdir(parents=True)
        (logs / "web.dev.log").write_text("dev1\ndev2\n")

        harness.bench.logs(follow=False)

        assert capsys.readouterr().out == "dev1\ndev2\n"


class TestServiceRouting:
    """`--service` names come from `get_available_services()`, which is the UNION of the
    bench's three compose files, while `self.docker_ops` only knows docker-compose.yml.
    Driving an admin-tools or worker service through docker_ops fails at the docker layer
    with `no such service: adminer` even though the container is running, so `fm logs
    --service adminer` / `fm shell --service adminer` advertised what they could not reach.
    """

    @staticmethod
    def _with_admin_tools(harness, *, services, running=True):
        bench = harness.bench
        bench.docker_ops = MagicMock(name="main_docker_ops")
        admin_compose = harness.path / "docker-compose.admin-tools.yml"
        admin_compose.write_text("")
        bench.admin_tools = MagicMock(name="admin_tools")
        bench.admin_tools.compose_file_manager.get_services_list.return_value = list(services)
        bench.admin_tools.docker_client.compose.docker_compose_cmd = [
            "docker",
            "compose",
            "-f",
            admin_compose.as_posix(),
        ]
        bench.admin_tools.docker_client.compose.get_all_services_status.return_value = [
            {"Service": name, "State": "running" if running else "exited"} for name in services
        ]
        return bench

    def test_logs_for_an_admin_tools_service_use_the_admin_tools_compose_client(self, harness):
        bench = self._with_admin_tools(harness, services=["adminer", "mailpit"])

        bench.logs(follow=False, service="adminer")

        bench.admin_tools.docker_client.compose.logs.assert_called_once_with(
            services=["adminer"], follow=False, stream=True
        )
        bench.docker_ops.logs.assert_not_called()

    def test_a_stopped_admin_tools_service_is_still_refused(self, harness):
        bench = self._with_admin_tools(harness, services=["adminer"], running=False)

        with pytest.raises(BenchServiceNotRunning, match="adminer"):
            bench.logs(follow=False, service="adminer")

        bench.admin_tools.docker_client.compose.logs.assert_not_called()

    def test_shell_for_an_admin_tools_service_execs_against_its_own_compose_file(self, harness):
        bench = self._with_admin_tools(harness, services=["adminer"])

        with patch("os.execvp") as execvp:
            bench.shell("adminer", None)

        argv = execvp.call_args.args[1]
        assert argv[:3] == ["docker", "compose", "-f"]
        assert argv[3] == (harness.path / "docker-compose.admin-tools.yml").as_posix()
        # `adminer` has no bash, hence `sh` -- and no --user/--workdir, which are frappe-only.
        assert argv[4:] == ["exec", "adminer", "sh"]
        bench.docker_ops.shell.assert_not_called()

    def test_a_command_run_in_an_admin_tools_service_execs_on_its_own_compose_client(self, harness):
        """`fm shell --service adminer -c ...` is the same advertised service, same routing."""
        bench = self._with_admin_tools(harness, services=["adminer"])
        bench.admin_tools.docker_client.compose.exec.return_value = SubprocessOutput([], [], [], 0)

        assert bench.execute_command("adminer", "id", None, shell_path="sh") == 0

        assert bench.admin_tools.docker_client.compose.exec.call_args.kwargs["service"] == "adminer"
        bench.docker_ops.execute_command.assert_not_called()

    def test_a_main_compose_service_still_goes_through_docker_ops(self, harness):
        bench = self._with_admin_tools(harness, services=["adminer"])
        bench.docker_ops._is_service_running.return_value = True

        bench.logs(follow=True, service="nginx")
        bench.shell("nginx", None)

        bench.docker_ops.logs.assert_called_once_with(services=["nginx"], follow=True)
        bench.docker_ops.shell.assert_called_once_with("nginx", None, shell_path=None, use_run=False, site=None)
        bench.admin_tools.docker_client.compose.logs.assert_not_called()


class TestAvailableServices:
    def test_services_are_collected_from_every_compose_file_that_exists(self, harness):
        bench = harness.bench
        bench.compose_file_manager.compose_path = harness.path / "docker-compose.yml"
        bench.compose_file_manager.compose_path.write_text("")
        bench.compose_file_manager.get_services_list.return_value = ["frappe", "nginx"]
        bench.workers = MagicMock()
        bench.workers.compose_file_manager.get_services_list.return_value = ["schedule"]
        bench.admin_tools = MagicMock()
        bench.admin_tools.compose_file_manager.get_services_list.return_value = ["adminer"]
        (harness.path / "docker-compose.workers.yml").write_text("")
        (harness.path / "docker-compose.admin-tools.yml").write_text("")

        assert bench.get_available_services() == ["frappe", "nginx", "schedule", "adminer"]

    def test_absent_compose_files_contribute_nothing(self, harness):
        bench = harness.bench
        bench.compose_file_manager.compose_path = harness.path / "docker-compose.yml"
        bench.compose_file_manager.compose_path.write_text("")
        bench.compose_file_manager.get_services_list.return_value = ["frappe"]
        bench.workers = MagicMock()
        bench.admin_tools = MagicMock()

        assert bench.get_available_services() == ["frappe"]
        bench.workers.compose_file_manager.get_services_list.assert_not_called()
        bench.admin_tools.compose_file_manager.get_services_list.assert_not_called()


class TestThinDelegations:
    """Facade methods whose only contract is *which* module they route to."""

    def test_worker_coordinator_routes(self, harness):
        bench = harness.bench
        bench.worker_coordinator = MagicMock()
        backup = MagicMock()

        bench.sync_workers_compose(force_recreate=True, setup_supervisor=False, start=False)
        bench.worker_coordinator.sync_workers_compose.assert_called_once_with(
            force_recreate=True,
            setup_supervisor=False,
            include_default_workers=True,
            include_custom_workers=True,
            start=False,
        )

        bench.backup_restore_workers_supervisor(backup)
        bench.worker_coordinator.backup_restore_workers_supervisor.assert_called_once_with(backup)

        bench.backup_workers_supervisor_conf()
        bench.worker_coordinator.backup_workers_supervisor_conf.assert_called_once_with()

        bench.regenerate_workers_supervisor_conf()
        bench.worker_coordinator.regenerate_workers_supervisor_conf.assert_called_once_with()

        bench.ensure_workers_running_if_available()
        bench.worker_coordinator.ensure_workers_running_if_available.assert_called_once_with()

        bench.restart_workers_containers_services(use_container_restart=True, force=True)
        bench.worker_coordinator.restart_workers_containers_services.assert_called_once_with(
            use_container_restart=True, force=True
        )

    def test_docker_ops_routes(self, harness):
        bench = harness.bench
        bench.docker_ops = MagicMock()

        bench.frappe_logs_till_start()
        bench.docker_ops.frappe_logs_till_start.assert_called_once_with()

        bench.shell("frappe", None, shell_path="/bin/sh", use_run=True)
        bench.docker_ops.shell.assert_called_once_with("frappe", None, shell_path="/bin/sh", use_run=True, site=None)

        bench.execute_command("frappe", "ls", user="root", shell_path="/bin/bash", use_run=False)
        bench.docker_ops.execute_command.assert_called_once_with(
            "frappe", "ls", "root", shell_path="/bin/bash", use_run=False, site=None
        )

    def test_devtools_and_supervisor_routes(self, harness):
        bench = harness.bench
        bench.devtools = MagicMock()
        bench.supervisor = MagicMock()

        bench.attach_to_bench("frappe", ["ms-python.python"], "/workspace", debugger=True)
        bench.devtools.attach_to_bench.assert_called_once_with("frappe", ["ms-python.python"], "/workspace", True)

        bench.get_apps_dev_requirements()
        bench.devtools.get_apps_dev_requirements.assert_called_once_with()

        bench.install_dev_packages()
        bench.devtools.install_dev_packages.assert_called_once_with()

        bench.remove_dev_packages()
        bench.devtools.remove_dev_packages.assert_called_once_with()

        bench.is_supervisord_running(interval=5, timeout=9)
        bench.supervisor.is_supervisord_running.assert_called_once_with(5, 9)

        bench.restart_supervisor_service("frappe", None, 30, 1, True)
        bench.supervisor.restart_supervisor_service.assert_called_once_with("frappe", None, 30, 1, True)

    def test_info_and_database_routes(self, harness):
        bench = harness.bench
        bench.info_display = MagicMock()
        bench.database = MagicMock()

        bench.info()
        bench.info_display.display_info.assert_called_once_with()

        bench.get_bench_apps()
        bench.info_display.get_bench_apps.assert_called_once_with()

        bench.get_common_bench_config()
        bench.info_display.get_common_config.assert_called_once_with()

        # Forwards the site now, so a caller acting on a named site of a multisite bench reads that
        # site's file. None keeps the bench's own, which is what every existing caller wanted.
        bench.get_bench_site_config()
        bench.info_display.get_site_config.assert_called_once_with(None)

        bench.info_display.get_site_config.reset_mock()
        bench.get_bench_site_config("second.example.com")
        bench.info_display.get_site_config.assert_called_once_with("second.example.com")

        bench.get_log_file_paths()
        bench.info_display.get_log_file_paths.assert_called_once_with()

        # Forwards the site now: the card reads one row per site, and every caller used to get the
        # primary's schema and password whatever it asked for.
        bench.get_db_connection_info()
        bench.database.get_connection_info.assert_called_once_with(None)

        bench.database.get_connection_info.reset_mock()
        bench.get_db_connection_info("b.example.com")
        bench.database.get_connection_info.assert_called_once_with("b.example.com")

        # The site travels through: None means the bench's primary, which the module resolves.
        bench.remove_database_and_user()
        bench.database.remove_database_and_user.assert_called_once_with(None)

        bench.database.remove_database_and_user.reset_mock()
        bench.remove_database_and_user("b.example.com")
        bench.database.remove_database_and_user.assert_called_once_with("b.example.com")

    def test_alias_domain_updates_are_an_orchestrator_workflow(self, harness):
        bench = harness.bench
        bench.orchestrator = MagicMock()
        # The site travels through, same as the removal route above: an alias is an alternate FOR a
        # site, so which site it attaches to is part of the request. None means the bench's primary,
        # which the orchestrator resolves.
        bench.update_alias_domains(["a.example.com"], ["b.example.com"])
        bench.orchestrator.update_alias_domains.assert_called_once_with(
            ["a.example.com"], ["b.example.com"], site=None
        )

        bench.orchestrator.update_alias_domains.reset_mock()
        bench.update_alias_domains(["a.example.com"], None, site="other.example.com")
        bench.orchestrator.update_alias_domains.assert_called_once_with(
            ["a.example.com"], None, site="other.example.com"
        )

    def test_has_certificate_routes_to_the_ssl_module(self, harness):
        harness.bench.ssl = MagicMock(**{"has_certificate.return_value": True})
        assert harness.bench.has_certificate() is True
