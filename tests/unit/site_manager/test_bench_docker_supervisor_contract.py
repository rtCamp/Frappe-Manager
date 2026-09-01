"""Characterization of `BenchDockerOps` and `BenchSupervisor`.

These two modules decide what a bench actually *is* on disk and in the container:

`bench_docker.BenchDockerOps` owns
  * the compose projection handed to `configure_bench` (users, aliases, extra_hosts,
    the dev-SSL CA mount, the restart policy) and the image re-pin for image runtime,
  * the host skeleton (`create_compose_dirs`): which directories exist per runtime,
    which files are seeded once and never clobbered, and which `host_run_cp`
    extractions run and with which image,
  * the guards that refuse an action when a service is not running, and the
    argv it hands to `execvp` for a shell.

`bench_supervisor.BenchSupervisor` renders `supervisor.conf` and the gunicorn
wrapper. The numbers in there are load-bearing: the worker/thread sizing, the
`user=` every program runs as, and the per-program stop grace (`stopwaitsecs`),
which must stay above gunicorn's `--graceful-timeout` or a restart orphans
gunicorn workers. A custom queue's `timeout` becomes that program's stop grace.

Nothing here touches docker, the network or a real bench: the compose file
manager and docker client are mocks, the filesystem is `tmp_path`, and the real
Jinja templates are rendered so the assertions are about observable output.

Companion files, deliberately not duplicated here:
  * `test_nginx_conf_seeding.py`   -- merge/never-clobber/symlink semantics of `_seed_nginx_conf`
  * `test_execute_command_quoting.py` -- shlex round-tripping of `execute_command`
  * `test_custom_worker_validation.py` -- `validate_custom_workers` + stale split-conf cleanup
"""

import configparser
import os
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, call

import pytest

from frappe_manager.docker import DockerException
from frappe_manager.docker.subprocess_output import SubprocessOutput
from frappe_manager.site_manager.bench_config import (
    BenchConfig,
    BenchRuntime,
    FMBenchEnvType,
    MonitoringConfig,
    NewRelicConfig,
)
from frappe_manager.site_manager.exceptions import BenchOperationException, BenchServiceNotRunning
from frappe_manager.site_manager.modules.bench_docker import BenchDockerOps
from frappe_manager.site_manager.modules.bench_supervisor import BenchSupervisor
from frappe_manager.site_manager.modules.compose_shape import ServiceSpec
from frappe_manager.ssl_manager import SUPPORTED_SSL_TYPES

DOCKER_MODULE = "frappe_manager.site_manager.modules.bench_docker"
SHAPE_MODULE = "frappe_manager.site_manager.modules.compose_shape"
SUPERVISOR_MODULE = "frappe_manager.site_manager.modules.bench_supervisor"


def _docker_exception(*lines: str) -> DockerException:
    out = list(lines)
    return DockerException(["docker", "compose", "ps"], SubprocessOutput(out, out, out, 1))


# =============================================================== BenchDockerOps


def _ops(
    tmp_path,
    *,
    runtime=BenchRuntime.mount,
    name="test.localhost",
    site=None,
    alias_domains=None,
    ssl_certificates=(),
    yml=None,
) -> BenchDockerOps:
    """A `BenchDockerOps` whose collaborators are all mocks.

    `name` is the BENCH; `site` is the one site it serves, defaulting to the bench name because
    that is what an fm-created bench looks like. They are separate knobs so a test can tell the
    two apart: anything derived from the bench must not silently start following the site.
    """
    ops = object.__new__(BenchDockerOps)
    ops.logger = MagicMock()
    ops.docker_client = MagicMock()
    ops.compose_file_manager = MagicMock()
    ops.compose_file_manager.compose_path = tmp_path / "docker-compose.yml"
    ops.compose_file_manager.yml = yml or {
        "services": {"nginx": {"image": "nginx:pinned"}, "frappe": {"image": "frappe:pinned"}}
    }
    # A stand-in for `BenchConfig`. `generate_compose` reads only `domains` for routing, so that is
    # what the stand-in answers: aliases are no longer a bench-level list, they belong to a site,
    # and `domains` is each site's own name followed by that site's aliases. This bench has one
    # site, `site or name`, and `alias_domains` here are ITS aliases.
    ops.config = SimpleNamespace(
        name=name,
        runtime=runtime,
        domains=[site or name, *(alias_domains or [])],
        ssl_certificates=list(ssl_certificates),
        container_name_prefix="fm__test_localhost",
    )
    ops.path = tmp_path
    ops.output = MagicMock()
    return ops


class TestServiceRunningDecisions:
    """`is_running` and friends answer "is the bench healthy" -- the answer drives
    whether fm refuses to shell in, restarts, or reports a broken bench."""

    @staticmethod
    def _statuses(*triples):
        return [{"Service": s, "State": st, "Name": n} for s, st, n in triples]

    @pytest.mark.timeout(15)
    def test_is_running_excludes_services_fm_never_starts(self, tmp_path):
        """A bench on an external redis has redis suppressed via the `disabled`
        profile; counting it would report a healthy bench as broken."""
        ops = _ops(tmp_path)
        ops.compose_file_manager.get_services_list.return_value = ["frappe", "nginx"]
        ops.compose_file_manager.get_container_names.return_value = {"frappe": "c-frappe", "nginx": "c-nginx"}
        ops.docker_client.compose.get_all_services_status.return_value = self._statuses(
            ("frappe", "running", "c-frappe"), ("nginx", "running", "c-nginx")
        )

        assert ops.is_running() is True
        ops.compose_file_manager.get_services_list.assert_called_once_with(exclude_disabled=True)

    @pytest.mark.timeout(15)
    def test_is_running_is_false_when_one_expected_service_is_down(self, tmp_path):
        ops = _ops(tmp_path)
        ops.compose_file_manager.get_services_list.return_value = ["frappe", "nginx"]
        ops.compose_file_manager.get_container_names.return_value = {"frappe": "c-frappe", "nginx": "c-nginx"}
        ops.docker_client.compose.get_all_services_status.return_value = self._statuses(
            ("frappe", "running", "c-frappe"), ("nginx", "exited", "c-nginx")
        )

        assert ops.is_running() is False

    @pytest.mark.timeout(15)
    def test_is_running_ignores_a_container_that_is_not_this_benchs(self, tmp_path):
        """Statuses are matched on container Name, so another bench's running
        `nginx` cannot make this bench look up."""
        ops = _ops(tmp_path)
        ops.compose_file_manager.get_services_list.return_value = ["nginx"]
        ops.compose_file_manager.get_container_names.return_value = {"nginx": "c-nginx"}
        ops.docker_client.compose.get_all_services_status.return_value = self._statuses(
            ("nginx", "running", "someone-elses-nginx")
        )

        assert ops.is_running() is False

    @pytest.mark.timeout(15)
    def test_is_running_swallows_a_dead_docker_daemon(self, tmp_path):
        ops = _ops(tmp_path)
        ops.compose_file_manager.get_services_list.side_effect = _docker_exception("no daemon")

        assert ops.is_running() is False

    @pytest.mark.timeout(15)
    def test_running_status_map_drops_leftovers_of_disabled_services(self, tmp_path):
        """A container left behind from before redis was disabled must not turn up
        in the status of a bench doing exactly what it was configured to."""
        ops = _ops(tmp_path)
        ops.compose_file_manager.get_services_list.return_value = ["frappe"]
        ops.compose_file_manager.get_container_names.return_value = {
            "frappe": "c-frappe",
            "redis-cache": "c-redis",
        }
        ops.docker_client.compose.get_all_services_status.return_value = self._statuses(
            ("frappe", "running", "c-frappe"), ("redis-cache", "running", "c-redis")
        )

        assert ops.get_services_running_status() == {"frappe": "running"}

    @pytest.mark.timeout(15)
    def test_running_status_map_is_empty_when_docker_is_unreachable(self, tmp_path):
        ops = _ops(tmp_path)
        ops.docker_client.compose.get_all_services_status.side_effect = _docker_exception()

        assert ops.get_services_running_status() == {}

    @pytest.mark.timeout(15)
    def test_single_service_check_ignores_container_names(self, tmp_path):
        """SUSPICION pinned, not fixed: unlike `is_running`, the per-service check
        does not filter on container Name, so any running service of that name
        satisfies it."""
        ops = _ops(tmp_path)
        ops.docker_client.compose.get_all_services_status.return_value = self._statuses(
            ("frappe", "running", "a-totally-different-container")
        )

        assert ops._is_service_running("frappe") is True

    @pytest.mark.timeout(15)
    def test_single_service_check_is_false_when_docker_is_unreachable(self, tmp_path):
        ops = _ops(tmp_path)
        ops.docker_client.compose.get_all_services_status.side_effect = _docker_exception()

        assert ops._is_service_running("frappe") is False


