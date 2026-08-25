"""The order of `fm create`'s phases, and what each one is allowed to skip.

`BenchOrchestrator.create_bench` is a pipeline of ordered phases, and the ORDER is the contract.
Two bugs this month came out of it: a config file written after the thing that reads it, and a
directory created before the image copy that has to precede it. Neither is visible in a test that
only checks the happy path finished, so these tests pin the sequence itself -- which phase runs
before which, where the external-database probe sits relative to provisioning, when the per-site
`site_config.json` is written relative to `bench new-site`, and which phases are skipped on the
attach and template paths.

Everything is characterization: it pins what the code does TODAY so a later refactor that reorders
a side effect fails here instead of in someone's database. Docker is never reached; every
container channel is a recorder and the phase methods that only exist to talk to Docker are
stubbed with recorders of their own, so the assertions are about sequence and arguments.

Two layers:
- skeleton: the phase methods are stubbed, so the recorded event list IS `create_bench`'s
  orchestration (which phases, in which order, on which path).
- inner: the phase under test runs for real against a recording bench, so ordering WITHIN a phase
  (directories before compose, site file before new-site, re-check before provisioning) is pinned.
"""

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from frappe_manager.docker.docker_exceptions import DockerException
from frappe_manager.docker.subprocess_output import SubprocessOutput
from frappe_manager.site_manager.bench_config import BenchConfig, DeployState, FMBenchEnvType, SwitchConfig
from frappe_manager.site_manager.exceptions import BenchOperationException
from frappe_manager.site_manager.modules import db_probe, db_tls
from frappe_manager.site_manager.modules.bench_orchestrator import BenchOrchestrator

SITE = "app.example.com"
SCHEMA = "app_prod"
DB_HOST = "mydb.example.com"
SITE_PASSWORD = "site-db-secret"
ADMIN_USER = "fmadmin"
ADMIN_PASSWORD = "admin-secret"
IMAGE_TAG = "ghcr.io/fm/app:v1"

_TOP_LEVEL = [
    f'name = "{SITE}"',
    "developer_mode = false",
    "admin_tools = false",
    'environment = "prod"',
]

_APPS_TABLE = """
[[apps]]
name = "erpnext"
repo = "frappe/erpnext"
"""

_EXTERNAL_TABLE = f"""
[database."{SITE}"]
host = "{DB_HOST}"
name = "{SCHEMA}"
user = "app_svc"
"""


# --------------------------------------------------------------------------- config + probe fakes


def _config(
    tmp_path: Path,
    *,
    external: bool = False,
    runtime: str = "mount",
    seed_image: str | None = None,
    ca: str | None = None,
) -> BenchConfig:
    top = list(_TOP_LEVEL)
    if runtime == "image":
        top += ['runtime = "image"', 'image = "ghcr.io/fm/app"']
    if seed_image:
        top.append(f'seed_image = "{seed_image}"')
    # Order matters: bare keys after a table header would land inside that table.
    toml = "\n".join(top) + _APPS_TABLE
    if external:
        toml += _EXTERNAL_TABLE
        if ca:
            toml += f'ca = "{ca}"\n'
    tmp_path.mkdir(parents=True, exist_ok=True)
    path = tmp_path / "bench_config.toml"
    path.write_text(toml)
    config = BenchConfig.import_from_toml(path)
    if external:
        config.db_password = SITE_PASSWORD
    if runtime == "image":
        config.deploy_state = DeployState(current_tag=IMAGE_TAG)
    return config


def _probe_result(
    *,
    exists: bool,
    table_count: int = 0,
    is_frappe: bool = False,
    checks: tuple[db_probe.ProbeCheck, ...] | None = None,
) -> db_probe.ProbeResult:
    return db_probe.ProbeResult(
        checks=checks or (db_probe.ProbeCheck(db_probe.CHECK_CONNECT, db_probe.CheckStatus.ok, "connected"),),
        schema=db_probe.SchemaState(
            exists=exists,
            table_count=table_count,
            is_frappe=is_frappe,
            installed_apps=("frappe", "erpnext") if is_frappe else (),
        ),
        server_enforces_tls=False,
        tls_in_force=False,
        user_exists=exists,
    )


# `decide_flow` stays real everywhere: the flow decision is the thing under test, the server
# round trip is not. These are the three shapes it turns into the three flows.
ABSENT = {"exists": False}
EMPTY = {"exists": True, "table_count": 0}
FRAPPE_SITE = {"exists": True, "table_count": 412, "is_frappe": True}


class _Events(list):
    """An ordered log of everything the create did, plus helpers to assert on the order."""

    def hook(self, label: str, *, result=None, formatter=None):
        def side_effect(*args, **kwargs):
            self.append(formatter(*args, **kwargs) if formatter else label)
            return result

        return side_effect

    def at(self, prefix: str) -> int:
        for index, event in enumerate(self):
            if event.startswith(prefix):
                return index
        raise AssertionError(f"{prefix!r} never happened. Events: {list(self)}")

    def has(self, prefix: str) -> bool:
        return any(event.startswith(prefix) for event in self)

    def before(self, first: str, second: str) -> None:
        assert self.at(first) < self.at(second), f"{first!r} must precede {second!r}. Events: {list(self)}"

    def only(self, *prefixes: str) -> list[str]:
        return [event for event in self if event.startswith(prefixes)]


class _Harness:
    """A bench whose every collaborator records into one ordered event list.

    `bench.path` is a real directory under tmp_path so the phases that create directories really
    create them, which is how "the directory existed by the time X ran" becomes assertable.
    """

    def __init__(self, config: BenchConfig, tmp_path: Path):
        self.events = _Events()
        self.config = config
        self.root = tmp_path / "benches" / SITE
        self.probe_calls: list[dict] = []
        self.probe_results: list[db_probe.ProbeResult] = []
        self.stage_two_calls: list[dict] = []
        self.written_site_configs: list[dict] = []
        self.saved_switch: list[object] = []
        self.compose_run_kwargs: list[dict] = []
        self.compose_exec_kwargs: list[dict] = []
        self.remove_status = True
        self.bench = self._bench(config)

    # ------------------------------------------------------------------ bench
    @property
    def sites_dir(self) -> Path:
        return self.root / "workspace" / "frappe-bench" / "sites"

    def _bench(self, config: BenchConfig):
        events = self.events
        bench = MagicMock()
        bench.name = SITE
        bench.path = self.root
        bench.bench_config = config
        bench.exists = True

        bench.docker_ops.check_required_docker_images_available.side_effect = events.hook("check_images")
        bench.generate_compose.side_effect = events.hook(
            "generate_compose", formatter=lambda *_a, **_k: f"generate_compose(bench_dir_exists={self.root.is_dir()})"
        )
        bench.create_compose_dirs.side_effect = events.hook(
            "create_compose_dirs", formatter=lambda **kw: f"create_compose_dirs(copy_runtimes={kw['copy_runtimes']})"
        )
        bench.docker_client.images.return_value = []
        bench.docker_client.pull.side_effect = events.hook("pull", formatter=lambda image, **_k: f"pull({image})")
        bench.set_common_bench_config.side_effect = events.hook("common_site_config")
        bench.sync_bench_common_site_config.side_effect = events.hook("sync_common_site_config")
        bench.supervisor.setup_supervisor.side_effect = events.hook("setup_supervisor")
        bench.supervisor.is_supervisord_running.return_value = True

        bench.docker_client.compose.up.side_effect = events.hook(
            "compose_up", formatter=lambda **_kw: f"compose_up(site_dir_exists={(self.sites_dir / SITE).is_dir()})"
        )
        bench.docker_client.compose.run.side_effect = self._compose_run
        bench.docker_client.compose.exec.side_effect = self._compose_exec
        bench.workers.docker_client.compose.up.side_effect = events.hook("workers_up")

        bench.site_manager.wait_for_required_services.side_effect = events.hook("wait_for_services")
        bench.site_manager.create_bench_site.side_effect = events.hook(
            "new-site", formatter=lambda **kw: f"new-site(force={kw.get('force')})"
        )
        bench.site_manager.create_site_dirs.side_effect = events.hook("create_site_dirs")
        bench.site_manager.provision_external_schema.side_effect = events.hook(
            "provision_external_schema",
            formatter=lambda **kw: f"provision_external_schema(admin_user={kw['admin_user']},site={kw['site']})",
        )

        bench.create_bench_site_config.side_effect = self._write_site_config
        bench.set_bench_site_config.side_effect = events.hook("set_bench_site_config")
        bench.sync_bench_config_configuration.side_effect = events.hook("sync_bench_config_configuration")
        bench.sync_workers_compose.side_effect = events.hook(
            "sync_workers_compose", formatter=lambda **kw: f"sync_workers_compose(start={kw.get('start')})"
        )
        bench.save_bench_config.side_effect = self._save_bench_config
        bench.is_bench_created.return_value = True

        bench.app_manager.bench_cli_cmd = ["bench"]
        bench.app_manager.install_apps_to_site.side_effect = events.hook("install_apps_to_site")
        bench.app_manager._container_run.side_effect = events.hook(
            "container_run", formatter=lambda command, **_k: f"container_run({command})"
        )
        bench.info.side_effect = events.hook("info")
        bench.remove_bench.side_effect = lambda **kw: (
            events.append(f"remove_bench(default_choice={kw.get('default_choice')})"),
            self.remove_status,
        )[1]

        # `start_bench` and the alias-domain workflow, whose collaborators are disjoint from the
        # create's. Defaults are the plainest bench: no admin tools, no workers compose, nginx up.
        bench.docker_ops.start.side_effect = events.hook(
            "docker_ops_start",
            formatter=lambda **kw: f"docker_ops_start(services={kw['services']},force_recreate={kw['force_recreate']})",
        )
        bench.admin_tools.compose_file_manager.compose_path.exists.return_value = False
        bench.admin_tools.is_running.return_value = True
        bench.admin_tools.enable.side_effect = events.hook(
            "admin_tools_enable",
            formatter=lambda **kw: f"admin_tools_enable(force_recreate_container={kw['force_recreate_container']})",
        )
        bench._is_service_running.return_value = True
        bench.workers.compose_file_manager.exists.return_value = False
        bench.install_dev_packages.side_effect = events.hook("install_dev_packages")
        bench.remove_dev_packages.side_effect = events.hook("remove_dev_packages")
        bench.docker_client.compose.stop.side_effect = events.hook("compose_stop")
        return bench

    # ------------------------------------------------------------------ recorders
    def _compose_run(self, **kwargs) -> SubprocessOutput:
        self.compose_run_kwargs.append(kwargs)
        self.events.append(f"compose_run({kwargs.get('command')})")
        return SubprocessOutput(stdout=[], stderr=[], combined=[], exit_code=0)

    def _compose_exec(self, **kwargs) -> SubprocessOutput:
        self.compose_exec_kwargs.append(kwargs)
        self.events.append(f"compose_exec({kwargs.get('command')})")
        return SubprocessOutput(stdout=["200"], stderr=[], combined=["200"], exit_code=0)

    def _write_site_config(self, data: dict) -> None:
        self.written_site_configs.append(dict(data))
        self.events.append("write_site_config")

    def _save_bench_config(self, *_a, **_k) -> None:
        switch = self.config.switch
        self.saved_switch.append(None if switch is None else switch.migrate)
        self.events.append(f"save_bench_config(migrate={None if switch is None else switch.migrate})")

    # ------------------------------------------------------------------ orchestrator
    def orchestrator(self, *, real: tuple[str, ...] = ()) -> BenchOrchestrator:
        """`real` names the phases to run for real; the rest become recorders."""
        orchestrator = BenchOrchestrator(self.bench, output_handler=MagicMock())
        self.output = orchestrator.output
        self.output.prompt_ask.return_value = "no"

        stubs = {
            "_phase1_prepare_structure": None,
            "_external_database_gate": None,
            "_phase2_initialize_bench": None,
            "_phase2_seed_from_image": None,
            "_phase3_start_and_verify_bench": None,
            "_phase4_create_site": None,
            "_phase5_finalize": None,
            "_phase6_install_apps": True,
            "_skip_phase6_for_attach": True,
            "_create_template_bench": None,
            "_create_image_bench": None,
            "_recheck_external_schema": None,
            "_provision_external_schema": None,
            "_attach_existing_site": None,
            "_handle_creation_failure": None,
        }
        for name, result in stubs.items():
            if name in real:
                continue
            formatter = None
            if name == "_phase4_create_site":
                formatter = lambda *_a, **kw: f"phase4_create_site(force={kw.get('force', bool(_a and _a[0]))})"  # noqa: E731
            if name == "_phase3_start_and_verify_bench":
                formatter = lambda *_a, **_k: (  # noqa: E731
                    f"phase3_start_and_verify_bench(site_dir_exists={(self.sites_dir / SITE).is_dir()})"
                )
            # A MagicMock so a test can also assert on call counts and arguments, wrapping the
            # recorder so the ordered event list stays the primary evidence.
            stub = MagicMock(side_effect=self.events.hook(name.lstrip("_"), result=result, formatter=formatter))
            setattr(orchestrator, name, stub)
        return orchestrator

    def reraising_orchestrator(self, *, real: tuple[str, ...] = ()) -> BenchOrchestrator:
        """`create_bench` funnels every exception into `_handle_creation_failure`, which swallows
        it. Re-raise so a broken fake surfaces instead of a silently truncated pipeline."""
        orchestrator = self.orchestrator(real=real)

        def _reraise(exception: Exception):
            self.events.append(f"handle_creation_failure({type(exception).__name__})")
            raise exception

        orchestrator._handle_creation_failure = _reraise  # type: ignore[method-assign]
        return orchestrator

    # ------------------------------------------------------------------ probe fakes
    def stage_one_returns(self, monkeypatch, *shapes: dict) -> None:
        """One `probe_stage_one` reply per call, in order: the gate's, then the re-check's."""
        replies = [_probe_result(**shape) for shape in shapes]

        def fake(_runner, **kwargs) -> db_probe.ProbeResult:
            self.probe_calls.append(kwargs)
            self.events.append("probe_stage_one")
            reply = replies[min(len(self.probe_calls) - 1, len(replies) - 1)]
            self.probe_results.append(reply)
            return reply

        monkeypatch.setattr(db_probe, "probe_stage_one", fake)

    def stage_two_returns(self, monkeypatch, shape: dict) -> None:
        def fake(_runner, **kwargs) -> db_probe.ProbeResult:
            self.stage_two_calls.append(kwargs)
            self.events.append("probe_stage_two")
            return _probe_result(**shape)

        monkeypatch.setattr(db_probe, "probe_stage_two", fake)