class TestCreateComposeDirs:
    """The host skeleton. Getting this wrong means nginx has no config, bench has
    no `apps.txt`, or a multi-gigabyte runtime is re-extracted on every run."""

    @staticmethod
    def _seed_recorder(ops):
        calls = []
        ops._seed_nginx_conf = lambda conf_dir, image: calls.append((conf_dir, image))
        return calls

    @pytest.mark.timeout(15)
    def test_mount_runtime_gets_a_host_apps_directory(self, tmp_path, monkeypatch):
        ops = _ops(tmp_path, runtime=BenchRuntime.mount)
        self._seed_recorder(ops)
        monkeypatch.setattr(f"{DOCKER_MODULE}.host_run_cp", MagicMock())

        assert ops.create_compose_dirs() is True
        bench = tmp_path / "workspace" / "frappe-bench"
        assert (bench / "apps").is_dir()
        for sub in ("sites", "logs", "config", "config/pids"):
            assert (bench / sub).is_dir(), sub

    @pytest.mark.timeout(15)
    def test_image_runtime_has_no_host_apps_directory(self, tmp_path, monkeypatch):
        """Image runtime ships app code inside the image; a host `apps/` would be
        an empty directory bind-mounted over the image's code."""
        ops = _ops(tmp_path, runtime=BenchRuntime.image)
        self._seed_recorder(ops)
        monkeypatch.setattr(f"{DOCKER_MODULE}.host_run_cp", MagicMock())

        ops.create_compose_dirs(copy_runtimes=False)

        bench = tmp_path / "workspace" / "frappe-bench"
        assert not (bench / "apps").exists()
        assert (bench / "sites").is_dir()

    @pytest.mark.timeout(15)
    def test_skeleton_files_are_seeded_with_the_minimum_bench_expects(self, tmp_path, monkeypatch):
        ops = _ops(tmp_path)
        self._seed_recorder(ops)
        monkeypatch.setattr(f"{DOCKER_MODULE}.host_run_cp", MagicMock())

        ops.create_compose_dirs(copy_runtimes=False)

        sites = tmp_path / "workspace" / "frappe-bench" / "sites"
        assert (sites / "apps.txt").read_text() == "frappe\n"
        assert (sites / "common_site_config.json").read_text() == "{}"

    @pytest.mark.timeout(15)
    def test_existing_skeleton_files_are_never_clobbered(self, tmp_path, monkeypatch):
        """Re-running create (update, migrate) must not wipe the installed app list
        or the site config of a live bench."""
        sites = tmp_path / "workspace" / "frappe-bench" / "sites"
        sites.mkdir(parents=True)
        (sites / "apps.txt").write_text("frappe\nerpnext\n")
        (sites / "common_site_config.json").write_text('{"db_host": "mariadb"}')

        ops = _ops(tmp_path)
        self._seed_recorder(ops)
        monkeypatch.setattr(f"{DOCKER_MODULE}.host_run_cp", MagicMock())

        ops.create_compose_dirs(copy_runtimes=False)

        assert (sites / "apps.txt").read_text() == "frappe\nerpnext\n"
        assert (sites / "common_site_config.json").read_text() == '{"db_host": "mariadb"}'

    @pytest.mark.timeout(15)
    def test_nginx_seeding_is_pinned_to_the_compose_files_nginx_image(self, tmp_path, monkeypatch):
        """The config has to come from the image the bench will actually run, not
        from whatever `nginx:latest` happens to be on the host."""
        ops = _ops(
            tmp_path,
            yml={"services": {"nginx": {"image": "fm-nginx:0.42.0"}, "frappe": {"image": "frappe:pinned"}}},
        )
        seeded = self._seed_recorder(ops)
        monkeypatch.setattr(f"{DOCKER_MODULE}.host_run_cp", MagicMock())

        ops.create_compose_dirs(copy_runtimes=False)

        assert seeded == [(tmp_path / "configs" / "nginx" / "conf", "fm-nginx:0.42.0")]

    @pytest.mark.timeout(15)
    def test_nginx_seeding_is_skipped_once_the_marker_conf_is_present(self, tmp_path, monkeypatch):
        """Complements `test_nginx_conf_seeding.py` (which pins what the merge does):
        here the guard itself, i.e. `create_compose_dirs` keys off `conf/nginx.conf`
        and not off the directory."""
        conf = tmp_path / "configs" / "nginx" / "conf"
        conf.mkdir(parents=True)
        (conf / "nginx.conf").write_text("events {}\n")

        ops = _ops(tmp_path)
        seeded = self._seed_recorder(ops)
        monkeypatch.setattr(f"{DOCKER_MODULE}.host_run_cp", MagicMock())

        ops.create_compose_dirs(copy_runtimes=False)

        assert seeded == []

    @pytest.mark.timeout(15)
    def test_nginx_runtime_subdirectories_are_always_created(self, tmp_path, monkeypatch):
        """nginx cannot write its logs/cache/pid into a directory that the bind
        mount did not bring with it."""
        ops = _ops(tmp_path)
        self._seed_recorder(ops)
        monkeypatch.setattr(f"{DOCKER_MODULE}.host_run_cp", MagicMock())

        ops.create_compose_dirs(copy_runtimes=False)

        nginx_dir = tmp_path / "configs" / "nginx"
        assert sorted(p.name for p in nginx_dir.iterdir()) == ["cache", "html", "logs", "run"]

    @pytest.mark.timeout(15)
    def test_prebaked_runtimes_are_extracted_from_the_frappe_service_image(self, tmp_path, monkeypatch):
        ops = _ops(tmp_path, yml={"services": {"nginx": {"image": "n:1"}, "frappe": {"image": "frappe:0.9"}}})
        self._seed_recorder(ops)
        cp = MagicMock()
        monkeypatch.setattr(f"{DOCKER_MODULE}.host_run_cp", cp)

        ops.create_compose_dirs(copy_runtimes=True)

        bench = tmp_path / "workspace" / "frappe-bench"
        assert cp.call_args_list == [
            call(
                "frappe:0.9",
                source="/workspace/frappe-bench/.uv",
                destination=str((bench / ".uv").absolute()),
                docker=ops.docker_client,
            ),
            call(
                "frappe:0.9",
                source="/workspace/frappe-bench/.fnm",
                destination=str((bench / ".fnm").absolute()),
                docker=ops.docker_client,
            ),
        ]

    @pytest.mark.timeout(15)
    def test_prebaked_runtimes_already_on_the_host_are_not_re_extracted(self, tmp_path, monkeypatch):
        """`.uv` alone is hundreds of megabytes; the extraction is guarded per
        directory, so a half-done bench still gets the missing one."""
        bench = tmp_path / "workspace" / "frappe-bench"
        (bench / ".uv").mkdir(parents=True)

        ops = _ops(tmp_path)
        self._seed_recorder(ops)
        cp = MagicMock()
        monkeypatch.setattr(f"{DOCKER_MODULE}.host_run_cp", cp)

        ops.create_compose_dirs(copy_runtimes=True)

        assert [c.kwargs["source"] for c in cp.call_args_list] == ["/workspace/frappe-bench/.fnm"]

    @pytest.mark.timeout(15)
    def test_copy_runtimes_false_skips_both_extractions(self, tmp_path, monkeypatch):
        """Image runtime keeps `.uv`/`.fnm` in the app image, so the caller opts out
        of the extraction with the flag -- it is the flag, not the runtime, that decides."""
        ops = _ops(tmp_path, runtime=BenchRuntime.image)
        self._seed_recorder(ops)
        cp = MagicMock()
        monkeypatch.setattr(f"{DOCKER_MODULE}.host_run_cp", cp)

        ops.create_compose_dirs(copy_runtimes=False)

        cp.assert_not_called()

    @pytest.mark.timeout(15)
    def test_image_runtime_still_extracts_when_the_caller_asks_for_it(self, tmp_path, monkeypatch):
        ops = _ops(tmp_path, runtime=BenchRuntime.image)
        self._seed_recorder(ops)
        cp = MagicMock()
        monkeypatch.setattr(f"{DOCKER_MODULE}.host_run_cp", cp)

        ops.create_compose_dirs(copy_runtimes=True)

        assert cp.call_count == 2