@pytest.fixture(autouse=True)
def _no_real_benches(monkeypatch, tmp_path):
    """`_report_attach_warnings` scans fm's bench directory; point it at an empty one."""
    monkeypatch.setattr("frappe_manager.CLI_BENCHES_DIRECTORY", tmp_path / "fm-benches")


@pytest.fixture(autouse=True)
def _no_sleeping(monkeypatch):
    """`verify_bench_server_responding` sleeps between retries. Not in a unit test it doesn't."""
    monkeypatch.setattr("frappe_manager.site_manager.modules.bench_orchestrator.time.sleep", lambda _s: None)


def _fake_image_transport(monkeypatch, apps: str = "frappe\nerpnext\n") -> _Events:
    """The two registry calls the image and seed paths make, recorded rather than performed."""
    calls = _Events()

    monkeypatch.setattr(
        "frappe_manager.site_manager.modules.transport.fetch_image",
        lambda *_a, **_k: calls.append("fetch_image"),
    )
    monkeypatch.setattr(
        "frappe_manager.site_manager.modules.workspace_seed.materialize_workspace_from_image",
        lambda *_a, **_k: calls.append("materialize_workspace"),
    )

    def fake_cp(_tag, _src, dest, _client):
        calls.append(f"host_run_cp(site_dir_exists={(Path(dest).parent / SITE).is_dir()})")
        Path(dest).parent.mkdir(parents=True, exist_ok=True)
        Path(dest).write_text(apps)

    monkeypatch.setattr("frappe_manager.utils.docker.host_run_cp", fake_cp)
    return calls


def _fail(orchestrator: BenchOrchestrator, message: str) -> None:
    """Drive `_handle_creation_failure` the way `create_bench` does: from inside an `except`
    block, because it formats the live traceback."""
    try:
        raise RuntimeError(message)
    except RuntimeError as exception:
        orchestrator._handle_creation_failure(exception)


# --------------------------------------------------------------------------- skeleton: phase order


def test_a_mount_create_runs_the_phases_in_this_exact_order(tmp_path):
    """The pipeline, top to bottom. Image availability is checked before phase 1 touches the disk,
    and the external-database gate sits between phase 1 and phase 2 -- ahead of every expensive
    step and every connection."""
    harness = _Harness(_config(tmp_path), tmp_path)

    harness.reraising_orchestrator().create_bench()

    assert list(harness.events) == [
        "check_images",
        "phase1_prepare_structure",
        "external_database_gate",
        "phase2_initialize_bench",
        "phase3_start_and_verify_bench(site_dir_exists=False)",
        "phase4_create_site(force=False)",
        "phase5_finalize",
        "phase6_install_apps",
        "info",
    ]


def test_the_image_check_happens_outside_the_try_so_it_is_not_a_creation_failure(tmp_path):
    """`check_required_docker_images_available` is called BEFORE the try block, so a missing image
    propagates to the caller instead of being turned into a cleanup-and-remove."""
    harness = _Harness(_config(tmp_path), tmp_path)
    harness.bench.docker_ops.check_required_docker_images_available.side_effect = RuntimeError("no images")
    orchestrator = harness.orchestrator()

    with pytest.raises(RuntimeError, match="no images"):
        orchestrator.create_bench()

    assert harness.events.has("phase1_prepare_structure") is False
    orchestrator._handle_creation_failure.assert_not_called()


def test_a_template_bench_stops_after_phase_one(tmp_path):
    """Phase 1 still runs -- a template bench is directories and a compose file -- but the gate,
    every later phase and site creation do not."""
    harness = _Harness(_config(tmp_path), tmp_path)

    harness.reraising_orchestrator().create_bench(is_template_bench=True)

    assert list(harness.events) == ["check_images", "phase1_prepare_structure", "create_template_bench"]


def test_a_template_bench_ignores_an_external_database_entry(tmp_path):
    """No probe on the template path: it creates no site, so there is nothing to preflight."""
    harness = _Harness(_config(tmp_path, external=True), tmp_path)

    harness.reraising_orchestrator().create_bench(is_template_bench=True)

    assert harness.events.has("external_database_gate") is False


def test_an_image_runtime_leaves_the_mount_pipeline_after_phase_one(tmp_path):
    """Phase 1 is shared; everything after it is the image path's own sequence, so the mount
    phases 2 and 3 are never reached from `create_bench`."""
    harness = _Harness(_config(tmp_path, runtime="image"), tmp_path)

    harness.reraising_orchestrator().create_bench()

    assert list(harness.events) == ["check_images", "phase1_prepare_structure", "create_image_bench"]


def test_a_seed_image_replaces_phase_two_with_the_seeded_variant(tmp_path):
    """Seeded creates materialize the workspace from an image instead of provisioning it, and the
    gate still sits between phase 1 and that substitution."""
    harness = _Harness(_config(tmp_path, seed_image="ghcr.io/fm/seed:v1"), tmp_path)

    harness.reraising_orchestrator().create_bench()

    assert harness.events.has("phase2_initialize_bench") is False
    harness.events.before("external_database_gate", "phase2_seed_from_image")
    harness.events.before("phase2_seed_from_image", "phase3_start_and_verify_bench")


def test_a_seeded_create_clears_the_route_cache_after_the_apps_are_in(tmp_path):
    """The phase-3 health probe hits the server before the site exists and Frappe caches that
    route miss, so a seeded create flushes it -- after phase 6, before `info`."""
    harness = _Harness(_config(tmp_path, seed_image="ghcr.io/fm/seed:v1"), tmp_path)

    harness.reraising_orchestrator().create_bench()

    harness.events.before("phase6_install_apps", f"container_run(bench --site {SITE} clear-cache)")
    harness.events.before(f"container_run(bench --site {SITE} clear-cache)", "info")


def test_a_plain_create_does_not_clear_the_route_cache(tmp_path):
    """Control: the flush is specific to the seeded path."""
    harness = _Harness(_config(tmp_path), tmp_path)

    harness.reraising_orchestrator().create_bench()

    assert harness.events.has("container_run") is False


def test_a_failing_clear_cache_does_not_fail_the_create(tmp_path):
    """Best effort: the cache flush is a convenience, and the bench is already complete."""
    harness = _Harness(_config(tmp_path, seed_image="ghcr.io/fm/seed:v1"), tmp_path)
    harness.bench.app_manager._container_run.side_effect = RuntimeError("exec failed")

    harness.reraising_orchestrator().create_bench()

    assert harness.events.has("handle_creation_failure") is False
    assert harness.events.has("info") is True


def test_a_localhost_bench_is_not_told_to_edit_the_hosts_file(tmp_path):
    """`.localhost` resolves without a hosts entry, so the notice is suppressed."""
    harness = _Harness(_config(tmp_path), tmp_path)
    harness.bench.name = "local.localhost"

    harness.reraising_orchestrator().create_bench()

    printed = " ".join(str(call) for call in harness.output.print.call_args_list)
    assert "hosts file" not in printed


def test_a_public_domain_is_told_to_edit_the_hosts_file(tmp_path):
    harness = _Harness(_config(tmp_path), tmp_path)

    harness.reraising_orchestrator().create_bench()

    printed = " ".join(str(call) for call in harness.output.print.call_args_list)
    assert "hosts file" in printed


def test_a_failed_phase_six_offers_to_remove_the_bench(tmp_path):
    """Phase 6 fails gracefully -- it returns False rather than raising -- and a create whose apps
    did not install offers to tear the bench down instead of reporting it ready."""
    harness = _Harness(_config(tmp_path), tmp_path)
    orchestrator = harness.reraising_orchestrator()
    orchestrator._phase6_install_apps = harness.events.hook("phase6_install_apps", result=False)

    orchestrator.create_bench()

    assert harness.events.only("remove_bench", "info") == ["remove_bench(default_choice=False)"]


def test_declining_the_removal_still_prints_the_bench_info(tmp_path):
    """`remove_bench` returning False means the operator kept the bench; it gets described."""
    harness = _Harness(_config(tmp_path), tmp_path)
    harness.remove_status = False
    orchestrator = harness.reraising_orchestrator()
    orchestrator._phase6_install_apps = harness.events.hook("phase6_install_apps", result=False)

    orchestrator.create_bench()

    harness.events.before("remove_bench(default_choice=False)", "info")


def test_a_phase_five_failure_never_reaches_phase_six(tmp_path):
    """Every phase exception funnels into `_handle_creation_failure`, and the phases after the
    failure do not run."""
    harness = _Harness(_config(tmp_path), tmp_path)
    orchestrator = harness.orchestrator()
    orchestrator._phase5_finalize = MagicMock(side_effect=RuntimeError("workers exploded"))

    orchestrator.create_bench()

    assert harness.events.has("phase6_install_apps") is False
    orchestrator._handle_creation_failure.assert_called_once()
    assert isinstance(orchestrator._handle_creation_failure.call_args[0][0], RuntimeError)