class TestGenerateCompose:
    """What `generate_compose` decides before handing the compose file to
    `configure_bench` and the mode projection."""

    @staticmethod
    def _patch_shape(monkeypatch):
        specs = (ServiceSpec(name="frappe", image=None, managed_binds=()),)
        apply_specs = MagicMock()
        monkeypatch.setattr(f"{SHAPE_MODULE}.bench_service_specs", MagicMock(return_value=specs))
        monkeypatch.setattr(f"{SHAPE_MODULE}.apply_specs", apply_specs)
        return specs, apply_specs

    @pytest.mark.timeout(15)
    def test_user_input_is_reshaped_into_uid_gid_pairs(self, tmp_path, monkeypatch):
        ops = _ops(tmp_path)
        self._patch_shape(monkeypatch)
        monkeypatch.setattr(f"{DOCKER_MODULE}.get_proxy_ip_on_frontend", lambda: None)

        ops.generate_compose({"user": {"frappe": {"uid": 1000, "gid": 1001}}})

        assert ops.compose_file_manager.configure_bench.call_args.kwargs["users"] == {"frappe": (1000, 1001)}

    @pytest.mark.timeout(15)
    def test_no_user_input_means_no_user_remapping(self, tmp_path, monkeypatch):
        ops = _ops(tmp_path)
        self._patch_shape(monkeypatch)
        monkeypatch.setattr(f"{DOCKER_MODULE}.get_proxy_ip_on_frontend", lambda: None)

        ops.generate_compose({"environment": {"frappe": {"A": "b"}}, "labels": {"frappe": {"l": "1"}}})

        kwargs = ops.compose_file_manager.configure_bench.call_args.kwargs
        assert kwargs["users"] is None
        assert kwargs["envs"] == {"frappe": {"A": "b"}}
        assert kwargs["labels"] == {"frappe": {"l": "1"}}
        assert kwargs["network_name"] == "site-network"
        # auto_save=False: the single write_to_file below is the only disk write.
        assert kwargs["auto_save"] is False
        ops.compose_file_manager.write_to_file.assert_called_once_with()

    @pytest.mark.timeout(15)
    def test_prefix_is_derived_from_the_bench_name_not_from_a_domain(self, tmp_path, monkeypatch):
        """The container-name prefix is BENCH-scoped: it names the containers, and the
        leftover-container cleanup, admin tools, the database config and the workers compose all
        build it from the bench name. So this bench is `shop` while its site is `shop.localhost`,
        which is the only shape that tells the two sources apart -- deriving the prefix from
        `config.domains[0]` instead would write `fm__shop_localhost` here and leave every other
        caller looking for `fm__shop`.
        """
        ops = _ops(
            tmp_path,
            name="shop",
            site="shop.localhost",
            alias_domains=["www.shop.example.com"],
        )
        self._patch_shape(monkeypatch)
        monkeypatch.setattr(f"{DOCKER_MODULE}.get_proxy_ip_on_frontend", lambda: None)

        ops.generate_compose({})

        assert ops.compose_file_manager.configure_bench.call_args.kwargs["prefix"] == "fm__shop"

    @pytest.mark.timeout(15)
    def test_every_domain_resolves_to_the_proxy_inside_the_containers(self, tmp_path, monkeypatch):
        """The bench's own containers must reach their own site through the global
        proxy (so SSL/vhost routing applies); aliases included."""
        ops = _ops(tmp_path, name="a.localhost", alias_domains=["b.localhost", "c.localhost"])
        self._patch_shape(monkeypatch)
        monkeypatch.setattr(f"{DOCKER_MODULE}.get_proxy_ip_on_frontend", lambda: "10.5.0.9")

        ops.generate_compose({})

        expected = ["a.localhost:10.5.0.9", "b.localhost:10.5.0.9", "c.localhost:10.5.0.9"]
        assert ops.compose_file_manager.set_extrahosts.call_args_list == [
            call("frappe", expected),
            call("socketio", expected),
            call("schedule", expected),
        ]

    @pytest.mark.timeout(15)
    def test_no_extra_hosts_are_written_when_the_proxy_ip_is_unknown(self, tmp_path, monkeypatch):
        """Pinning a stale/empty IP would be worse than leaving DNS alone."""
        ops = _ops(tmp_path)
        self._patch_shape(monkeypatch)
        monkeypatch.setattr(f"{DOCKER_MODULE}.get_proxy_ip_on_frontend", lambda: None)

        ops.generate_compose({})

        ops.compose_file_manager.set_extrahosts.assert_not_called()

    @pytest.mark.timeout(15)
    def test_restart_policy_defaults_to_no(self, tmp_path, monkeypatch):
        ops = _ops(tmp_path)
        self._patch_shape(monkeypatch)
        monkeypatch.setattr(f"{DOCKER_MODULE}.get_proxy_ip_on_frontend", lambda: None)

        ops.generate_compose({})

        ops.compose_file_manager.set_all_services_restart.assert_called_once_with("no")

    @pytest.mark.timeout(15)
    def test_restart_policy_from_inputs_is_applied_to_all_services(self, tmp_path, monkeypatch):
        ops = _ops(tmp_path)
        self._patch_shape(monkeypatch)
        monkeypatch.setattr(f"{DOCKER_MODULE}.get_proxy_ip_on_frontend", lambda: None)

        ops.generate_compose({"restart_policy": "always"})

        ops.compose_file_manager.set_all_services_restart.assert_called_once_with("always")

    @staticmethod
    def _dev_ca(tmp_path) -> Path:
        ca = tmp_path / "services" / "nginx-proxy" / "ssl" / "dev" / "ca" / "rootCA.pem"
        ca.parent.mkdir(parents=True)
        ca.write_text("-----BEGIN CERTIFICATE-----\n")
        return ca

    @pytest.mark.timeout(15)
    def test_dev_ssl_mounts_the_local_ca_into_the_outbound_services(self, tmp_path, monkeypatch):
        """Self-signed dev certs are not in any trust store, so the containers that
        make outbound HTTPS calls to the site get the CA and the two env vars that
        python-requests and node honour."""
        ca = self._dev_ca(tmp_path)
        cert = SimpleNamespace(ssl_type=SUPPORTED_SSL_TYPES.dev)
        ops = _ops(tmp_path, ssl_certificates=[cert])
        self._patch_shape(monkeypatch)
        monkeypatch.setattr(f"{DOCKER_MODULE}.get_proxy_ip_on_frontend", lambda: None)
        monkeypatch.setattr(f"{DOCKER_MODULE}.CLI_SERVICES_DIRECTORY", tmp_path / "services")
        ops.compose_file_manager.get_service_volumes.return_value = []
        ops.compose_file_manager.get_envs.return_value = None

        ops.generate_compose({})

        assert [c.args[0] for c in ops.compose_file_manager.set_service_volumes.call_args_list] == [
            "frappe",
            "socketio",
            "schedule",
        ]
        mount = ops.compose_file_manager.set_service_volumes.call_args_list[0].args[1][0]
        assert Path(mount.host) == ca
        assert str(mount.container) == "/etc/ssl/certs/fm-dev-ca.pem"
        for c in ops.compose_file_manager.set_envs.call_args_list:
            assert c.args[1] == {
                "NODE_EXTRA_CA_CERTS": "/etc/ssl/certs/fm-dev-ca.pem",
                "REQUESTS_CA_BUNDLE": "/etc/ssl/certs/fm-dev-ca.pem",
            }
            assert c.kwargs["append"] is True

    @pytest.mark.timeout(15)
    def test_letsencrypt_ssl_gets_no_ca_mount(self, tmp_path, monkeypatch):
        """Production certs chain to a public root that containers already trust."""
        self._dev_ca(tmp_path)
        cert = SimpleNamespace(ssl_type=SUPPORTED_SSL_TYPES.le)
        ops = _ops(tmp_path, ssl_certificates=[cert])
        self._patch_shape(monkeypatch)
        monkeypatch.setattr(f"{DOCKER_MODULE}.get_proxy_ip_on_frontend", lambda: None)
        monkeypatch.setattr(f"{DOCKER_MODULE}.CLI_SERVICES_DIRECTORY", tmp_path / "services")

        ops.generate_compose({})

        ops.compose_file_manager.set_service_volumes.assert_not_called()
        ops.compose_file_manager.set_envs.assert_not_called()

    @pytest.mark.timeout(15)
    def test_dev_ssl_without_a_generated_ca_file_mounts_nothing(self, tmp_path, monkeypatch):
        """Mounting a non-existent host path would make docker create a directory
        there and every outbound HTTPS call would fail on an unreadable bundle."""
        cert = SimpleNamespace(ssl_type=SUPPORTED_SSL_TYPES.dev)
        ops = _ops(tmp_path, ssl_certificates=[cert])
        self._patch_shape(monkeypatch)
        monkeypatch.setattr(f"{DOCKER_MODULE}.get_proxy_ip_on_frontend", lambda: None)
        monkeypatch.setattr(f"{DOCKER_MODULE}.CLI_SERVICES_DIRECTORY", tmp_path / "nowhere")

        ops.generate_compose({})

        ops.compose_file_manager.set_service_volumes.assert_not_called()

    @pytest.mark.timeout(15)
    def test_mode_shape_is_projected_from_the_bench_config(self, tmp_path, monkeypatch):
        """Image/volume shape is a pure projection so create/update/deploy all
        produce the identical compose."""
        ops = _ops(tmp_path, name="proj.localhost")
        specs, apply_specs = self._patch_shape(monkeypatch)
        monkeypatch.setattr(f"{DOCKER_MODULE}.get_proxy_ip_on_frontend", lambda: None)

        ops.generate_compose({})

        apply_specs.assert_called_once_with(ops.compose_file_manager, specs, "proj.localhost")


class TestRenderImageCompose:
    @staticmethod
    def _patch_shape(monkeypatch, specs):
        monkeypatch.setattr(f"{SHAPE_MODULE}.bench_service_specs", MagicMock(return_value=specs))
        monkeypatch.setattr(f"{SHAPE_MODULE}.apply_specs", MagicMock())

    @pytest.mark.timeout(15)
    def test_mount_runtime_is_refused(self, tmp_path):
        ops = _ops(tmp_path, runtime=BenchRuntime.mount)

        with pytest.raises(ValueError, match="only valid for image runtime"):
            ops.render_image_compose("repo/app:v1")

    @pytest.mark.timeout(15)
    def test_returns_the_paired_nginx_assets_tag(self, tmp_path, monkeypatch):
        ops = _ops(tmp_path, runtime=BenchRuntime.image)
        self._patch_shape(monkeypatch, ())
        ops.compose_file_manager.get_services_list.return_value = []

        assert ops.render_image_compose("ghcr.io/x/app:v1.2") == "ghcr.io/x/app-nginx:v1.2"

    @pytest.mark.timeout(15)
    def test_the_candidate_tag_is_projected_without_touching_deploy_state(self, tmp_path, monkeypatch):
        ops = _ops(tmp_path, runtime=BenchRuntime.image)
        specs_fn = MagicMock(return_value=())
        monkeypatch.setattr(f"{SHAPE_MODULE}.bench_service_specs", specs_fn)
        monkeypatch.setattr(f"{SHAPE_MODULE}.apply_specs", MagicMock())
        ops.compose_file_manager.get_services_list.return_value = []

        ops.render_image_compose("repo/app:v9", rolling=True)

        ctx = specs_fn.call_args.args[1]
        assert (ctx.deploy_tag, ctx.rolling) == ("repo/app:v9", True)

    @pytest.mark.timeout(15)
    def test_rolling_render_sheds_container_name_on_scaled_services(self, tmp_path, monkeypatch):
        """`compose up --scale svc=2` is rejected while the service pins a
        container_name, so the rolling render drops it."""
        specs = (
            ServiceSpec(name="frappe", image="repo:v1", managed_binds=(), rolling=True),
            ServiceSpec(name="schedule", image="repo:v1", managed_binds=(), rolling=False),
        )
        ops = _ops(tmp_path, runtime=BenchRuntime.image)
        self._patch_shape(monkeypatch, specs)
        ops.compose_file_manager.get_services_list.return_value = ["frappe", "schedule"]

        ops.render_image_compose("repo:v1", rolling=True)

        ops.compose_file_manager.remove_container_name.assert_called_once_with("frappe")
        ops.compose_file_manager.set_container_name.assert_not_called()

    @pytest.mark.timeout(15)
    def test_canonical_render_restores_the_container_name(self, tmp_path, monkeypatch):
        """`get_container_names()` has to keep working between deploys."""
        specs = (ServiceSpec(name="frappe", image="repo:v1", managed_binds=(), rolling=True),)
        ops = _ops(tmp_path, runtime=BenchRuntime.image, name="img.localhost")
        self._patch_shape(monkeypatch, specs)
        ops.compose_file_manager.get_services_list.return_value = ["frappe"]

        ops.render_image_compose("repo:v1", rolling=False)

        ops.compose_file_manager.set_container_name.assert_called_once_with("frappe", "fm__img_localhost__frappe")
        ops.compose_file_manager.remove_container_name.assert_not_called()

    @pytest.mark.timeout(15)
    def test_a_spec_absent_from_the_compose_file_is_left_alone(self, tmp_path, monkeypatch):
        specs = (ServiceSpec(name="frappe", image="repo:v1", managed_binds=(), rolling=True),)
        ops = _ops(tmp_path, runtime=BenchRuntime.image)
        self._patch_shape(monkeypatch, specs)
        ops.compose_file_manager.get_services_list.return_value = ["schedule"]

        ops.render_image_compose("repo:v1", rolling=True)

        ops.compose_file_manager.remove_container_name.assert_not_called()
        ops.compose_file_manager.write_to_file.assert_called_once_with()