# --------------------------------------------------------------------------- phase 1, for real


def test_phase_one_creates_the_bench_directory_before_generating_the_compose_file(tmp_path):
    """The compose generator writes into the bench directory, so the directory comes first."""
    harness = _Harness(_config(tmp_path), tmp_path)

    harness.reraising_orchestrator(real=("_phase1_prepare_structure",)).create_bench()

    assert harness.root.is_dir()
    harness.events.before("generate_compose(bench_dir_exists=True)", "create_compose_dirs")


def test_phase_one_stamps_the_environment_into_the_compose_inputs(tmp_path):
    """FRAPPE_ENV is injected into the frappe service's environment, not left to the template."""
    harness = _Harness(_config(tmp_path), tmp_path)

    harness.reraising_orchestrator(real=("_phase1_prepare_structure",)).create_bench()

    inputs = harness.bench.generate_compose.call_args[0][0]
    assert inputs["environment"]["frappe"]["FRAPPE_ENV"] == "prod"


def test_phase_one_prepares_everything_before_anything_connects(tmp_path):
    """Directories and the compose file exist before the first container command, which is what
    lets the probe reach the server over the bench's real networks."""
    harness = _Harness(_config(tmp_path, external=True), tmp_path)
    orchestrator = harness.reraising_orchestrator(real=("_phase1_prepare_structure",))

    orchestrator.create_bench()

    harness.events.before("create_compose_dirs", "external_database_gate")
    harness.events.before("create_compose_dirs", "phase3_start_and_verify_bench")


def test_phase_one_copies_runtimes_only_for_a_plain_mount_create(tmp_path):
    """Seeded and image creates get their runtimes from the image; pre-copying from the base image
    would version-mismatch the venv."""
    plain = _Harness(_config(tmp_path / "a"), tmp_path / "a")
    seeded = _Harness(_config(tmp_path / "b", seed_image="ghcr.io/fm/seed:v1"), tmp_path / "b")
    imaged = _Harness(_config(tmp_path / "c", runtime="image"), tmp_path / "c")

    for harness in (plain, seeded, imaged):
        harness.reraising_orchestrator(real=("_phase1_prepare_structure",)).create_bench()

    assert plain.events.only("create_compose_dirs") == ["create_compose_dirs(copy_runtimes=True)"]
    assert seeded.events.only("create_compose_dirs") == ["create_compose_dirs(copy_runtimes=False)"]
    assert imaged.events.only("create_compose_dirs") == ["create_compose_dirs(copy_runtimes=False)"]


def test_phase_one_pulls_a_base_image_only_when_it_is_not_already_local(tmp_path):
    harness = _Harness(_config(tmp_path), tmp_path)
    harness.config.base_image = "ghcr.io/fm/base:v2"

    harness.reraising_orchestrator(real=("_phase1_prepare_structure",)).create_bench()

    assert harness.events.only("pull") == ["pull(ghcr.io/fm/base:v2)"]
    harness.events.before("pull(ghcr.io/fm/base:v2)", "create_compose_dirs")


def test_phase_one_skips_the_pull_when_the_base_image_is_present(tmp_path):
    """Presence is matched on repository AND tag, split on the LAST colon."""
    harness = _Harness(_config(tmp_path), tmp_path)
    harness.config.base_image = "ghcr.io/fm/base:v2"
    harness.bench.docker_client.images.return_value = [{"Repository": "ghcr.io/fm/base", "Tag": "v2"}]

    harness.reraising_orchestrator(real=("_phase1_prepare_structure",)).create_bench()

    assert harness.events.has("pull") is False


def test_phase_one_pulls_when_only_a_different_tag_of_the_base_image_is_local(tmp_path):
    harness = _Harness(_config(tmp_path), tmp_path)
    harness.config.base_image = "ghcr.io/fm/base:v2"
    harness.bench.docker_client.images.return_value = [{"Repository": "ghcr.io/fm/base", "Tag": "v1"}]

    harness.reraising_orchestrator(real=("_phase1_prepare_structure",)).create_bench()

    assert harness.events.only("pull") == ["pull(ghcr.io/fm/base:v2)"]


# --------------------------------------------------------------------------- phase 2, for real


def test_phase_two_writes_the_common_config_and_supervisor_before_provisioning(tmp_path, monkeypatch):
    """Cloning and installing happen against a bench whose common_site_config.json and supervisor
    configs are already in place."""
    harness = _Harness(_config(tmp_path), tmp_path)
    monkeypatch.setattr(
        "frappe_manager.site_manager.modules.bench_orchestrator.provision",
        lambda *_a, **kw: harness.events.append(f"provision(use_run={kw['use_run']})"),
    )

    harness.reraising_orchestrator(real=("_phase2_initialize_bench",)).create_bench()

    harness.events.before("common_site_config", "setup_supervisor")
    harness.events.before("setup_supervisor", "provision(use_run=True)")
    harness.events.before("provision(use_run=True)", "phase3_start_and_verify_bench")


def test_phase_two_provisions_through_one_off_containers(tmp_path, monkeypatch):
    """`use_run=True` everywhere in phase 2: no persistent container exists yet."""
    harness = _Harness(_config(tmp_path), tmp_path)
    captured: dict = {}
    monkeypatch.setattr(
        "frappe_manager.site_manager.modules.bench_orchestrator.provision",
        lambda *args, **kwargs: captured.update(args=args, kwargs=kwargs),
    )

    harness.reraising_orchestrator(real=("_phase2_initialize_bench",)).create_bench()

    assert captured["kwargs"]["use_run"] is True
    assert captured["args"][1] == harness.config.apps_list
    assert harness.bench.supervisor.setup_supervisor.call_args.kwargs == {"force": True, "use_run": True}


# --------------------------------------------------------------------------- phase 3, for real


def test_phase_three_starts_the_containers_then_waits_then_verifies(tmp_path):
    harness = _Harness(_config(tmp_path), tmp_path)

    harness.reraising_orchestrator(real=("_phase3_start_and_verify_bench",)).create_bench()

    harness.events.before("compose_up", "wait_for_services")
    harness.events.before("wait_for_services", "compose_exec")
    harness.events.before("compose_exec", "phase4_create_site")


def test_phase_three_never_pulls_and_never_force_recreates(tmp_path):
    """Every image is local by now; a pull here would be a surprise network call mid-create."""
    harness = _Harness(_config(tmp_path), tmp_path)

    harness.reraising_orchestrator(real=("_phase3_start_and_verify_bench",)).create_bench()

    kwargs = harness.bench.docker_client.compose.up.call_args.kwargs
    assert kwargs["pull"] == "never"
    assert kwargs["force_recreate"] is False
    assert kwargs["detach"] is True


def test_the_server_check_refuses_to_start_without_supervisord(tmp_path):
    harness = _Harness(_config(tmp_path), tmp_path)
    harness.bench.supervisor.is_supervisord_running.return_value = False
    orchestrator = harness.orchestrator()

    with pytest.raises(Exception, match="Supervisord not running"):
        orchestrator.verify_bench_server_responding()

    assert harness.events.has("compose_exec") is False


@pytest.mark.parametrize("status", ["200", "404"])
def test_the_server_check_accepts_a_site_that_is_not_there_yet(tmp_path, status):
    """Phase 3 runs BEFORE the site exists, so a 404 is the expected healthy answer."""
    harness = _Harness(_config(tmp_path), tmp_path)
    harness.bench.docker_client.compose.exec.side_effect = lambda **_kw: SubprocessOutput(
        stdout=[status], stderr=[], combined=[status], exit_code=0
    )

    harness.orchestrator().verify_bench_server_responding()

    assert harness.bench.docker_client.compose.exec.call_count == 1


@pytest.mark.timeout(15)
def test_the_server_check_retries_thirty_times_before_giving_up(tmp_path):
    """A wrong status is retried, not accepted, and the final failure is raised rather than
    logged."""
    harness = _Harness(_config(tmp_path), tmp_path)
    harness.bench.docker_client.compose.exec.side_effect = lambda **_kw: SubprocessOutput(
        stdout=["502"], stderr=[], combined=["502"], exit_code=0
    )

    with pytest.raises(Exception, match="Bench server not responding"):
        harness.orchestrator().verify_bench_server_responding()

    assert harness.bench.docker_client.compose.exec.call_count == 30


@pytest.mark.timeout(15)
def test_the_server_check_survives_a_raising_exec(tmp_path):
    """An exec that throws is a retry, not a create failure."""
    harness = _Harness(_config(tmp_path), tmp_path)
    harness.bench.docker_client.compose.exec.side_effect = RuntimeError("container not up")

    with pytest.raises(Exception, match="Bench server not responding"):
        harness.orchestrator().verify_bench_server_responding()

    assert harness.bench.docker_client.compose.exec.call_count == 30


# --------------------------------------------------------------------------- the gate, for real


def test_the_gate_is_a_noop_for_a_bench_on_the_global_db(tmp_path, monkeypatch):
    """No `[database]` entry: no probe, no per-site config file, and the create runs exactly the
    phases it has always run."""
    harness = _Harness(_config(tmp_path), tmp_path)
    harness.stage_one_returns(monkeypatch, ABSENT)
    orchestrator = harness.reraising_orchestrator(real=("_external_database_gate",))

    orchestrator.create_bench()

    assert harness.probe_calls == []
    assert harness.written_site_configs == []
    assert orchestrator._external_flow is None


def test_the_gate_probes_before_phase_two_and_writes_the_site_file_after_deciding(tmp_path, monkeypatch):
    """The per-site `site_config.json` is the only per-site config source Frappe reads and TLS has
    no CLI flag, so it is written in the gate -- after the flow decision, and long before anything
    connects with it."""
    harness = _Harness(_config(tmp_path, external=True), tmp_path)
    harness.stage_one_returns(monkeypatch, EMPTY)

    harness.reraising_orchestrator(real=("_external_database_gate",)).create_bench()

    harness.events.before("phase1_prepare_structure", "probe_stage_one")
    harness.events.before("probe_stage_one", "write_site_config")
    harness.events.before("write_site_config", "phase2_initialize_bench")


def test_the_site_file_is_written_before_new_site_runs(tmp_path, monkeypatch):
    """The bug this pins: a config file written after the thing that reads it. `new-site` and the
    direct `setup_database` call both read this file."""
    harness = _Harness(_config(tmp_path, external=True), tmp_path)
    harness.stage_one_returns(monkeypatch, EMPTY)
    harness.stage_two_returns(monkeypatch, EMPTY)

    harness.reraising_orchestrator(real=("_external_database_gate", "_phase4_create_site")).create_bench()

    harness.events.before("write_site_config", "new-site(force=False)")


def test_the_gate_marks_the_provisioning_path_in_the_site_file(tmp_path, monkeypatch):
    """`rds_db` only goes in on the provisioning path: `grant_all_privileges` is the one thing in
    Frappe that reads it, and only `setup_database` reaches it."""
    provisioning = _Harness(_config(tmp_path / "a", external=True), tmp_path / "a")
    provisioning.config.db_admin_user = ADMIN_USER
    provisioning.config.db_admin_password = ADMIN_PASSWORD
    provisioning.config.db_password_generated = True
    provisioning.stage_one_returns(monkeypatch, ABSENT)
    provisioning.reraising_orchestrator(real=("_external_database_gate",)).create_bench()

    adopting = _Harness(_config(tmp_path / "b", external=True), tmp_path / "b")
    adopting.stage_one_returns(monkeypatch, EMPTY)
    adopting.reraising_orchestrator(real=("_external_database_gate",)).create_bench()

    assert provisioning.written_site_configs[0]["rds_db"] == 1
    assert "rds_db" not in adopting.written_site_configs[0]
    assert adopting.written_site_configs[0]["db_name"] == SCHEMA


def test_the_gate_withholds_a_password_fm_minted_itself_from_the_probe(tmp_path, monkeypatch):
    """Only a password the OPERATOR supplied can be authenticated. Offering fm's own generated one
    would fail the credentials check and suppress the refusal that catches a pre-existing login."""
    harness = _Harness(_config(tmp_path, external=True), tmp_path)
    harness.config.db_password_generated = True
    harness.config.db_admin_user = ADMIN_USER
    harness.config.db_admin_password = ADMIN_PASSWORD
    harness.stage_one_returns(monkeypatch, ABSENT)

    harness.reraising_orchestrator(real=("_external_database_gate",)).create_bench()

    assert harness.probe_calls[0]["site_password"] is None
    assert harness.probe_calls[0]["admin_user"] == ADMIN_USER
    assert harness.probe_calls[0]["attach"] is False
    assert harness.probe_calls[0]["schema"] == SCHEMA
    assert harness.probe_calls[0]["bench_apps"] == ("erpnext",)


def test_an_operator_supplied_password_is_handed_to_the_probe(tmp_path, monkeypatch):
    harness = _Harness(_config(tmp_path, external=True), tmp_path)
    harness.stage_one_returns(monkeypatch, EMPTY)

    harness.reraising_orchestrator(real=("_external_database_gate",)).create_bench()

    assert harness.probe_calls[0]["site_password"] == SITE_PASSWORD


def test_the_gate_installs_the_ca_before_it_probes(tmp_path, monkeypatch):
    """TLS has to be in place for the probe itself, and the probe is told where the CA landed."""
    ca_file = tmp_path / "rds-ca.pem"
    ca_file.write_text("-----BEGIN CERTIFICATE-----\n")
    harness = _Harness(_config(tmp_path, external=True, ca=str(ca_file)), tmp_path)
    installs: list = []
    monkeypatch.setattr(
        db_tls,
        "install_site_ca",
        lambda path, site, ca: installs.append((path, site, ca)) or harness.events.append("install_site_ca"),
    )
    harness.stage_one_returns(monkeypatch, EMPTY)

    harness.reraising_orchestrator(real=("_external_database_gate",)).create_bench()

    assert installs == [(harness.root, SITE, Path(str(ca_file)))]
    harness.events.before("install_site_ca", "probe_stage_one")
    assert harness.probe_calls[0]["mysql_home"] == db_tls.site_mysql_home(SITE)


def test_without_a_ca_no_mysql_home_is_passed(tmp_path, monkeypatch):
    harness = _Harness(_config(tmp_path, external=True), tmp_path)
    harness.stage_one_returns(monkeypatch, EMPTY)

    harness.reraising_orchestrator(real=("_external_database_gate",)).create_bench()

    assert harness.probe_calls[0]["mysql_home"] is None


def test_a_refused_preflight_stops_before_the_site_file_is_written(tmp_path, monkeypatch):
    """A non-empty, non-Frappe schema without `--attach-existing-site`: `decide_flow` refuses and
    the gate raises. Nothing has been written yet, which is the point of probing here."""
    harness = _Harness(_config(tmp_path, external=True), tmp_path)
    harness.stage_one_returns(monkeypatch, {"exists": True, "table_count": 7})
    orchestrator = harness.reraising_orchestrator(real=("_external_database_gate",))

    with pytest.raises(BenchOperationException, match="will not create a site in a"):
        orchestrator.create_bench()

    assert harness.written_site_configs == []
    assert harness.events.has("phase2_initialize_bench") is False
    assert orchestrator._external_flow is None


def test_the_gate_reports_every_check_by_severity(tmp_path, monkeypatch):
    """One pass/fail verdict hides which of a dozen separately actionable things is wrong, so each
    check is printed on its own line and failures go to the error channel."""
    harness = _Harness(_config(tmp_path, external=True), tmp_path)
    checks = (
        db_probe.ProbeCheck(db_probe.CHECK_CONNECT, db_probe.CheckStatus.ok, "connected"),
        db_probe.ProbeCheck(db_probe.CHECK_TLS_IN_FORCE, db_probe.CheckStatus.warn, "plaintext"),
        db_probe.ProbeCheck(db_probe.CHECK_CHARACTER_SET, db_probe.CheckStatus.fail, "latin1"),
    )
    harness.stage_one_returns(monkeypatch, {"exists": True, "table_count": 0, "checks": checks})
    orchestrator = harness.reraising_orchestrator(real=("_external_database_gate",))

    with pytest.raises(BenchOperationException, match="preflight refused"):
        orchestrator.create_bench()

    assert f"{db_probe.CHECK_CHARACTER_SET}: latin1" in str(harness.output.display_error.call_args_list)
    assert f"{db_probe.CHECK_TLS_IN_FORCE}: plaintext" in str(harness.output.warning.call_args_list)
    assert f"{db_probe.CHECK_CONNECT}: connected" in str(harness.output.print.call_args_list)


def test_a_database_entry_that_vanishes_mid_create_is_an_operation_failure(tmp_path):
    """`_external_database` is only called once the gate ran, so a missing entry here means the
    config changed under the create rather than a bench on the global db."""
    harness = _Harness(_config(tmp_path), tmp_path)
    orchestrator = harness.orchestrator()

    with pytest.raises(BenchOperationException, match="went missing mid-create"):
        orchestrator._external_database()


# --------------------------------------------------------------------------- the probe runner


def test_stage_one_runs_through_a_throwaway_container_on_the_benchs_own_networks(tmp_path):
    """`compose run --rm` on the `frappe` service, which is what puts the probe on the bench's real
    networks before any container is up."""
    harness = _Harness(_config(tmp_path, external=True), tmp_path)
    orchestrator = harness.orchestrator()

    orchestrator._probe_runner(use_run=True)("mariadb -e 'SELECT 1'")

    kwargs = harness.compose_run_kwargs[0]
    assert kwargs["service"] == "frappe"
    assert kwargs["rm"] is True
    assert kwargs["entrypoint"] == "/exec-entrypoint.sh"
    assert kwargs["stream"] is False
    assert "export PATH=/workspace/frappe-bench/env/bin:$PATH" in kwargs["command"]
    assert "SELECT 1" in kwargs["command"]
    assert harness.compose_exec_kwargs == []


def test_stage_two_runs_in_the_container_that_is_already_up(tmp_path):
    """By phase 4 the containers are running, so an exec is both cheaper and closer to how the
    site itself connects -- as the container user, from the bench directory."""
    harness = _Harness(_config(tmp_path, external=True), tmp_path)
    orchestrator = harness.orchestrator()

    orchestrator._probe_runner(use_run=False)("python -c 'import pymysql'")

    kwargs = harness.compose_exec_kwargs[0]
    assert kwargs["service"] == "frappe"
    assert kwargs["user"] == "frappe"
    assert kwargs["workdir"] == "/workspace/frappe-bench"
    assert harness.compose_run_kwargs == []


def test_the_probe_command_survives_its_own_quotes(tmp_path):
    """Both `compose.run` and `compose.exec` shlex-split what they are handed and every probe
    command carries quotes of its own, so the payload is quoted as a single argument."""
    import shlex

    harness = _Harness(_config(tmp_path, external=True), tmp_path)

    harness.orchestrator()._probe_runner(use_run=True)("mariadb -e \"SELECT 'a b'\"")

    argv = shlex.split(harness.compose_run_kwargs[0]["command"])
    assert argv[:2] == ["/bin/bash", "-c"]
    assert argv[2].endswith("mariadb -e \"SELECT 'a b'\"")
    assert len(argv) == 3


def test_a_failing_probe_command_answers_with_its_output_not_an_exception(tmp_path):
    """The client's own `ERROR <code>` line is where the whole diagnosis lives, and `db_probe`
    parses it, so a non-zero exit is handed back as text."""
    harness = _Harness(_config(tmp_path, external=True), tmp_path)
    output = SubprocessOutput(
        stdout=[],
        stderr=[],
        combined=[" Container app-frappe-run-1 Creating", "ERROR 1045 (28000): Access denied"],
        exit_code=1,
    )
    harness.bench.docker_client.compose.run.side_effect = DockerException(["docker", "compose", "run"], output)

    reply = harness.orchestrator()._probe_runner(use_run=True)("mariadb -e 'SELECT 1'")

    assert reply == "ERROR 1045 (28000): Access denied"


def test_an_exception_without_captured_output_falls_back_to_its_message(tmp_path):
    harness = _Harness(_config(tmp_path, external=True), tmp_path)
    harness.bench.docker_client.compose.run.side_effect = DockerException(
        ["docker", "compose", "run"], SubprocessOutput(stdout=[], stderr=[], combined=[], exit_code=1)
    )

    reply = harness.orchestrator()._probe_runner(use_run=True)("mariadb -e 'SELECT 1'")

    assert reply == ""


def test_compose_lifecycle_narration_never_reaches_the_probe(tmp_path):
    """The probe parses the FIRST line positionally, so a lifecycle line would be read as the
    query result: a schema that exists gets reported absent."""
    harness = _Harness(_config(tmp_path, external=True), tmp_path)
    harness.bench.docker_client.compose.run.side_effect = lambda **_kw: SubprocessOutput(
        stdout=[],
        stderr=[],
        combined=[" Container app-frappe-run-1 Creating", "Network app_default  Created", "app_prod"],
        exit_code=0,
    )

    reply = harness.orchestrator()._probe_runner(use_run=True)("mariadb -e 'SHOW DATABASES'")

    assert reply == "app_prod"


# --------------------------------------------------------------------------- phase 4 + provisioning


def test_provisioning_happens_in_phase_four_after_a_fresh_recheck(tmp_path, monkeypatch):
    """Provisioning is deliberately NOT done at probe time: phases 2 and 3 take minutes and a
    failure in either would strand a schema fm cannot clean up on a later run. The cost is a stale
    verdict, so it is re-taken immediately before the write."""
    harness = _Harness(_config(tmp_path, external=True), tmp_path)
    harness.config.db_password_generated = True
    harness.config.db_admin_user = ADMIN_USER
    harness.config.db_admin_password = ADMIN_PASSWORD
    harness.stage_one_returns(monkeypatch, ABSENT)

    harness.reraising_orchestrator(
        real=(
            "_external_database_gate",
            "_phase4_create_site",
            "_recheck_external_schema",
            "_provision_external_schema",
        )
    ).create_bench()

    assert harness.events.only("probe_stage_one", "provision_external_schema", "new-site") == [
        "probe_stage_one",
        "probe_stage_one",
        f"provision_external_schema(admin_user={ADMIN_USER},site={SITE})",
        "new-site(force=False)",
    ]
    harness.events.before("phase3_start_and_verify_bench", "provision_external_schema")


def test_the_recheck_uses_stage_one_while_provisioning_because_the_login_does_not_exist_yet(tmp_path, monkeypatch):
    """On the provisioning path the site login is not on the server yet, so the re-check re-runs
    stage one with the admin credentials rather than stage two's pymysql connection."""
    harness = _Harness(_config(tmp_path, external=True), tmp_path)
    harness.config.db_password_generated = True
    harness.config.db_admin_user = ADMIN_USER
    harness.config.db_admin_password = ADMIN_PASSWORD
    harness.stage_one_returns(monkeypatch, ABSENT)
    harness.stage_two_returns(monkeypatch, ABSENT)

    harness.reraising_orchestrator(
        real=(
            "_external_database_gate",
            "_phase4_create_site",
            "_recheck_external_schema",
            "_provision_external_schema",
        )
    ).create_bench()

    assert harness.stage_two_calls == []
    assert len(harness.probe_calls) == 2
    assert harness.probe_calls[1]["admin_user"] == ADMIN_USER
    assert "site_password" not in harness.probe_calls[1]