class TestConstruction:
    @pytest.mark.timeout(15)
    def test_an_explicit_output_handler_is_kept(self, tmp_path):
        handler = MagicMock()
        ops = BenchDockerOps(
            docker_client=MagicMock(),
            compose_file_manager=MagicMock(),
            config=MagicMock(),
            path=tmp_path,
            output_handler=handler,
        )

        assert ops.output is handler

    @pytest.mark.timeout(15)
    def test_without_a_handler_it_falls_back_to_rich_output(self, tmp_path):
        """Every call site prints; a None handler would be an AttributeError deep in
        a docker operation."""
        from frappe_manager.output_manager.rich_output import RichOutputHandler

        ops = BenchDockerOps(
            docker_client=MagicMock(),
            compose_file_manager=MagicMock(),
            config=MagicMock(),
            path=tmp_path,
        )

        assert isinstance(ops.output, RichOutputHandler)


class TestStartStopArguments:
    @pytest.mark.timeout(15)
    def test_start_defaults_to_all_services_detached_and_never_pulls(self, tmp_path):
        """A pull on every start would break an offline host and silently move the
        pinned images."""
        ops = _ops(tmp_path)

        ops.start()

        ops.docker_client.compose.up.assert_called_once_with(
            services=[], detach=True, pull="never", force_recreate=False
        )

    @pytest.mark.timeout(15)
    def test_start_passes_the_requested_services_and_policies_through(self, tmp_path):
        ops = _ops(tmp_path)

        ops.start(services=["frappe"], force_recreate=True, pull="always")

        ops.docker_client.compose.up.assert_called_once_with(
            services=["frappe"], detach=True, pull="always", force_recreate=True
        )

    @pytest.mark.timeout(15)
    def test_stop_addresses_the_whole_project_with_the_given_timeout(self, tmp_path):
        ops = _ops(tmp_path)

        ops.stop(timeout=25)

        ops.docker_client.compose.stop.assert_called_once_with(services=[], timeout=25)


class TestExecCommandPassthrough:
    @pytest.mark.timeout(15)
    def test_no_user_means_no_user_argument(self, tmp_path):
        """Passing user=None explicitly would override the image's own USER."""
        ops = _ops(tmp_path)

        ops.exec_command("nginx", "nginx -t")

        ops.docker_client.compose.exec.assert_called_once_with(service="nginx", command="nginx -t", stream=False)

    @pytest.mark.timeout(15)
    def test_a_user_and_streaming_are_forwarded(self, tmp_path):
        ops = _ops(tmp_path)

        ops.exec_command("frappe", "bench version", user="frappe", stream=True)

        ops.docker_client.compose.exec.assert_called_once_with(
            service="frappe", command="bench version", stream=True, user="frappe"
        )


class TestExecuteCommandOutputAndExitCodes:
    @staticmethod
    def _ops_with_running_frappe(tmp_path):
        ops = _ops(tmp_path)
        ops.docker_client.compose.get_all_services_status.return_value = [
            {"Service": "frappe", "State": "running", "Name": "c-frappe"}
        ]
        return ops

    @pytest.mark.timeout(15)
    def test_captured_output_is_replayed_on_the_right_streams(self, tmp_path, capsys):
        ops = self._ops_with_running_frappe(tmp_path)
        ops.docker_client.compose.exec.return_value = SimpleNamespace(stdout=["out-1"], stderr=["err-1"], exit_code=0)

        assert ops.execute_command("frappe", "true") == 0

        captured = capsys.readouterr()
        assert captured.out.splitlines() == ["out-1"]
        assert captured.err.splitlines() == ["err-1"]

    @pytest.mark.timeout(15)
    def test_a_failing_command_propagates_the_container_exit_code(self, tmp_path, capsys):
        """`fm shell -c` is used in scripts; swallowing the exit code would make
        every failure look like success."""
        ops = self._ops_with_running_frappe(tmp_path)
        ops.docker_client.compose.exec.side_effect = DockerException(
            ["docker"], SubprocessOutput(["partial"], ["boom"], ["partial", "boom"], 42)
        )

        assert ops.execute_command("frappe", "false") == 42

        captured = capsys.readouterr()
        assert captured.out.splitlines() == ["partial"]
        assert captured.err.splitlines() == ["boom"]

    @pytest.mark.timeout(15)
    def test_run_mode_replays_output_on_success(self, tmp_path, capsys):
        ops = self._ops_with_running_frappe(tmp_path)
        ops.docker_client.compose.run.return_value = SimpleNamespace(
            stdout=["run-out"], stderr=["run-err"], exit_code=0
        )

        assert ops.execute_command("frappe", "true", use_run=True) == 0

        captured = capsys.readouterr()
        assert captured.out.splitlines() == ["run-out"]
        assert captured.err.splitlines() == ["run-err"]

    @pytest.mark.timeout(15)
    def test_run_mode_also_propagates_the_exit_code_and_output(self, tmp_path, capsys):
        ops = self._ops_with_running_frappe(tmp_path)
        ops.docker_client.compose.run.side_effect = DockerException(
            ["docker"], SubprocessOutput(["r-out"], ["r-err"], ["r-out", "r-err"], 7)
        )

        assert ops.execute_command("frappe", "false", use_run=True) == 7

        captured = capsys.readouterr()
        assert captured.out.splitlines() == ["r-out"]
        assert captured.err.splitlines() == ["r-err"]

    @pytest.mark.timeout(15)
    def test_run_mode_lands_in_the_bench_directory_like_exec_does(self, tmp_path):
        """`fm shell --run -c 'bench ...'` used to run from /workspace (the image's
        WORKDIR) because only the exec branch passed --workdir, so every bench
        command failed on a mount-runtime bench."""
        ops = self._ops_with_running_frappe(tmp_path)
        ops.docker_client.compose.run.return_value = SimpleNamespace(stdout=[], stderr=[], exit_code=0)

        ops.execute_command("frappe", "bench version", use_run=True)

        assert ops.docker_client.compose.run.call_args.kwargs["workdir"] == "/workspace/frappe-bench"

    @pytest.mark.timeout(15)
    def test_run_mode_on_a_non_frappe_service_gets_no_bench_workdir(self, tmp_path):
        ops = self._ops_with_running_frappe(tmp_path)
        ops.docker_client.compose.run.return_value = SimpleNamespace(stdout=[], stderr=[], exit_code=0)

        ops.execute_command("mailpit", "true", use_run=True)

        assert "workdir" not in ops.docker_client.compose.run.call_args.kwargs

    @pytest.mark.timeout(15)
    def test_a_site_reaches_the_exec_branch_as_frappe_site(self, tmp_path):
        """`fm shell BENCH/SITE -c 'bench migrate'` is the scripted form of the address, and
        it goes through `execute_command`, not `shell`. Wiring only the interactive path
        would leave every scripted invocation silently targeting the bench-wide default."""
        ops = self._ops_with_running_frappe(tmp_path)
        ops.docker_client.compose.exec.return_value = SimpleNamespace(stdout=[], stderr=[], exit_code=0)

        ops.execute_command("frappe", "bench migrate", site="a.localhost")

        assert ops.docker_client.compose.exec.call_args.kwargs["env"] == ["FRAPPE_SITE=a.localhost"]

    @pytest.mark.timeout(15)
    def test_a_site_reaches_the_run_branch_too(self, tmp_path):
        ops = self._ops_with_running_frappe(tmp_path)
        ops.docker_client.compose.run.return_value = SimpleNamespace(stdout=[], stderr=[], exit_code=0)

        ops.execute_command("frappe", "bench migrate", use_run=True, site="a.localhost")

        assert ops.docker_client.compose.run.call_args.kwargs["env"] == ["FRAPPE_SITE=a.localhost"]

    @pytest.mark.timeout(15)
    def test_no_site_passes_no_env_at_all(self, tmp_path):
        """An empty `env` list would still be a behaviour change for every existing caller."""
        ops = self._ops_with_running_frappe(tmp_path)
        ops.docker_client.compose.exec.return_value = SimpleNamespace(stdout=[], stderr=[], exit_code=0)

        ops.execute_command("frappe", "true")

        assert "env" not in ops.docker_client.compose.exec.call_args.kwargs


class TestFrappeLogsTillStart:
    @pytest.mark.timeout(15)
    def test_it_follows_the_frappe_logs_until_supervisord_announces_itself(self, tmp_path):
        """`fm create` blocks on this; the stop string is the only thing that ends it."""
        ops = _ops(tmp_path)

        ops.frappe_logs_till_start()

        ops.docker_client.compose.logs.assert_called_once_with(
            services=["frappe"], no_log_prefix=True, no_color=True, follow=True, stream=True
        )
        assert ops.output.live_lines.call_args.kwargs["stop_string"] == "INFO supervisord started with pid"


class TestRestartServices:
    @pytest.mark.timeout(15)
    def test_disabled_services_are_dropped_before_the_restart(self, tmp_path):
        """docker compose cannot address a service outside the active profiles."""
        ops = _ops(tmp_path)
        ops.compose_file_manager.is_service_profile_disabled.side_effect = lambda s: s == "redis-cache"

        ops.restart_services(["frappe", "redis-cache"])

        ops.docker_client.compose.restart.assert_called_once_with(services=["frappe"], timeout=100)

    @pytest.mark.timeout(15)
    def test_a_fully_disabled_request_is_a_no_op(self, tmp_path):
        ops = _ops(tmp_path)
        ops.compose_file_manager.is_service_profile_disabled.return_value = True

        ops.restart_services(["redis-cache", "redis-queue"])

        ops.docker_client.compose.restart.assert_not_called()
        ops.output.change_head.assert_not_called()

    @pytest.mark.timeout(15)
    def test_force_restart_kills_immediately_and_says_so(self, tmp_path):
        ops = _ops(tmp_path)
        ops.compose_file_manager.is_service_profile_disabled.return_value = False

        ops.restart_services(["frappe", "nginx"], force=True)

        ops.docker_client.compose.restart.assert_called_once_with(services=["frappe", "nginx"], timeout=0)
        ops.output.print.assert_called_once_with("Force restarted services - frappe nginx")

    @pytest.mark.timeout(15)
    def test_graceful_restart_reports_the_enabled_services_only(self, tmp_path):
        ops = _ops(tmp_path)
        ops.compose_file_manager.is_service_profile_disabled.side_effect = lambda s: s == "redis-cache"

        ops.restart_services(["frappe", "redis-cache"])

        ops.output.print.assert_called_once_with("Restarted services - frappe")