def test_the_recheck_uses_stage_two_when_adopting_an_empty_schema(tmp_path, monkeypatch):
    """The site login already exists there, so the re-check runs the exact driver and config the
    site itself will use: pymysql out of the bench venv, reading the site file."""
    harness = _Harness(_config(tmp_path, external=True), tmp_path)
    harness.stage_one_returns(monkeypatch, EMPTY)
    harness.stage_two_returns(monkeypatch, EMPTY)

    harness.reraising_orchestrator(
        real=("_external_database_gate", "_phase4_create_site", "_recheck_external_schema")
    ).create_bench()

    assert harness.stage_two_calls == [{"site": SITE, "schema": SCHEMA}]
    assert len(harness.probe_calls) == 1
    assert harness.events.has("provision_external_schema") is False


def test_the_recheck_stops_the_create_when_the_schema_changed_under_it(tmp_path, monkeypatch):
    """Empty at probe time, populated by phase 4: this is the check standing between `--force` and
    someone's data, so it refuses instead of proceeding on the stale verdict."""
    harness = _Harness(_config(tmp_path, external=True), tmp_path)
    harness.stage_one_returns(monkeypatch, EMPTY)
    harness.stage_two_returns(monkeypatch, {"exists": True, "table_count": 412, "is_frappe": True})

    orchestrator = harness.reraising_orchestrator(
        real=("_external_database_gate", "_phase4_create_site", "_recheck_external_schema")
    )
    with pytest.raises(BenchOperationException, match="no longer what the preflight found"):
        orchestrator.create_bench()

    assert harness.events.has("new-site") is False


def test_a_recheck_that_now_wants_a_different_flow_is_also_a_refusal(tmp_path, monkeypatch):
    """The schema fm was about to create appeared in between. Same refusal: the flow no longer
    matches the one the create was planned around."""
    harness = _Harness(_config(tmp_path, external=True), tmp_path)
    harness.config.db_password_generated = True
    harness.config.db_admin_user = ADMIN_USER
    harness.config.db_admin_password = ADMIN_PASSWORD
    harness.stage_one_returns(monkeypatch, ABSENT, EMPTY)

    orchestrator = harness.reraising_orchestrator(
        real=(
            "_external_database_gate",
            "_phase4_create_site",
            "_recheck_external_schema",
            "_provision_external_schema",
        )
    )
    with pytest.raises(BenchOperationException, match="no longer what the preflight found"):
        orchestrator.create_bench()

    assert harness.events.has("provision_external_schema") is False
    assert harness.events.has("new-site") is False


def test_provisioning_without_admin_credentials_refuses_with_the_flags_to_pass(tmp_path, monkeypatch):
    """An absent schema and only `--db-password`: fm has nothing to create the schema with, and it
    says so before touching the server."""
    harness = _Harness(_config(tmp_path, external=True), tmp_path)
    harness.stage_one_returns(monkeypatch, ABSENT)

    orchestrator = harness.reraising_orchestrator(
        real=(
            "_external_database_gate",
            "_phase4_create_site",
            "_recheck_external_schema",
            "_provision_external_schema",
        )
    )
    with pytest.raises(BenchOperationException, match="--db-admin-user together with --db-admin-password"):
        orchestrator.create_bench()

    assert harness.events.has("provision_external_schema") is False
    assert harness.events.has("new-site") is False


def test_only_a_completed_provision_is_remembered_as_undoable(tmp_path, monkeypatch):
    """`_provisioned` is what a later failure offers to drop, so it is set AFTER the provisioning
    call returns -- never before it, and never on the adopt path."""
    provisioned = _Harness(_config(tmp_path / "a", external=True), tmp_path / "a")
    provisioned.config.db_password_generated = True
    provisioned.config.db_admin_user = ADMIN_USER
    provisioned.config.db_admin_password = ADMIN_PASSWORD
    provisioned.stage_one_returns(monkeypatch, ABSENT)
    orchestrator = provisioned.reraising_orchestrator(
        real=(
            "_external_database_gate",
            "_phase4_create_site",
            "_recheck_external_schema",
            "_provision_external_schema",
        )
    )
    orchestrator.create_bench()
    assert orchestrator._provisioned is not None
    assert orchestrator._provisioned.name == SCHEMA

    adopted = _Harness(_config(tmp_path / "b", external=True), tmp_path / "b")
    adopted.stage_one_returns(monkeypatch, EMPTY)
    adopted.stage_two_returns(monkeypatch, EMPTY)
    adopting = adopted.reraising_orchestrator(
        real=(
            "_external_database_gate",
            "_phase4_create_site",
            "_recheck_external_schema",
            "_provision_external_schema",
        )
    )
    adopting.create_bench()
    assert adopting._provisioned is None


def test_a_failed_provisioning_call_leaves_nothing_to_offer(tmp_path, monkeypatch):
    """The exception propagates and `_provisioned` stays None: fm cannot name a schema it does not
    know it created."""
    harness = _Harness(_config(tmp_path, external=True), tmp_path)
    harness.config.db_password_generated = True
    harness.config.db_admin_user = ADMIN_USER
    harness.config.db_admin_password = ADMIN_PASSWORD
    harness.stage_one_returns(monkeypatch, ABSENT)
    harness.bench.site_manager.provision_external_schema.side_effect = RuntimeError("setup_database blew up")

    orchestrator = harness.reraising_orchestrator(
        real=(
            "_external_database_gate",
            "_phase4_create_site",
            "_recheck_external_schema",
            "_provision_external_schema",
        )
    )
    with pytest.raises(RuntimeError, match="setup_database blew up"):
        orchestrator.create_bench()

    assert orchestrator._provisioned is None
    assert harness.events.has("new-site") is False


def test_a_global_db_create_neither_rechecks_nor_provisions(tmp_path):
    """With no external flow decided, phase 4 goes straight to `new-site`."""
    harness = _Harness(_config(tmp_path), tmp_path)

    harness.reraising_orchestrator(real=("_phase4_create_site",)).create_bench()

    assert harness.events.only("recheck_external_schema", "provision_external_schema", "new-site") == [
        "new-site(force=False)"
    ]


def test_phase_four_writes_the_admin_password_and_syncs_after_creating_the_site(tmp_path):
    harness = _Harness(_config(tmp_path), tmp_path)

    harness.reraising_orchestrator(real=("_phase4_create_site",)).create_bench()

    harness.events.before("new-site(force=False)", "set_bench_site_config")
    harness.events.before("set_bench_site_config", "sync_bench_config_configuration")
    assert "admin_password" in harness.bench.set_bench_site_config.call_args[0][0]


# --------------------------------------------------------------------------- attach


@pytest.fixture
def attach_harness(monkeypatch, tmp_path):
    """A create that the gate decides is an attach: `--attach-existing-site` and a schema that
    already holds a Frappe site. `decide_flow` is the real one."""

    def build(**config_kwargs) -> _Harness:
        harness = _Harness(_config(tmp_path, external=True, **config_kwargs), tmp_path)
        harness.config.attach_existing_site = True
        harness.stage_one_returns(monkeypatch, FRAPPE_SITE)
        return harness

    return build


def test_attach_turns_migrate_off_with_the_decision_not_after_the_pipeline(attach_harness):
    """A create that dies in a later phase still leaves the bench directory and its `[database]`
    entry on disk. Writing this flag last produced exactly the bench it exists to protect:
    attached to someone's data with migrate still on. So it lands in the gate, before phase 2."""
    harness = attach_harness()

    harness.reraising_orchestrator(real=("_external_database_gate",)).create_bench()

    harness.events.before("save_bench_config(migrate=False)", "phase2_initialize_bench")
    harness.events.before("save_bench_config(migrate=False)", "phase3_start_and_verify_bench")
    harness.events.before("save_bench_config(migrate=False)", "phase5_finalize")
    assert harness.saved_switch[0] is False


def test_attach_writes_migrate_false_even_when_phase_five_kills_the_create(attach_harness):
    """The measured failure: the flag is already on disk by the time a later phase fails."""
    harness = attach_harness()
    orchestrator = harness.orchestrator(real=("_external_database_gate",))

    def _die(*_a, **_k):
        harness.events.append("phase5_finalize")
        raise RuntimeError("boom")

    orchestrator._phase5_finalize = MagicMock(side_effect=_die)

    orchestrator.create_bench()

    assert harness.saved_switch[0] is False
    harness.events.before("save_bench_config(migrate=False)", "phase5_finalize")
    orchestrator._handle_creation_failure.assert_called_once()


def test_attach_creates_a_switch_section_when_the_config_has_none(attach_harness):
    harness = attach_harness()
    assert harness.config.switch is None

    harness.reraising_orchestrator(real=("_external_database_gate",)).create_bench()

    assert harness.config.switch is not None
    assert harness.config.switch.migrate is False


def test_attach_only_flips_migrate_and_leaves_the_rest_of_switch_alone(attach_harness):
    """An existing `[switch]` section is edited, not replaced: the other settings are the
    operator's."""
    harness = attach_harness()
    harness.config.switch = SwitchConfig(migrate="auto", migrate_timeout=900)

    harness.reraising_orchestrator(real=("_external_database_gate",)).create_bench()

    assert harness.config.switch.migrate is False
    assert harness.config.switch.migrate_timeout == 900


def test_attach_phase_four_builds_only_the_directories(attach_harness):
    """A Frappe site is a directory plus a database, and the database is already there. No
    `new-site` in any form, no re-check, and no `admin_password` recorded -- fm did not set this
    site's Administrator password."""
    harness = attach_harness()

    harness.reraising_orchestrator(
        real=("_external_database_gate", "_phase4_create_site", "_recheck_external_schema", "_attach_existing_site")
    ).create_bench()

    assert harness.events.only("create_site_dirs", "new-site", "probe_stage_two", "set_bench_site_config") == [
        "create_site_dirs"
    ]
    harness.events.before("create_site_dirs", "sync_bench_config_configuration")


def test_attach_skips_phase_six_and_calls_that_a_success(attach_harness):
    """Both `install-app` and `migrate` write to the database, which is the one thing attach
    promises not to do. Nothing ran, so nothing failed: the bench is complete and is described
    rather than offered for removal."""
    harness = attach_harness()

    harness.reraising_orchestrator(real=("_external_database_gate", "_skip_phase6_for_attach")).create_bench()

    assert harness.events.has("phase6_install_apps") is False
    assert harness.events.has("install_apps_to_site") is False
    assert harness.events.has("remove_bench") is False
    assert harness.events.has("info") is True


def test_the_phase_six_skip_tells_the_operator_how_to_migrate_by_hand(tmp_path):
    """Reconciling the schema against this bench's apps is the operator's call to make, so the
    skip hands over the exact two commands and returns success."""
    harness = _Harness(_config(tmp_path, external=True), tmp_path)
    orchestrator = harness.orchestrator(real=("_skip_phase6_for_attach",))

    assert orchestrator._skip_phase6_for_attach() is True

    printed = " ".join(str(call) for call in harness.output.print.call_args_list)
    assert f"fm shell {SITE}" in printed
    assert f"bench --site {SITE} migrate" in printed
    assert harness.events.has("install_apps_to_site") is False


def test_attach_warns_about_a_missing_encryption_key_without_refusing(attach_harness):
    """Attach writes nothing, so everything here is recoverable afterwards and a create has no
    business second-guessing the operator about it."""
    harness = attach_harness()

    harness.reraising_orchestrator(real=("_external_database_gate",)).create_bench()

    warned = " ".join(str(call) for call in harness.output.warning.call_args_list)
    assert "no encryption key provided" in warned
    assert harness.events.has("phase2_initialize_bench") is True


def test_a_supplied_encryption_key_silences_that_warning(attach_harness):
    harness = attach_harness()
    harness.config.encryption_key = "kEyOfSomeSort"

    harness.reraising_orchestrator(real=("_external_database_gate",)).create_bench()

    warned = " ".join(str(call) for call in harness.output.warning.call_args_list)
    assert "no encryption key provided" not in warned


def test_attach_warns_when_another_bench_already_points_at_the_schema(attach_harness, monkeypatch, tmp_path):
    """Frappe prefixes its redis keys with db_name and not with the site name, so two benches on
    one schema share cache keys."""
    harness = attach_harness()
    benches = tmp_path / "fm-benches"
    for name, data in (
        ("other.example.com", f'{{"db_name": "{SCHEMA}", "db_host": "{DB_HOST}"}}'),
        ("unrelated.example.com", '{"db_name": "other_db", "db_host": "elsewhere"}'),
        ("broken.example.com", "{not json"),
    ):
        site_dir = benches / name / "workspace" / "frappe-bench" / "sites" / name
        site_dir.mkdir(parents=True)
        (site_dir / "site_config.json").write_text(data)
    monkeypatch.setattr("frappe_manager.CLI_BENCHES_DIRECTORY", benches)

    harness.reraising_orchestrator(real=("_external_database_gate",)).create_bench()

    warned = " ".join(str(call) for call in harness.output.warning.call_args_list)
    assert f"bench other.example.com already points at {SCHEMA}" in warned
    assert "unrelated.example.com already points" not in warned
    assert "broken.example.com already points" not in warned


def test_the_bench_being_created_is_not_reported_as_sharing_with_itself(tmp_path, monkeypatch):
    harness = _Harness(_config(tmp_path, external=True), tmp_path)
    benches = tmp_path / "fm-benches"
    site_dir = benches / SITE / "workspace" / "frappe-bench" / "sites" / SITE
    site_dir.mkdir(parents=True)
    (site_dir / "site_config.json").write_text(f'{{"db_name": "{SCHEMA}", "db_host": "{DB_HOST}"}}')
    monkeypatch.setattr("frappe_manager.CLI_BENCHES_DIRECTORY", benches)
    orchestrator = harness.orchestrator()

    database = harness.config.get_database_config(SITE)
    assert orchestrator._benches_sharing_schema(database) == []


def test_no_bench_directory_at_all_is_not_an_error(tmp_path, monkeypatch):
    harness = _Harness(_config(tmp_path, external=True), tmp_path)
    monkeypatch.setattr("frappe_manager.CLI_BENCHES_DIRECTORY", tmp_path / "nope")
    orchestrator = harness.orchestrator()

    assert orchestrator._benches_sharing_schema(harness.config.get_database_config(SITE)) == []


# --------------------------------------------------------------------------- the image runtime path


def test_the_image_path_copies_apps_txt_before_it_creates_the_site_directory(tmp_path, monkeypatch):
    """The other bug this pins. `host_run_cp` writes into `sites/`, and `sites/<site>` is
    pre-created afterwards so the per-site bind is frappe-owned rather than auto-created
    root-owned by `compose up` -- which means the copy has to come first."""
    harness = _Harness(_config(tmp_path, runtime="image"), tmp_path)
    transport = _fake_image_transport(monkeypatch)

    harness.reraising_orchestrator(real=("_create_image_bench",)).create_bench()

    assert transport == ["fetch_image", "host_run_cp(site_dir_exists=False)"]
    assert (harness.sites_dir / SITE).is_dir()
    harness.events.before("external_database_gate", "phase3_start_and_verify_bench(site_dir_exists=True)")


def test_the_image_path_runs_the_gate_after_fetching_the_image_and_before_any_container(tmp_path, monkeypatch):
    """There is no phase 2 here, and the probe goes through `compose run --rm` on the app image,
    so the gate is placed at the first point this path can run a container at all -- still ahead
    of the containers, the site and every write."""
    harness = _Harness(_config(tmp_path, runtime="image"), tmp_path)
    transport = _fake_image_transport(monkeypatch)

    harness.reraising_orchestrator(real=("_create_image_bench",)).create_bench()

    assert list(harness.events) == [
        "check_images",
        "phase1_prepare_structure",
        "common_site_config",
        "setup_supervisor",
        "external_database_gate",
        "phase3_start_and_verify_bench(site_dir_exists=True)",
        "phase4_create_site(force=True)",
        "phase6_install_apps",
        "phase5_finalize",
        "workers_up",
        "info",
    ]
    assert transport[0] == "fetch_image"


def test_the_image_path_forces_new_site_over_the_directory_it_pre_created(tmp_path, monkeypatch):
    """`new-site` refuses a non-empty site directory, and this path deliberately made one."""
    harness = _Harness(_config(tmp_path, runtime="image"), tmp_path)
    _fake_image_transport(monkeypatch)

    harness.reraising_orchestrator(real=("_create_image_bench", "_phase4_create_site")).create_bench()

    assert harness.events.only("new-site") == ["new-site(force=True)"]


def test_the_image_paths_app_set_comes_from_the_baked_apps_txt(tmp_path, monkeypatch):
    """`apps_list` is replaced by the image's own apps before the gate runs, so the attach parity
    check compares against the baked set."""
    harness = _Harness(_config(tmp_path, runtime="image"), tmp_path)
    _fake_image_transport(monkeypatch, apps="frappe\nerpnext\nhrms\n\n")

    harness.reraising_orchestrator(real=("_create_image_bench",)).create_bench()

    assert [app.name for app in harness.config.apps_list] == ["frappe", "erpnext", "hrms"]


def test_the_image_path_brings_up_workers_without_starting_them_in_phase_five(tmp_path, monkeypatch):
    """Phase 5 generates the workers compose image-shaped but does not start it; the image path
    brings it up itself once the apps are in."""
    harness = _Harness(_config(tmp_path, runtime="image"), tmp_path)
    _fake_image_transport(monkeypatch)

    harness.reraising_orchestrator(real=("_create_image_bench", "_phase5_finalize")).create_bench()

    assert harness.events.only("sync_workers_compose") == ["sync_workers_compose(start=False)"]
    harness.events.before("sync_workers_compose(start=False)", "workers_up")


def test_a_mount_create_starts_its_workers_from_phase_five(tmp_path):
    harness = _Harness(_config(tmp_path), tmp_path)

    harness.reraising_orchestrator(real=("_phase5_finalize",)).create_bench()

    assert harness.events.only("sync_workers_compose") == ["sync_workers_compose(start=True)"]
    assert harness.events.has("workers_up") is False


def test_a_failed_image_create_offers_to_remove_the_bench(tmp_path, monkeypatch):
    harness = _Harness(_config(tmp_path, runtime="image"), tmp_path)
    _fake_image_transport(monkeypatch)
    orchestrator = harness.reraising_orchestrator(real=("_create_image_bench",))
    orchestrator._phase6_install_apps = harness.events.hook("phase6_install_apps", result=False)

    orchestrator.create_bench()

    assert harness.events.only("remove_bench", "info") == ["remove_bench(default_choice=False)"]


def test_a_kept_image_bench_is_described_instead(tmp_path, monkeypatch):
    """Declining the removal leaves the bench, so it gets described like any other."""
    harness = _Harness(_config(tmp_path, runtime="image"), tmp_path)
    harness.remove_status = False
    _fake_image_transport(monkeypatch)
    orchestrator = harness.reraising_orchestrator(real=("_create_image_bench",))
    orchestrator._phase6_install_apps = harness.events.hook("phase6_install_apps", result=False)

    orchestrator.create_bench()

    harness.events.before("remove_bench(default_choice=False)", "info")


# --------------------------------------------------------------------------- phase 2, seeded


def test_the_seeded_phase_two_materializes_before_it_writes_any_config(tmp_path, monkeypatch):
    """No clone, no dependency install, no asset build: the image already carries all of it at the
    paths the mount bind exposes. The workspace is materialized first, and the host-side config and
    supervisor are written onto it afterwards."""
    harness = _Harness(_config(tmp_path, seed_image="ghcr.io/fm/seed:v1"), tmp_path)
    transport = _fake_image_transport(monkeypatch)
    monkeypatch.setattr(
        "frappe_manager.site_manager.modules.bench_orchestrator.provision",
        lambda *_a, **_k: harness.events.append("provision"),
    )

    harness.reraising_orchestrator(real=("_phase2_seed_from_image",)).create_bench()

    assert transport == ["fetch_image", "materialize_workspace", "host_run_cp(site_dir_exists=False)"]
    assert harness.events.has("provision") is False
    harness.events.before("common_site_config", "setup_supervisor")
    harness.events.before("setup_supervisor", "phase3_start_and_verify_bench")


def test_the_seeded_app_set_comes_from_the_image_not_the_command_line(tmp_path, monkeypatch):
    """For a seeded create the `--apps` entries are OVERRIDES, not the bench app set: `apps_list`
    is rebuilt from the baked apps.txt and the overrides are grafted on top."""
    harness = _Harness(_config(tmp_path, seed_image="ghcr.io/fm/seed:v1"), tmp_path)
    _fake_image_transport(monkeypatch, apps="frappe\nhrms\n")
    harness.bench.app_manager.graft_apps.side_effect = lambda overrides, **kw: harness.events.append(
        f"graft_apps({[app.name for app in overrides]},stash={kw['stash']},use_run={kw['use_run']})"
    )

    harness.reraising_orchestrator(real=("_phase2_seed_from_image",)).create_bench()

    assert [app.name for app in harness.config.apps_list] == ["frappe", "hrms"]
    assert harness.events.only("graft_apps") == ["graft_apps(['erpnext'],stash=False,use_run=True)"]


def test_a_seeded_create_without_overrides_does_not_graft(tmp_path, monkeypatch):
    harness = _Harness(_config(tmp_path, seed_image="ghcr.io/fm/seed:v1"), tmp_path)
    harness.config.apps_list = []
    _fake_image_transport(monkeypatch)

    harness.reraising_orchestrator(real=("_phase2_seed_from_image",)).create_bench()

    assert harness.bench.app_manager.graft_apps.called is False


def test_a_requested_python_version_reinstalls_every_app_into_the_recreated_venv(tmp_path, monkeypatch):
    """`--python` with `--from-image` swaps the seeded toolchain, and every app -- baked plus
    overrides -- is reinstalled into it without re-cloning."""
    harness = _Harness(_config(tmp_path, seed_image="ghcr.io/fm/seed:v1"), tmp_path)
    _fake_image_transport(monkeypatch)
    harness.config.python_version = "3.11"
    harness.bench.app_manager.setup_python_and_node_environments.return_value = True
    harness.bench.app_manager.install_apps.side_effect = lambda **kw: harness.events.append(
        f"install_apps(skip_clone={kw['skip_clone']},use_run={kw['use_run']})"
    )

    harness.reraising_orchestrator(real=("_phase2_seed_from_image",)).create_bench()

    assert harness.bench.app_manager.setup_python_and_node_environments.call_args.kwargs == {
        "use_run": True,
        "recreate_python_env": True,
    }
    assert harness.events.only("install_apps") == ["install_apps(skip_clone=True,use_run=True)"]