class TestShellArgv:
    @staticmethod
    def _capture_execvp(monkeypatch):
        captured = []
        monkeypatch.setattr(os, "execvp", lambda f, a: captured.append((f, a)))
        return captured

    @staticmethod
    def _running(ops, service="frappe"):
        ops.docker_client.compose.get_all_services_status.return_value = [
            {"Service": service, "State": "running", "Name": f"c-{service}"}
        ]
        ops.docker_client.compose.docker_compose_cmd = ["docker", "compose"]

    @pytest.mark.timeout(15)
    def test_frappe_shell_runs_as_frappe_in_the_bench_directory(self, tmp_path, monkeypatch):
        ops = _ops(tmp_path)
        self._running(ops)
        captured = self._capture_execvp(monkeypatch)

        ops.shell("frappe")

        assert captured == [
            (
                "docker",
                [
                    "docker",
                    "compose",
                    "exec",
                    "--user",
                    "frappe",
                    "--workdir",
                    "/workspace/frappe-bench",
                    "frappe",
                    "/bin/bash",
                ],
            )
        ]

    @pytest.mark.timeout(15)
    def test_a_bash_less_service_falls_back_to_sh_and_no_user(self, tmp_path, monkeypatch):
        """redis/adminer images have no bash; forcing `/bin/bash` there just fails."""
        ops = _ops(tmp_path)
        self._running(ops, "redis-cache")
        captured = self._capture_execvp(monkeypatch)

        ops.shell("redis-cache")

        assert captured == [("docker", ["docker", "compose", "exec", "redis-cache", "sh"])]

    @pytest.mark.timeout(15)
    def test_an_explicit_shell_path_overrides_the_detection(self, tmp_path, monkeypatch):
        ops = _ops(tmp_path)
        self._running(ops)
        captured = self._capture_execvp(monkeypatch)

        ops.shell("frappe", user="root", shell_path="/bin/zsh")

        assert captured[0][1][-2:] == ["frappe", "/bin/zsh"]
        assert captured[0][1][captured[0][1].index("--user") + 1] == "root"

    @pytest.mark.timeout(15)
    def test_a_site_becomes_frappe_site_in_the_exec_environment(self, tmp_path, monkeypatch):
        """The payload of the `bench/site` address. FRAPPE_SITE sits above
        common_site_config's default_site in Frappe's own resolution, so this is what makes a
        bare `bench` command inside the shell target the site the operator addressed instead
        of the bench-wide default."""
        ops = _ops(tmp_path)
        self._running(ops)
        captured = self._capture_execvp(monkeypatch)

        ops.shell("frappe", site="a.localhost")

        argv = captured[0][1]
        assert "--env" in argv
        assert argv[argv.index("--env") + 1] == "FRAPPE_SITE=a.localhost"
        # Before the service and the shell path, or docker reads it as an argument to the
        # containerised command. `frappe` also appears earlier as the value of `--user`, so
        # the service is located by position (the trailing pair), not by searching for it.
        assert argv[-2:] == ["frappe", "/bin/bash"]
        assert argv.index("--env") < len(argv) - 2

    @pytest.mark.timeout(15)
    def test_no_site_means_no_env_flag_at_all(self, tmp_path, monkeypatch):
        """`fm shell BENCH` with no site part must behave exactly as it did before addresses."""
        ops = _ops(tmp_path)
        self._running(ops)
        captured = self._capture_execvp(monkeypatch)

        ops.shell("frappe")

        assert "--env" not in captured[0][1]

    @pytest.mark.timeout(15)
    def test_the_run_branch_carries_frappe_site_too(self, tmp_path, monkeypatch):
        """`--run` builds a separate argv through `compose run`; injecting into only one of
        the two branches is the easy mistake here."""
        ops = _ops(tmp_path)
        self._running(ops)
        captured = self._capture_execvp(monkeypatch)

        ops.shell("frappe", use_run=True, site="a.localhost")

        argv = captured[0][1]
        assert "run" in argv
        assert argv[argv.index("--env") + 1] == "FRAPPE_SITE=a.localhost"

    @pytest.mark.timeout(15)
    def test_shell_refuses_when_the_service_is_not_running(self, tmp_path, monkeypatch):
        ops = _ops(tmp_path)
        ops.docker_client.compose.get_all_services_status.return_value = [
            {"Service": "frappe", "State": "exited", "Name": "c-frappe"}
        ]
        captured = self._capture_execvp(monkeypatch)

        ops.shell("frappe")

        assert captured == []
        ops.output.display_error.assert_called_once_with("Cannot spawn shell. Compose service 'frappe' not running!")

    @pytest.mark.timeout(15)
    def test_run_mode_needs_no_running_container_and_uses_the_light_entrypoint(self, tmp_path, monkeypatch):
        """`fm shell --run` exists precisely for a bench that will not come up.

        The --workdir is not decoration: /exec-entrypoint.sh never cds and the image's
        WORKDIR is /workspace, one level above the bench, so a shell landing there has
        every `bench ...` fail. This pinned the missing flag before it was added."""
        ops = _ops(tmp_path)
        ops.docker_client.compose.get_all_services_status.return_value = []
        ops.docker_client.compose.docker_compose_cmd = ["docker", "compose"]
        captured = self._capture_execvp(monkeypatch)

        ops.shell("frappe", use_run=True)

        assert captured == [
            (
                "docker",
                [
                    "docker",
                    "compose",
                    "run",
                    "--rm",
                    "--entrypoint",
                    "/exec-entrypoint.sh",
                    "--workdir",
                    "/workspace/frappe-bench",
                    "frappe",
                    "/bin/bash",
                ],
            )
        ]

    @pytest.mark.timeout(15)
    def test_run_mode_on_a_non_frappe_service_gets_no_bench_workdir(self, tmp_path, monkeypatch):
        """Only the frappe image has /workspace/frappe-bench; --workdir elsewhere would
        make the container exit before running anything."""
        ops = _ops(tmp_path)
        ops.docker_client.compose.get_all_services_status.return_value = []
        ops.docker_client.compose.docker_compose_cmd = ["docker", "compose"]
        captured = self._capture_execvp(monkeypatch)

        ops.shell("redis-cache", use_run=True)

        assert "--workdir" not in captured[0][1]

    @pytest.mark.timeout(15)
    def test_the_live_display_is_stopped_before_handing_over_the_terminal(self, tmp_path, monkeypatch):
        """execvp replaces the process; a running rich Live would leave the terminal
        in a broken state."""
        ops = _ops(tmp_path)
        self._running(ops)
        self._capture_execvp(monkeypatch)

        ops.shell("frappe")

        ops.output.stop.assert_called_once_with()


class TestGuardsThatRefuseAnAction:
    @pytest.mark.timeout(15)
    def test_execute_command_returns_one_when_the_service_is_down(self, tmp_path):
        ops = _ops(tmp_path)
        ops.docker_client.compose.get_all_services_status.return_value = []

        assert ops.execute_command("frappe", "ls") == 1
        ops.docker_client.compose.exec.assert_not_called()

    @pytest.mark.timeout(15)
    def test_logs_refuses_for_a_service_that_is_not_running(self, tmp_path):
        """Raises rather than printing and returning: `fm logs BENCH --service X` exited 0 after
        saying it could show nothing, so a caller could not tell empty logs from a dead container."""
        ops = _ops(tmp_path)
        ops.docker_client.compose.get_all_services_status.return_value = []

        with pytest.raises(BenchServiceNotRunning, match="frappe"):
            ops.logs(services=["frappe"])

        ops.docker_client.compose.logs.assert_not_called()

    @pytest.mark.timeout(15)
    def test_logs_for_all_services_skips_the_running_check(self, tmp_path):
        ops = _ops(tmp_path)

        ops.logs()

        ops.docker_client.compose.logs.assert_called_once_with(services=[], follow=False, stream=True)

    @pytest.mark.timeout(15)
    def test_removing_containers_without_a_compose_file_warns_instead_of_failing(self, tmp_path):
        """`fm delete` on a half-created bench must still finish."""
        ops = _ops(tmp_path)
        ops.compose_file_manager.exists.return_value = False

        ops.remove_containers()

        ops.docker_client.compose.down.assert_not_called()
        ops.output.warning.assert_called_once_with("Bench compose file not found. Skipping containers removal.")

    @pytest.mark.timeout(15)
    def test_removing_containers_takes_volumes_down_with_orphans(self, tmp_path):
        ops = _ops(tmp_path)
        ops.compose_file_manager.exists.return_value = True

        ops.remove_containers(remove_volumes=False, timeout=7)

        ops.docker_client.compose.down.assert_called_once_with(
            remove_orphans=True, volumes=False, timeout=7, stream=True
        )


class TestRequiredImagesCheck:
    @staticmethod
    def _patch_fm_images(monkeypatch, images):
        monkeypatch.setattr("frappe_manager.utils.site.get_all_docker_images", lambda: images)

    @pytest.mark.timeout(15)
    def test_all_images_present_raises_nothing(self, tmp_path, monkeypatch):
        ops = _ops(tmp_path)
        self._patch_fm_images(monkeypatch, {"frappe": {"name": "fm/frappe", "tag": "v1"}})
        ops.docker_client.images.return_value = [{"Repository": "fm/frappe", "Tag": "v1"}]

        ops.check_required_docker_images_available()

        ops.output.display_error.assert_not_called()

    @pytest.mark.timeout(15)
    def test_a_matching_name_with_a_different_tag_counts_as_missing(self, tmp_path, monkeypatch):
        """Both repository and tag must match: fm pins exact tags per version."""
        from frappe_manager.site_manager.exceptions import BenchOperationRequiredDockerImagesNotAvailable

        ops = _ops(tmp_path)
        self._patch_fm_images(monkeypatch, {"frappe": {"name": "fm/frappe", "tag": "v2"}})
        ops.docker_client.images.return_value = [{"Repository": "fm/frappe", "Tag": "v1"}]

        with pytest.raises(BenchOperationRequiredDockerImagesNotAvailable):
            ops.check_required_docker_images_available()

        ops.output.display_error.assert_called_once_with("Docker image 'fm/frappe:v2' is not available locally")

    @pytest.mark.timeout(15)
    def test_the_same_missing_image_is_only_reported_once(self, tmp_path, monkeypatch):
        """Several services share one image; the operator should see one line."""
        from frappe_manager.site_manager.exceptions import BenchOperationRequiredDockerImagesNotAvailable

        ops = _ops(tmp_path)
        self._patch_fm_images(
            monkeypatch,
            {
                "frappe": {"name": "fm/frappe", "tag": "v1"},
                "schedule": {"name": "fm/frappe", "tag": "v1"},
                "nginx": {"name": "fm/nginx", "tag": "v1"},
            },
        )
        ops.docker_client.images.return_value = []

        with pytest.raises(BenchOperationRequiredDockerImagesNotAvailable):
            ops.check_required_docker_images_available()

        assert [c.args[0] for c in ops.output.display_error.call_args_list] == [
            "Docker image 'fm/frappe:v1' is not available locally",
            "Docker image 'fm/nginx:v1' is not available locally",
        ]