def test_a_venv_that_already_satisfies_the_request_is_left_alone(tmp_path, monkeypatch):
    """The setup helper no-ops when the image's venv already matches, and then there is nothing to
    reinstall."""
    harness = _Harness(_config(tmp_path, seed_image="ghcr.io/fm/seed:v1"), tmp_path)
    _fake_image_transport(monkeypatch)
    harness.config.node_version = "20"
    harness.bench.app_manager.setup_python_and_node_environments.return_value = False

    harness.reraising_orchestrator(real=("_phase2_seed_from_image",)).create_bench()

    assert harness.bench.app_manager.install_apps.called is False


def test_without_a_version_request_the_seeded_toolchain_is_never_touched(tmp_path, monkeypatch):
    harness = _Harness(_config(tmp_path, seed_image="ghcr.io/fm/seed:v1"), tmp_path)
    _fake_image_transport(monkeypatch)

    harness.reraising_orchestrator(real=("_phase2_seed_from_image",)).create_bench()

    assert harness.bench.app_manager.setup_python_and_node_environments.called is False


# --------------------------------------------------------------------------- phase 5 and phase 6


def test_phase_five_verifies_the_bench_after_saving_its_config(tmp_path):
    """The migration stamp and the config write come first; the liveness check is the last thing
    phase 5 does, and it raises rather than warns."""
    harness = _Harness(_config(tmp_path), tmp_path)
    harness.bench.is_bench_created.return_value = False
    orchestrator = harness.orchestrator(real=("_phase5_finalize",))

    orchestrator.create_bench()

    harness.events.before("sync_workers_compose(start=True)", "save_bench_config(migrate=None)")
    assert harness.events.has("phase6_install_apps") is False
    orchestrator._handle_creation_failure.assert_called_once()
    assert "inactive or unresponsive" in str(orchestrator._handle_creation_failure.call_args[0][0])


def test_phase_five_stamps_the_current_fm_version_as_the_migration_state(tmp_path):
    harness = _Harness(_config(tmp_path), tmp_path)

    harness.reraising_orchestrator(real=("_phase5_finalize",)).create_bench()

    state = harness.config.migration_state
    assert state is not None
    assert state.migrated_to
    assert state.last_migration_date


def test_a_template_bench_is_stamped_and_saved_too(tmp_path):
    """The template path skips phase 5 entirely, so it does its own version stamp."""
    harness = _Harness(_config(tmp_path), tmp_path)

    harness.reraising_orchestrator(real=("_create_template_bench",)).create_bench(is_template_bench=True)

    assert harness.config.migration_state is not None
    harness.events.before("sync_common_site_config", "save_bench_config(migrate=None)")


def test_phase_six_installs_the_apps_then_migrates(tmp_path):
    harness = _Harness(_config(tmp_path), tmp_path)

    harness.reraising_orchestrator(real=("_phase6_install_apps", "_run_bench_migrate")).create_bench()

    assert harness.events.only("install_apps_to_site", "container_run") == [
        "install_apps_to_site",
        f"container_run(bench --site {SITE} migrate)",
    ]


def test_phase_six_failure_is_graceful_and_keeps_the_bench(tmp_path):
    """The bench itself is configured and running; only the site-level installs failed, so the
    error is reported as guidance and phase 6 returns False instead of raising."""
    harness = _Harness(_config(tmp_path), tmp_path)
    harness.bench.app_manager.install_apps_to_site.side_effect = RuntimeError("dependency conflict")
    orchestrator = harness.orchestrator(real=("_phase6_install_apps",))

    orchestrator.create_bench()

    warned = " ".join(str(call) for call in harness.output.warning.call_args_list)
    assert "App Installation Failed" in warned
    assert "dependency conflict" in warned
    assert harness.events.only("remove_bench") == ["remove_bench(default_choice=False)"]
    orchestrator._handle_creation_failure.assert_not_called()


def test_a_failing_migrate_does_not_fail_phase_six(tmp_path):
    """The apps ARE installed; a migrate failure is a warning with the command to re-run, and
    phase 6 still reports success."""
    harness = _Harness(_config(tmp_path), tmp_path)
    harness.bench.app_manager._container_run.side_effect = RuntimeError("migrate exploded")
    orchestrator = harness.orchestrator(real=("_phase6_install_apps", "_run_bench_migrate"))

    orchestrator.create_bench()

    warned = " ".join(str(call) for call in harness.output.warning.call_args_list)
    assert "Database migration failed" in warned
    assert harness.events.has("remove_bench") is False
    assert harness.events.has("info") is True


def test_the_migrate_command_is_built_from_the_benchs_own_cli_prefix(tmp_path):
    harness = _Harness(_config(tmp_path), tmp_path)
    harness.bench.app_manager.bench_cli_cmd = ["/workspace/frappe-bench/env/bin/bench"]

    harness.orchestrator()._run_bench_migrate()

    assert harness.events.only("container_run") == [
        f"container_run(/workspace/frappe-bench/env/bin/bench --site {SITE} migrate)"
    ]
    assert isinstance(
        harness.bench.app_manager._container_run.call_args.kwargs["on_failure"](),
        BenchOperationException,
    )


# --------------------------------------------------------------------------- failure handling


def test_a_creation_failure_offers_to_drop_the_schema_before_removing_the_bench(tmp_path):
    """`remove_bench` takes the compose file and the container with it, and the drop runs through
    the container, so the offer comes first."""
    harness = _Harness(_config(tmp_path, external=True), tmp_path)
    harness.config.db_admin_user = ADMIN_USER
    harness.config.db_admin_password = ADMIN_PASSWORD
    orchestrator = harness.orchestrator(real=("_handle_creation_failure",))
    orchestrator._provisioned = harness.config.get_database_config(SITE)
    harness.output.prompt_ask.return_value = "no"

    _fail(orchestrator, "phase 5 died")

    assert harness.output.prompt_ask.called is True
    assert harness.events.only("remove_bench") == ["remove_bench(default_choice=False)"]
    left = " ".join(str(call) for call in harness.output.print.call_args_list)
    assert f"Left schema {SCHEMA}" in left


def test_declining_the_drop_is_the_default_answer(tmp_path):
    """Declining leaves the schema in place so the create can simply be re-run, and it is both the
    default and the non-interactive answer."""
    harness = _Harness(_config(tmp_path, external=True), tmp_path)
    harness.config.db_admin_user = ADMIN_USER
    harness.config.db_admin_password = ADMIN_PASSWORD
    orchestrator = harness.orchestrator()
    orchestrator._provisioned = harness.config.get_database_config(SITE)

    orchestrator._offer_to_drop_provisioned_schema()

    kwargs = harness.output.prompt_ask.call_args.kwargs
    assert kwargs["default"] == "no"
    assert kwargs["choices"] == ["yes", "no"]
    assert harness.bench.site_manager._container_exec_argv.called is False


def test_accepting_the_drop_hands_the_admin_password_over_on_stdin(tmp_path):
    """fm authors no SQL: the drop goes through Frappe's own `DbManager`, and the password travels
    on stdin rather than a flag, a file or a process listing."""
    harness = _Harness(_config(tmp_path, external=True), tmp_path)
    harness.config.db_admin_user = ADMIN_USER
    harness.config.db_admin_password = ADMIN_PASSWORD
    orchestrator = harness.orchestrator()
    orchestrator._provisioned = harness.config.get_database_config(SITE)
    harness.output.prompt_ask.return_value = "yes"

    orchestrator._offer_to_drop_provisioned_schema()

    call = harness.bench.site_manager._container_exec_argv.call_args
    argv = call[0][0]
    assert argv[1] == "-c"
    assert f'manager.drop_database("{SCHEMA}")' in argv[2]
    assert 'manager.delete_user("app_svc", "%")' in argv[2]
    assert ADMIN_PASSWORD not in argv[2]
    assert call.kwargs["stdin_data"] == f"{ADMIN_PASSWORD}\n"
    assert call.kwargs["workdir"] == db_probe.SITES_CONTAINER_ROOT


def test_a_failed_drop_says_the_schema_is_still_there(tmp_path):
    """fm will not hold the admin credentials on any later run, so this one is the operator's to
    clean up by hand."""
    harness = _Harness(_config(tmp_path, external=True), tmp_path)
    harness.config.db_admin_user = ADMIN_USER
    harness.config.db_admin_password = ADMIN_PASSWORD
    harness.bench.site_manager._container_exec_argv.side_effect = RuntimeError("no container")
    orchestrator = harness.orchestrator()
    orchestrator._provisioned = harness.config.get_database_config(SITE)
    harness.output.prompt_ask.return_value = "yes"

    orchestrator._offer_to_drop_provisioned_schema()

    assert "It is still" in str(harness.output.display_error.call_args_list)


def test_the_drop_is_offered_once_per_run(tmp_path):
    """Whatever else fails on the way out, the operator is asked exactly once."""
    harness = _Harness(_config(tmp_path, external=True), tmp_path)
    harness.config.db_admin_user = ADMIN_USER
    harness.config.db_admin_password = ADMIN_PASSWORD
    orchestrator = harness.orchestrator()
    orchestrator._provisioned = harness.config.get_database_config(SITE)

    orchestrator._offer_to_drop_provisioned_schema()
    orchestrator._offer_to_drop_provisioned_schema()

    assert harness.output.prompt_ask.call_count == 1


def test_nothing_provisioned_means_nothing_offered(tmp_path):
    """A create that failed before provisioning, or one that adopted an existing schema, has
    nothing fm may drop."""
    harness = _Harness(_config(tmp_path, external=True), tmp_path)
    orchestrator = harness.orchestrator()

    orchestrator._offer_to_drop_provisioned_schema()

    assert harness.output.prompt_ask.called is False


def test_without_the_admin_credentials_in_memory_no_drop_is_offered(tmp_path):
    """A drop needs the credentials this run was given; without them there is nothing to ask."""
    harness = _Harness(_config(tmp_path, external=True), tmp_path)
    orchestrator = harness.orchestrator()
    orchestrator._provisioned = harness.config.get_database_config(SITE)

    orchestrator._offer_to_drop_provisioned_schema()

    assert harness.output.prompt_ask.called is False


def test_a_bench_that_never_made_it_to_disk_is_not_removed(tmp_path):
    """`bench.exists` gates the cleanup: there is nothing to remove."""
    harness = _Harness(_config(tmp_path), tmp_path)
    harness.bench.exists = False
    orchestrator = harness.orchestrator(real=("_handle_creation_failure",))

    _fail(orchestrator, "phase 1 died")

    assert harness.events.has("remove_bench") is False
    assert "phase 1 died" in str(harness.output.display_error.call_args_list)


def test_a_kept_bench_is_described_after_a_failure(tmp_path):
    harness = _Harness(_config(tmp_path), tmp_path)
    harness.remove_status = False
    orchestrator = harness.orchestrator(real=("_handle_creation_failure",))

    _fail(orchestrator, "phase 5 died")

    harness.events.before("remove_bench(default_choice=False)", "info")


# --------------------------------------------------------------------------- start_bench
#
# The other ordered workflow in this module. Same contract shape: which optional step runs under
# which flag, and in what order relative to the containers coming up.


def test_starting_a_bench_checks_its_images_then_starts_then_waits_then_saves(tmp_path):
    harness = _Harness(_config(tmp_path), tmp_path)

    harness.orchestrator().start_bench()

    assert list(harness.events) == [
        "check_images",
        "docker_ops_start(services=[],force_recreate=False)",
        "wait_for_services",
        "save_bench_config(migrate=None)",
    ]


def test_force_reaches_the_containers_and_the_workers(tmp_path):
    harness = _Harness(_config(tmp_path), tmp_path)
    harness.bench.workers.compose_file_manager.exists.return_value = True

    harness.orchestrator().start_bench(force=True)

    assert harness.events.has("docker_ops_start(services=[],force_recreate=True)") is True
    assert harness.bench.workers.docker_client.compose.up.call_args.kwargs["force_recreate"] is True


def test_the_common_site_config_is_reconfigured_before_anything_starts(tmp_path):
    """It is what the containers read on boot, so it cannot be written after they are up."""
    harness = _Harness(_config(tmp_path), tmp_path)

    harness.orchestrator().start_bench(reconfigure_common_site_config=True)

    harness.events.before("sync_common_site_config", "docker_ops_start")


def test_admin_tools_are_left_alone_when_they_are_already_running(tmp_path):
    """Without `force`, a running admin-tools stack is not recreated."""
    harness = _Harness(_config(tmp_path), tmp_path)
    harness.bench.admin_tools.compose_file_manager.compose_path.exists.return_value = True

    harness.orchestrator().start_bench()

    assert harness.events.has("admin_tools_enable") is False


def test_admin_tools_that_are_down_get_enabled(tmp_path):
    harness = _Harness(_config(tmp_path), tmp_path)
    harness.bench.admin_tools.compose_file_manager.compose_path.exists.return_value = True
    harness.bench.admin_tools.is_running.return_value = False

    harness.orchestrator().start_bench()

    assert harness.events.has("admin_tools_enable(force_recreate_container=False)") is True


def test_force_recreates_admin_tools_even_when_they_are_running(tmp_path):
    harness = _Harness(_config(tmp_path), tmp_path)
    harness.bench.admin_tools.compose_file_manager.compose_path.exists.return_value = True

    harness.orchestrator().start_bench(force=True)

    assert harness.events.has("admin_tools_enable(force_recreate_container=True)") is True


def test_an_nginx_that_the_admin_tools_took_down_is_brought_back(tmp_path):
    """Enabling admin tools rewrites the shared compose and can leave nginx stopped, so it is
    started again -- only nginx, and only when it is actually down."""
    harness = _Harness(_config(tmp_path), tmp_path)
    harness.bench.admin_tools.compose_file_manager.compose_path.exists.return_value = True
    harness.bench._is_service_running.return_value = False

    harness.orchestrator().start_bench()

    assert harness.events.only("docker_ops_start") == [
        "docker_ops_start(services=[],force_recreate=False)",
        "docker_ops_start(services=['nginx'],force_recreate=False)",
    ]
    harness.events.before("docker_ops_start(services=['nginx']", "wait_for_services")


def test_a_running_nginx_is_not_restarted(tmp_path):
    harness = _Harness(_config(tmp_path), tmp_path)
    harness.bench.admin_tools.compose_file_manager.compose_path.exists.return_value = True

    harness.orchestrator().start_bench()

    assert harness.events.only("docker_ops_start") == ["docker_ops_start(services=[],force_recreate=False)"]


def test_no_admin_tools_compose_means_no_nginx_heal_at_all(tmp_path):
    """The heal lives inside the admin-tools branch: a bench without them never checks nginx."""
    harness = _Harness(_config(tmp_path), tmp_path)
    harness.bench._is_service_running.return_value = False

    harness.orchestrator().start_bench()

    assert harness.bench._is_service_running.called is False


def test_supervisor_and_workers_are_reconfigured_only_when_asked(tmp_path):
    harness = _Harness(_config(tmp_path), tmp_path)

    harness.orchestrator().start_bench(
        reconfigure_supervisor=True, reconfigure_workers=True, include_default_workers=True
    )

    assert harness.bench.supervisor.setup_supervisor.call_args.kwargs == {"force": True}
    assert harness.bench.sync_workers_compose.call_args.kwargs == {
        "include_default_workers": True,
        "include_custom_workers": False,
    }
    harness.events.before("wait_for_services", "sync_workers_compose")


def test_nothing_is_reconfigured_by_default(tmp_path):
    harness = _Harness(_config(tmp_path), tmp_path)

    harness.orchestrator().start_bench()

    assert harness.bench.supervisor.setup_supervisor.called is False
    assert harness.bench.sync_workers_compose.called is False


def test_dev_packages_follow_the_environment_type(tmp_path):
    """`sync_dev_packages` does not mean install: it means make the bench match its environment."""
    dev = _Harness(_config(tmp_path / "dev"), tmp_path / "dev")
    dev.config.environment_type = FMBenchEnvType.dev
    dev.orchestrator().start_bench(sync_dev_packages=True)

    prod = _Harness(_config(tmp_path / "prod"), tmp_path / "prod")
    prod.orchestrator().start_bench(sync_dev_packages=True)

    assert dev.events.only("install_dev_packages", "remove_dev_packages") == ["install_dev_packages"]
    assert prod.events.only("install_dev_packages", "remove_dev_packages") == ["remove_dev_packages"]


def test_workers_are_only_started_when_their_compose_exists(tmp_path):
    harness = _Harness(_config(tmp_path), tmp_path)

    harness.orchestrator().start_bench()

    assert harness.bench.workers.docker_client.compose.up.called is False


# --------------------------------------------------------------------------- alias domains


def test_the_primary_domain_cannot_be_added_as_its_own_alias(tmp_path):
    """Skipped with a warning rather than refused: the rest of the request still applies."""
    harness = _Harness(_config(tmp_path), tmp_path)

    harness.orchestrator().update_alias_domains(add_domains=[SITE, "extra.example.com"])

    assert harness.config.alias_domains == ["extra.example.com"]
    assert f"Skipping '{SITE}'" in str(harness.output.warning.call_args_list)


def test_the_primary_domain_cannot_be_removed_at_all(tmp_path):
    """Refused, because there is no sane thing to do with the request."""
    harness = _Harness(_config(tmp_path), tmp_path)
    orchestrator = harness.orchestrator()

    with pytest.raises(ValueError, match="Cannot remove primary domain"):
        orchestrator.update_alias_domains(remove_domains=[SITE])

    assert harness.events.has("save_bench_config") is False


def test_a_no_op_request_saves_nothing_and_touches_no_container(tmp_path):
    """Adding an alias that is already there and removing one that is not: nothing changed, so
    nothing is written and nginx is not recreated."""
    harness = _Harness(_config(tmp_path), tmp_path)
    harness.config.alias_domains = ["www.example.com"]

    harness.orchestrator().update_alias_domains(add_domains=["www.example.com"], remove_domains=["gone.example.com"])

    assert harness.events.has("save_bench_config") is False
    assert harness.bench.generate_compose.called is False
    assert "No changes to apply" in str(harness.output.print.call_args_list)
    warned = str(harness.output.warning.call_args_list)
    assert "is already an alias" in warned
    assert "is not an alias" in warned


def test_an_applied_change_saves_the_config_before_regenerating_the_compose(tmp_path):
    """The compose file is generated FROM the config, so the config is updated first, and the alias
    list is stored sorted."""
    harness = _Harness(_config(tmp_path), tmp_path)
    harness.config.alias_domains = ["b.example.com"]

    harness.orchestrator().update_alias_domains(add_domains=["a.example.com"])

    assert harness.config.alias_domains == ["a.example.com", "b.example.com"]
    harness.events.before("save_bench_config", "generate_compose")
    assert harness.bench.docker_client.compose.up.call_args.kwargs == {
        "services": ["nginx"],
        "detach": True,
        "pull": "never",
        "force_recreate": True,
    }


def test_the_stale_nginx_vhost_is_deleted_so_it_gets_rebuilt(tmp_path):
    """`default.conf` is generated on container start; leaving the old one would serve the old
    domain list."""
    harness = _Harness(_config(tmp_path), tmp_path)
    conf = harness.root / "configs" / "nginx" / "conf" / "conf.d" / "default.conf"
    conf.parent.mkdir(parents=True)
    conf.write_text("server { server_name old; }")

    harness.orchestrator().update_alias_domains(add_domains=["a.example.com"])

    assert conf.exists() is False


def test_a_failure_after_the_save_rolls_back_memory_only(tmp_path):
    """Characterized, not endorsed: the rollback restores `alias_domains` on the config object but
    does not save again, so a compose failure AFTER a successful save leaves the new alias on disk
    and the old list in memory. Reported as a suspicion rather than fixed here."""
    harness = _Harness(_config(tmp_path), tmp_path)
    harness.config.alias_domains = ["a.example.com"]
    saved: list[list[str]] = []
    harness.bench.save_bench_config.side_effect = lambda *_a, **_k: saved.append(list(harness.config.alias_domains))
    harness.bench.generate_compose.side_effect = RuntimeError("template blew up")
    orchestrator = harness.orchestrator()

    with pytest.raises(Exception, match="Failed to update alias domains: template blew up"):
        orchestrator.update_alias_domains(add_domains=["b.example.com"])

    assert saved == [["a.example.com", "b.example.com"]]  # what was written
    assert harness.config.alias_domains == ["a.example.com"]  # what memory believes afterwards


def test_a_wildcard_alias_is_flagged_as_needing_dns01(tmp_path):
    harness = _Harness(_config(tmp_path), tmp_path)

    harness.orchestrator().update_alias_domains(add_domains=["*.example.com"])

    assert "requires DNS-01 challenge" in str(harness.output.warning.call_args_list)


def test_added_aliases_come_with_the_command_that_gets_them_a_certificate(tmp_path):
    """SSL is deliberately NOT automatic for aliases, so the operator is told how to ask."""
    harness = _Harness(_config(tmp_path), tmp_path)

    harness.orchestrator().update_alias_domains(add_domains=["a.example.com"])

    assert f"fm ssl add {SITE} a.example.com" in str(harness.output.print.call_args_list)


def test_removing_an_alias_needs_no_certificate_advice(tmp_path):
    harness = _Harness(_config(tmp_path), tmp_path)
    harness.config.alias_domains = ["a.example.com"]

    harness.orchestrator().update_alias_domains(remove_domains=["a.example.com"])

    assert harness.config.alias_domains == []
    assert "fm ssl add" not in str(harness.output.print.call_args_list)


def test_a_failure_puts_the_alias_list_back_the_way_it_was(tmp_path):
    """The in-memory config was already mutated before the write, so the rollback is what keeps a
    failed update from leaving the object claiming a domain the bench does not serve."""
    harness = _Harness(_config(tmp_path), tmp_path)
    harness.config.alias_domains = ["a.example.com"]
    harness.bench.save_bench_config.side_effect = RuntimeError("disk full")
    orchestrator = harness.orchestrator()

    with pytest.raises(Exception, match="Failed to update alias domains: disk full"):
        orchestrator.update_alias_domains(add_domains=["b.example.com"])

    assert harness.config.alias_domains == ["a.example.com"]


def test_a_full_service_restart_regenerates_the_compose_between_stop_and_up(tmp_path):
    """The heavier sibling of the lightweight alias update: everything goes down, the compose and
    the vhost are rebuilt, then everything comes back and is waited for."""
    harness = _Harness(_config(tmp_path), tmp_path)
    harness.bench.admin_tools.compose_file_manager.compose_path.exists.return_value = True
    harness.bench.workers.compose_file_manager.exists.return_value = True
    conf = harness.root / "configs" / "nginx" / "conf" / "conf.d" / "default.conf"
    conf.parent.mkdir(parents=True)
    conf.write_text("server { server_name old; }")

    harness.orchestrator()._restart_services_with_updated_config()

    assert conf.exists() is False
    harness.events.before("compose_stop", "generate_compose")
    harness.events.before("generate_compose", "compose_up")
    harness.events.before("compose_up", "admin_tools_enable(force_recreate_container=True)")
    harness.events.before("admin_tools_enable", "wait_for_services")
    harness.events.before("wait_for_services", "workers_up")