# ============================================================== BenchSupervisor


def _supervisor(*, newrelic_enabled=False, newrelic_license_key=None, bench_name="test.localhost") -> BenchSupervisor:
    sup = object.__new__(BenchSupervisor)
    sup.logger = MagicMock()
    sup.docker_client = MagicMock()
    sup.config = BenchConfig(
        name=bench_name,
        developer_mode=False,
        admin_tools=False,
        environment_type=FMBenchEnvType.dev,
        root_path=Path("/nonexistent/bench_config.toml"),
        monitoring=MonitoringConfig(newrelic=NewRelicConfig(enabled=newrelic_enabled, license_key=newrelic_license_key))
        if newrelic_enabled or newrelic_license_key
        else None,
    )
    sup.bench_name = bench_name
    sup.output = MagicMock()
    return sup


def _bench_path(tmp_path, common_site_config: str | None = None) -> Path:
    bench = tmp_path / "test.localhost"
    sites = bench / "workspace" / "frappe-bench" / "sites"
    sites.mkdir(parents=True)
    if common_site_config is not None:
        (sites / "common_site_config.json").write_text(common_site_config)
    return bench


def _programs(rendered: str) -> configparser.ConfigParser:
    cfg = configparser.ConfigParser(allow_no_value=True, strict=False, interpolation=None)
    cfg.read_string(rendered)
    return cfg


class TestGunicornSizing:
    """Oversizing gunicorn on a small box is how a bench OOMs; undersizing serialises
    requests. The sizing is derived, so pin the arithmetic and its floors."""

    @staticmethod
    def _patch(monkeypatch, cpus, ram_gb):
        monkeypatch.setattr(f"{SUPERVISOR_MODULE}.multiprocessing.cpu_count", lambda: cpus)
        import psutil

        monkeypatch.setattr(psutil, "virtual_memory", lambda: SimpleNamespace(total=int(ram_gb * 1024**3)))

    @pytest.mark.timeout(15)
    def test_cpu_bound_box_is_capped_by_cpu_count(self, monkeypatch):
        self._patch(monkeypatch, cpus=4, ram_gb=32)
        assert _supervisor()._get_gunicorn_workers() == 4

    @pytest.mark.timeout(15)
    def test_ram_bound_box_is_capped_at_256mb_per_worker(self, monkeypatch):
        """32 CPUs but 2 GiB of RAM: 2*1024/256 = 8 workers, not 32."""
        self._patch(monkeypatch, cpus=32, ram_gb=2)
        assert _supervisor()._get_gunicorn_workers() == 8

    @pytest.mark.timeout(15)
    def test_a_tiny_box_still_gets_one_worker(self, monkeypatch):
        """Under 256 MiB the arithmetic yields 0; a bench with no web worker serves
        nothing, so the floor is 1."""
        self._patch(monkeypatch, cpus=8, ram_gb=0.1)
        assert _supervisor()._get_gunicorn_workers() == 1

    @pytest.mark.timeout(15)
    def test_threads_floor_at_two_on_a_single_cpu(self, monkeypatch):
        """gthread with one thread is a sync worker; frappe is IO-bound."""
        monkeypatch.setattr(f"{SUPERVISOR_MODULE}.multiprocessing.cpu_count", lambda: 1)
        assert _supervisor()._get_gunicorn_threads() == 2

    @pytest.mark.timeout(15)
    def test_threads_ceiling_at_four_on_a_big_box(self, monkeypatch):
        monkeypatch.setattr(f"{SUPERVISOR_MODULE}.multiprocessing.cpu_count", lambda: 64)
        assert _supervisor()._get_gunicorn_threads() == 4

    @pytest.mark.timeout(15)
    def test_threads_track_cpu_count_between_the_bounds(self, monkeypatch):
        monkeypatch.setattr(f"{SUPERVISOR_MODULE}.multiprocessing.cpu_count", lambda: 3)
        assert _supervisor()._get_gunicorn_threads() == 3

    @pytest.mark.timeout(15)
    def test_max_requests_jitter_is_a_tenth_of_max_requests(self):
        """Without jitter every worker recycles on the same request count and the
        whole pool restarts at once."""
        sup = _supervisor()
        assert sup._compute_max_requests_jitter(1000) == 100
        assert sup._compute_max_requests_jitter(255) == 25


class TestSupervisorContext:
    """`generate_supervisor_config` merges common_site_config.json over the derived
    defaults; the merge decides every number in the rendered conf."""

    @staticmethod
    def _patch_sizing(monkeypatch, cpus=4, ram_gb=32):
        monkeypatch.setattr(f"{SUPERVISOR_MODULE}.multiprocessing.cpu_count", lambda: cpus)
        import psutil

        monkeypatch.setattr(psutil, "virtual_memory", lambda: SimpleNamespace(total=int(ram_gb * 1024**3)))

    @pytest.mark.timeout(15)
    def test_defaults_when_there_is_no_common_site_config(self, tmp_path, monkeypatch):
        self._patch_sizing(monkeypatch, cpus=4, ram_gb=32)
        _, ctx = _supervisor().generate_supervisor_config(_bench_path(tmp_path))

        assert ctx["gunicorn_workers"] == 4
        assert ctx["gunicorn_threads"] == 4
        assert ctx["gunicorn_max_requests"] == 1000
        assert ctx["gunicorn_max_requests_jitter"] == 100
        assert ctx["http_timeout"] == 120
        assert ctx["webserver_port"] == 80
        assert ctx["background_workers"] == 1
        assert ctx["user"] == "frappe"
        assert ctx["bench_dir"] == "/workspace/frappe-bench"
        assert ctx["supervisor_startretries"] == 10
        assert ctx["workers"] == {}

    @pytest.mark.timeout(15)
    def test_explicit_settings_beat_the_derived_ones(self, tmp_path, monkeypatch):
        self._patch_sizing(monkeypatch, cpus=4, ram_gb=32)
        bench = _bench_path(
            tmp_path,
            '{"gunicorn_workers": 11, "gunicorn_threads": 9, "gunicorn_max_requests": 250,'
            ' "http_timeout": 600, "webserver_port": 8080, "background_workers": 3}',
        )

        _, ctx = _supervisor().generate_supervisor_config(bench)

        assert ctx["gunicorn_workers"] == 11
        assert ctx["gunicorn_threads"] == 9
        assert ctx["gunicorn_max_requests"] == 250
        assert ctx["gunicorn_max_requests_jitter"] == 25
        assert ctx["http_timeout"] == 600
        assert ctx["webserver_port"] == 8080
        assert ctx["background_workers"] == 3

    @pytest.mark.timeout(15)
    def test_zero_background_workers_is_coerced_to_one(self, tmp_path, monkeypatch):
        """`numprocs=0` makes supervisor refuse the program; a bench with no queue
        worker silently stops processing jobs."""
        self._patch_sizing(monkeypatch)
        _, ctx = _supervisor().generate_supervisor_config(_bench_path(tmp_path, '{"background_workers": 0}'))

        assert ctx["background_workers"] == 1

    @pytest.mark.timeout(15)
    def test_unparseable_common_site_config_falls_back_to_defaults(self, tmp_path, monkeypatch):
        """A truncated config must not stop the bench from getting a supervisor conf."""
        self._patch_sizing(monkeypatch, cpus=2, ram_gb=32)
        _, ctx = _supervisor().generate_supervisor_config(_bench_path(tmp_path, "{not json"))

        assert ctx["gunicorn_workers"] == 2
        assert ctx["http_timeout"] == 120

    @pytest.mark.timeout(15)
    def test_multi_queue_consumption_is_on(self, tmp_path, monkeypatch):
        self._patch_sizing(monkeypatch)
        _, ctx = _supervisor().generate_supervisor_config(_bench_path(tmp_path))

        assert ctx["multi_queue_consumption"] is True

    @pytest.mark.timeout(15)
    def test_a_malformed_workers_entry_stops_the_render(self, tmp_path, monkeypatch):
        """The template subscripts worker entries blindly."""
        self._patch_sizing(monkeypatch)
        bench = _bench_path(tmp_path, '{"workers": {"reports": {"timeout": "soon"}}}')

        with pytest.raises(ValueError, match=r"workers\.reports\.timeout"):
            _supervisor().generate_supervisor_config(bench)


class TestRenderedSupervisorConf:
    """The rendered conf is the contract with supervisord inside the container."""

    @pytest.fixture
    def render(self, tmp_path, monkeypatch):
        monkeypatch.setattr(f"{SUPERVISOR_MODULE}.multiprocessing.cpu_count", lambda: 4)
        import psutil

        monkeypatch.setattr(psutil, "virtual_memory", lambda: SimpleNamespace(total=32 * 1024**3))

        def _render(common_site_config=None, **kwargs):
            bench = _bench_path(tmp_path, common_site_config)
            rendered, ctx = _supervisor().generate_supervisor_config(bench, **kwargs)
            return _programs(rendered), ctx

        return _render

    @pytest.mark.timeout(15)
    def test_every_program_runs_as_the_requested_user(self, render):
        """The bench workspace is owned by one uid; a program running as anyone else
        cannot write logs or the socket."""
        cfg, _ = render(user="operator")

        programs = [s for s in cfg.sections() if s.startswith("program:")]
        assert programs
        for section in programs:
            assert cfg.get(section, "user") == "operator", section

    @pytest.mark.timeout(15)
    def test_the_default_user_is_frappe(self, render):
        cfg, _ = render()
        assert cfg.get("program:frappe-bench-frappe-web", "user") == "frappe"

    @pytest.mark.timeout(15)
    def test_web_stop_grace_exceeds_the_gunicorn_graceful_timeout(self, render, tmp_path):
        """The invariant in the template: supervisor must wait longer than gunicorn's
        own graceful shutdown or it SIGKILLs the master and orphans the workers."""
        cfg, ctx = render()
        stopwaitsecs = cfg.getint("program:frappe-bench-frappe-web", "stopwaitsecs")

        sup = _supervisor()
        config_dir = tmp_path / "cfg"
        config_dir.mkdir()
        sup._write_gunicorn_wrapper(config_dir, ctx)
        script = (config_dir / "fm-web-server.sh").read_text()

        assert stopwaitsecs == 40
        assert "--graceful-timeout 30" in script
        assert stopwaitsecs > 30

    @pytest.mark.timeout(15)
    def test_short_and_long_queues_get_their_own_stop_grace(self, render):
        """A long-running job must not be killed mid-flight on restart; the short
        queue does not need 26 minutes of grace."""
        cfg, _ = render()

        assert cfg.getint("program:frappe-bench-frappe-short-worker", "stopwaitsecs") == 360
        assert cfg.getint("program:frappe-bench-frappe-long-worker", "stopwaitsecs") == 1560

    @pytest.mark.timeout(15)
    def test_the_scheduler_gets_no_stop_grace_override(self, render):
        """It is restartable at any point, so the supervisor default applies."""
        cfg, _ = render()

        assert not cfg.has_option("program:frappe-bench-frappe-schedule", "stopwaitsecs")

    @pytest.mark.timeout(15)
    def test_no_separate_default_worker_when_queues_are_multiplexed(self, render):
        """`short` also consumes `default`, so a dedicated default worker would take
        the same jobs twice."""
        cfg, _ = render()

        assert not cfg.has_section("program:frappe-bench-frappe-default-worker")
        assert cfg.get("program:frappe-bench-frappe-short-worker", "command").endswith("worker --queue short,default")
        assert cfg.get("program:frappe-bench-frappe-long-worker", "command").endswith(
            "worker --queue long,default,short"
        )

    @pytest.mark.timeout(15)
    def test_a_custom_queues_timeout_becomes_its_supervisor_stop_grace(self, render):
        """This is the whole point of the `timeout` key: RQ will let a job in that
        queue run that long, so supervisor has to wait at least as long."""
        cfg, _ = render('{"workers": {"reports": {"timeout": 5400}}}')

        section = "program:frappe-bench-frappe-reports-worker"
        assert cfg.getint(section, "stopwaitsecs") == 5400
        assert cfg.get(section, "command").endswith("worker --queue reports")

    @pytest.mark.timeout(15)
    def test_a_custom_queue_defaults_to_the_shared_process_count(self, render):
        cfg, _ = render('{"background_workers": 3, "workers": {"reports": {}}}')

        assert cfg.getint("program:frappe-bench-frappe-reports-worker", "numprocs") == 3
        # and the default timeout is frappe's queue default, not the built-ins'
        assert cfg.getint("program:frappe-bench-frappe-reports-worker", "stopwaitsecs") == 300

    @pytest.mark.timeout(15)
    def test_a_custom_queue_can_override_its_own_process_count(self, render):
        cfg, _ = render('{"background_workers": 3, "workers": {"reports": {"background_workers": 7}}}')

        assert cfg.getint("program:frappe-bench-frappe-reports-worker", "numprocs") == 7
        assert cfg.getint("program:frappe-bench-frappe-short-worker", "numprocs") == 3

    @pytest.mark.timeout(15)
    def test_custom_queues_join_the_workers_group(self, render):
        """The group name is what `supervisorctl restart <group>:*` and the workers
        compose address; a program outside it is never restarted."""
        cfg, _ = render('{"workers": {"reports": {}, "email": {}}}')

        programs = cfg.get("group:frappe-bench-workers", "programs").split(",")
        assert programs == [
            "frappe-bench-frappe-schedule",
            "frappe-bench-frappe-short-worker",
            "frappe-bench-frappe-long-worker",
            "frappe-bench-frappe-reports-worker",
            "frappe-bench-frappe-email-worker",
        ]

    @pytest.mark.timeout(15)
    def test_the_web_group_holds_the_request_serving_programs(self, render):
        cfg, _ = render()

        assert cfg.get("group:frappe-bench-web", "programs").split(",") == [
            "frappe-bench-frappe-web",
            "frappe-bench-node-socketio",
        ]

    @pytest.mark.timeout(15)
    def test_workers_are_killed_as_a_group_so_no_child_survives(self, render):
        cfg, _ = render()

        for name in ("short-worker", "long-worker"):
            section = f"program:frappe-bench-frappe-{name}"
            assert cfg.get(section, "killasgroup") == "true", section
            assert cfg.get(section, "stopasgroup") == "true", section


class TestGunicornWrapper:
    @pytest.mark.timeout(15)
    def test_the_wrapper_carries_the_whole_gunicorn_command_line(self, tmp_path):
        ctx = {
            "webserver_port": 8080,
            "gunicorn_workers": 3,
            "gunicorn_threads": 2,
            "gunicorn_max_requests": 500,
            "gunicorn_max_requests_jitter": 50,
            "http_timeout": 300,
            "bench_dir": "/workspace/frappe-bench",
        }
        sup = _supervisor()

        sup._write_gunicorn_wrapper(tmp_path, ctx)

        script = (tmp_path / "fm-web-server.sh").read_text()
        assert (
            "-b 0.0.0.0:8080 -w 3 --worker-class=gthread --threads 2 --max-requests 500"
            " --max-requests-jitter 50 -t 300 --graceful-timeout 30"
            " frappe.app:application --preload" in script
        )

    @pytest.mark.timeout(15)
    def test_the_wrapper_is_executable(self, tmp_path):
        """supervisor runs it through `/bin/bash`, but the entrypoint also execs it."""
        sup = _supervisor()
        sup._write_gunicorn_wrapper(
            tmp_path,
            {
                "webserver_port": 80,
                "gunicorn_workers": 1,
                "gunicorn_threads": 2,
                "gunicorn_max_requests": 1000,
                "gunicorn_max_requests_jitter": 100,
                "http_timeout": 120,
                "bench_dir": "/workspace/frappe-bench",
            },
        )

        assert (tmp_path / "fm-web-server.sh").stat().st_mode & 0o111 == 0o111


class TestSetupSupervisor:
    @pytest.fixture(autouse=True)
    def sizing(self, monkeypatch):
        monkeypatch.setattr(f"{SUPERVISOR_MODULE}.multiprocessing.cpu_count", lambda: 2)
        import psutil

        monkeypatch.setattr(psutil, "virtual_memory", lambda: SimpleNamespace(total=8 * 1024**3))

    @pytest.mark.timeout(15)
    def test_existing_confs_are_left_alone_without_force(self, tmp_path):
        """Regenerating would throw away an operator's hand edit on every `fm start`."""
        bench = _bench_path(tmp_path)
        config_dir = bench / "workspace" / "frappe-bench" / "config"
        config_dir.mkdir(parents=True)
        (config_dir / "web.fm.supervisor.conf").write_text("hand edited\n")

        _supervisor().setup_supervisor(bench)

        assert (config_dir / "web.fm.supervisor.conf").read_text() == "hand edited\n"
        assert not (config_dir / "fm-web-server.sh").exists()

    @pytest.mark.timeout(15)
    def test_force_regenerates_over_existing_confs(self, tmp_path):
        bench = _bench_path(tmp_path)
        config_dir = bench / "workspace" / "frappe-bench" / "config"
        config_dir.mkdir(parents=True)
        (config_dir / "web.fm.supervisor.conf").write_text("hand edited\n")

        _supervisor().setup_supervisor(bench, force=True)

        assert "program:frappe-bench-frappe-web" in (config_dir / "web.fm.supervisor.conf").read_text()

    @pytest.mark.timeout(15)
    def test_a_fresh_bench_gets_one_conf_per_program_plus_the_wrapper(self, tmp_path):
        bench = _bench_path(tmp_path)

        _supervisor().setup_supervisor(bench)

        config_dir = bench / "workspace" / "frappe-bench" / "config"
        assert sorted(p.name for p in config_dir.iterdir()) == [
            "fm-web-server.sh",
            "long-worker.workers.fm.supervisor.conf",
            "schedule.fm.supervisor.conf",
            "short-worker.workers.fm.supervisor.conf",
            "socketio.fm.supervisor.conf",
            "web.fm.supervisor.conf",
        ]

    @pytest.mark.timeout(15)
    def test_a_custom_queue_gets_its_own_worker_conf(self, tmp_path):
        bench = _bench_path(tmp_path, '{"workers": {"reports": {"timeout": 900}}}')

        _supervisor().setup_supervisor(bench)

        conf = bench / "workspace" / "frappe-bench" / "config" / "reports-worker.workers.fm.supervisor.conf"
        assert "stopwaitsecs = 900" in conf.read_text()

    @pytest.mark.timeout(15)
    def test_a_broken_workers_key_fails_the_setup_naming_the_bench(self, tmp_path):
        """Failing loud at sync time beats a conf that only breaks in the container."""
        bench = _bench_path(tmp_path, '{"workers": {"default": {}}}')

        with pytest.raises(BenchOperationException, match="Failed to configure supervisor"):
            _supervisor().setup_supervisor(bench)

    @pytest.mark.timeout(15)
    def test_newrelic_ini_is_written_only_when_enabled_with_a_key(self, tmp_path):
        bench = _bench_path(tmp_path)

        _supervisor(newrelic_enabled=True, newrelic_license_key="key-123").setup_supervisor(bench)

        ini = bench / "workspace" / "frappe-bench" / "config" / "newrelic.ini"
        parsed = configparser.RawConfigParser()
        parsed.read_string(ini.read_text())
        assert parsed.get("newrelic", "license_key") == "key-123"
        assert parsed.get("newrelic", "app_name") == "Frappe - test.localhost"

    @pytest.mark.timeout(15)
    def test_newrelic_enabled_without_a_key_writes_no_ini(self, tmp_path):
        """The wrapper script refuses to start when NR is on but the ini is missing,
        so writing a keyless ini would be worse than writing none."""
        bench = _bench_path(tmp_path)

        _supervisor(newrelic_enabled=True).setup_supervisor(bench)

        assert not (bench / "workspace" / "frappe-bench" / "config" / "newrelic.ini").exists()

    @pytest.mark.timeout(15)
    def test_newrelic_disabled_writes_no_ini(self, tmp_path):
        bench = _bench_path(tmp_path)

        _supervisor(newrelic_license_key="key-123").setup_supervisor(bench)

        assert not (bench / "workspace" / "frappe-bench" / "config" / "newrelic.ini").exists()

    @pytest.mark.timeout(15)
    def test_setup_newrelic_refreshes_the_wrapper_without_the_confs(self, tmp_path):
        """`fm update` toggling newrelic must not rewrite the supervisor confs."""
        bench = _bench_path(tmp_path)

        _supervisor(newrelic_enabled=True, newrelic_license_key="k").setup_newrelic(bench)

        config_dir = bench / "workspace" / "frappe-bench" / "config"
        assert sorted(p.name for p in config_dir.iterdir()) == ["fm-web-server.sh", "newrelic.ini"]


class TestSupervisordReadiness:
    @pytest.mark.timeout(15)
    def test_a_successful_status_call_means_running(self):
        sup = _supervisor()

        assert sup.is_supervisord_running() is True
        sup.docker_client.compose.exec.assert_called_once_with(
            "frappe",
            "supervisorctl -c /opt/user/supervisord.conf status all",
            user="frappe",
            stream=False,
        )

    @pytest.mark.timeout(15)
    def test_a_failure_that_still_mentions_the_bench_counts_as_running(self, monkeypatch):
        """supervisorctl exits non-zero when some program is not RUNNING, but the
        output naming the bench proves supervisord itself answered."""
        monkeypatch.setattr(f"{SUPERVISOR_MODULE}.time.sleep", lambda _: None)
        sup = _supervisor()
        sup.docker_client.compose.exec.side_effect = _docker_exception("frappe-bench-frappe-web FATAL")

        assert sup.is_supervisord_running() is True
        assert sup.docker_client.compose.exec.call_count == 1

    @pytest.mark.timeout(15)
    def test_it_retries_then_gives_up(self, monkeypatch):
        sleeps = []
        monkeypatch.setattr(f"{SUPERVISOR_MODULE}.time.sleep", sleeps.append)
        sup = _supervisor()
        sup.docker_client.compose.exec.side_effect = _docker_exception("connection refused")

        assert sup.is_supervisord_running(interval=3, timeout=4) is False
        assert sup.docker_client.compose.exec.call_count == 4
        assert sleeps == [3, 3, 3, 3]


class TestRestartSupervisorService:
    @staticmethod
    def _client(running=True, service="frappe"):
        client = MagicMock()
        client.compose.get_all_services_status.return_value = (
            [{"Service": service, "State": "running"}] if running else []
        )
        return client

    @pytest.mark.timeout(15)
    def test_a_service_that_is_not_running_is_refused(self):
        sup = _supervisor()
        sup.docker_client = self._client(running=False)

        assert sup.restart_supervisor_service("frappe") is False
        sup.docker_client.compose.exec.assert_not_called()
        sup.output.display_error.assert_called_once()

    @pytest.mark.timeout(15)
    def test_an_unreachable_daemon_is_treated_as_not_running(self):
        sup = _supervisor()
        sup.docker_client = MagicMock()
        sup.docker_client.compose.get_all_services_status.side_effect = _docker_exception()

        assert sup.restart_supervisor_service("frappe") is False

    @pytest.mark.timeout(15)
    def test_the_graceful_path_issues_one_restart_then_checks_the_socket(self):
        sup = _supervisor()
        sup.docker_client = self._client()

        assert sup.restart_supervisor_service("frappe") is True

        commands = [c.kwargs["command"] for c in sup.docker_client.compose.exec.call_args_list]
        assert commands == [
            "supervisorctl -c /opt/user/supervisord.conf restart all",
            "test -e /fm-sockets/frappe.sock",
        ]

    @pytest.mark.timeout(15)
    def test_the_force_path_stops_before_it_starts(self):
        """A hard restart has to fully stop the programs; `restart` leaves a wedged
        process wedged."""
        sup = _supervisor()
        sup.docker_client = self._client()

        assert sup.restart_supervisor_service("frappe", force=True) is True

        commands = [c.kwargs["command"] for c in sup.docker_client.compose.exec.call_args_list]
        assert commands == [
            "supervisorctl -c /opt/user/supervisord.conf stop all",
            "supervisorctl -c /opt/user/supervisord.conf start all",
            "test -e /fm-sockets/frappe.sock",
        ]

    @pytest.mark.timeout(15)
    def test_supervisorctl_always_runs_as_frappe(self):
        sup = _supervisor()
        sup.docker_client = self._client(service="socketio")

        sup.restart_supervisor_service("socketio")

        for c in sup.docker_client.compose.exec.call_args_list:
            assert c.kwargs["user"] == "frappe"

    @pytest.mark.timeout(15)
    def test_a_failed_graceful_restart_raises_naming_the_bench(self):
        sup = _supervisor(bench_name="broken.localhost")
        sup.docker_client = self._client()
        sup.docker_client.compose.exec.side_effect = _docker_exception("no such process")

        with pytest.raises(BenchOperationException, match="Failed to restart supervisor for frappe service"):
            sup.restart_supervisor_service("frappe")

    @pytest.mark.timeout(15)
    def test_a_failed_force_restart_raises_its_own_message(self):
        sup = _supervisor()
        sup.docker_client = self._client()
        sup.docker_client.compose.exec.side_effect = _docker_exception("boom")

        with pytest.raises(BenchOperationException, match="Failed to force restart supervisor"):
            sup.restart_supervisor_service("frappe", force=True)

    @pytest.mark.timeout(15)
    def test_a_missing_socket_warns_but_still_reports_success(self, monkeypatch):
        """Dev mode does not use the socket, so its absence must not fail a restart
        that supervisorctl already accepted."""
        monkeypatch.setattr(f"{SUPERVISOR_MODULE}.time.sleep", lambda _: None)
        sup = _supervisor()
        sup.docker_client = self._client()
        sup.docker_client.compose.exec.side_effect = [MagicMock()] + [_docker_exception("nope")] * 3

        assert sup.restart_supervisor_service("frappe", timeout=3) is True
        sup.output.warning.assert_called_once()
        assert "/fm-sockets/frappe.sock" in sup.output.warning.call_args.args[0]

    @pytest.mark.timeout(15)
    def test_the_socket_check_goes_to_the_client_that_did_the_restart(self):
        """Was pinned as a suspicion, now fixed: the socket lives inside the container
        the restart was issued in, so it has to be verified through the SAME client.
        Worker callers pass the workers-compose client, and the bench compose has no
        worker service at all."""
        sup = _supervisor()
        sup.docker_client = self._client()
        other = self._client()

        assert sup.restart_supervisor_service("frappe", docker_client_obj=other) is True

        assert [c.kwargs["command"] for c in other.compose.exec.call_args_list] == [
            "supervisorctl -c /opt/user/supervisord.conf restart all",
            "test -e /fm-sockets/frappe.sock",
        ]
        sup.docker_client.compose.exec.assert_not_called()

    @pytest.mark.timeout(15)
    def test_a_worker_restart_does_not_burn_the_timeout_on_the_bench_compose(self, monkeypatch):
        """`--force` is documented as the fast path. Checking the socket on the bench
        compose (which has no `short-worker` service) made every attempt raise, so each
        worker cost the full timeout in sleeps and then warned for nothing."""
        sleeps = []
        monkeypatch.setattr(f"{SUPERVISOR_MODULE}.time.sleep", sleeps.append)
        sup = _supervisor()
        # the bench compose knows nothing about worker services
        sup.docker_client = self._client(running=False)
        sup.docker_client.compose.exec.side_effect = _docker_exception("no such service: short-worker")
        workers = self._client(service="short-worker")

        assert sup.restart_supervisor_service("short-worker", docker_client_obj=workers, force=True) is True

        assert [c.kwargs["command"] for c in workers.compose.exec.call_args_list] == [
            "supervisorctl -c /opt/user/supervisord.conf stop all",
            "supervisorctl -c /opt/user/supervisord.conf start all",
            "test -e /fm-sockets/short-worker.sock",
        ]
        sup.docker_client.compose.exec.assert_not_called()
        assert sleeps == []
        sup.output.warning.assert_not_called()


class TestRunFrappeCommand:
    @pytest.mark.timeout(15)
    def test_a_command_runs_unstreamed_as_frappe(self):
        sup = _supervisor()

        sup._run_frappe_command("bench build")

        sup.docker_client.compose.exec.assert_called_once_with("frappe", "bench build", user="frappe", stream=False)

    @pytest.mark.timeout(15)
    def test_a_docker_failure_becomes_a_bench_exception_naming_the_command(self):
        """The docker traceback is useless to an operator; the command is not."""
        from frappe_manager.site_manager.exceptions import BenchException

        sup = _supervisor()
        sup.docker_client.compose.exec.side_effect = _docker_exception("exit 1")

        with pytest.raises(BenchException, match="Failed to run bench build in frappe service"):
            sup._run_frappe_command("bench build")


class TestSetupNewrelicFailure:
    @pytest.mark.timeout(15)
    def test_an_unreadable_bench_config_is_reported_as_a_newrelic_setup_failure(self, tmp_path, monkeypatch):
        """`setup_newrelic` re-reads the bench config purely to rebuild the wrapper;
        it must not surface as a generic supervisor error."""
        monkeypatch.setattr(f"{SUPERVISOR_MODULE}.multiprocessing.cpu_count", lambda: 2)
        import psutil

        monkeypatch.setattr(psutil, "virtual_memory", lambda: SimpleNamespace(total=8 * 1024**3))
        bench = _bench_path(tmp_path, '{"workers": {"schedule": {}}}')

        with pytest.raises(BenchOperationException, match="Failed to read bench config for NewRelic setup"):
            _supervisor().setup_newrelic(bench)
