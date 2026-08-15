"""What `fm create` DECIDES when it waits, when it overwrites, and when it refuses.

Three lines in `bench_orchestrator` were executed by the existing phase tests with nothing
asserting what they chose, so a bug injected into each one left the whole suite green:

- the gap between two health-check attempts (`if i < max_retries - 1`), where `<=` buys the
  operator one more pointless wait AFTER the create has already given up;
- `force=True` on the image path's supervisor generation, where `force=False` silently keeps
  whatever supervisor config the bench's `config/` already holds;
- the phase-6 skip's announcement, which is the operator's only record that BOTH of the database
  writes attach refuses were skipped, and not just one of them.

Two neighbours of the same shape come with them: where the image path's host-side config sits
relative to the image fetch, and whether attach's phase-6 skip is reached on the image runtime at
all -- that path carries its own copy of the `_attaching` ternary.

`test_bench_orchestrator_phases.py` owns the phase order of the mount path and the flow decision
itself; neither is restated here. Docker is never reached: every container channel is a recorder,
and the phases these tests do not exercise are stubbed.
"""

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from frappe_manager.docker.subprocess_output import SubprocessOutput
from frappe_manager.site_manager.bench_config import BenchConfig, DeployState
from frappe_manager.site_manager.modules import db_probe
from frappe_manager.site_manager.modules.bench_orchestrator import BenchOrchestrator

SITE = "app.example.com"
IMAGE_TAG = "ghcr.io/fm/app:v1"

_BASE_TOML = f"""
name = "{SITE}"
developer_mode = false
admin_tools = false
environment = "prod"

[[apps]]
name = "erpnext"
repo = "frappe/erpnext"
"""


def _config(tmp_path: Path, *, runtime: str = "mount") -> BenchConfig:
    toml = _BASE_TOML
    if runtime == "image":
        toml = f'runtime = "image"\nimage = "ghcr.io/fm/app"\n{toml}'
    tmp_path.mkdir(parents=True, exist_ok=True)
    path = tmp_path / "bench_config.toml"
    path.write_text(toml)
    config = BenchConfig.import_from_toml(path)
    if runtime == "image":
        config.deploy_state = DeployState(current_tag=IMAGE_TAG)
    return config


class _Events(list):
    """An ordered log of what the create did, with the two order questions asked directly."""

    def at(self, prefix: str) -> int:
        for index, event in enumerate(self):
            if event.startswith(prefix):
                return index
        raise AssertionError(f"{prefix!r} never happened. Events: {list(self)}")

    def has(self, prefix: str) -> bool:
        return any(event.startswith(prefix) for event in self)

    def before(self, first: str, second: str) -> None:
        assert self.at(first) < self.at(second), f"{first!r} must precede {second!r}. Events: {list(self)}"


class _Harness:
    """A bench whose collaborators record into one ordered event list.

    `bench.path` is a real directory under tmp_path, so "the supervisor config was already on
    disk when the call came" is a fact the recorders can read rather than a claim.
    """

    def __init__(self, config: BenchConfig, tmp_path: Path):
        self.events = _Events()
        self.config = config
        self.root = tmp_path / "benches" / SITE
        self.root.mkdir(parents=True, exist_ok=True)
        self.remove_status = True
        self.output = MagicMock()
        self.bench = self._bench()

    @property
    def config_dir(self) -> Path:
        return self.root / "workspace" / "frappe-bench" / "config"

    def _record(self, label: str, *, result=None):
        def side_effect(*_args, **_kwargs):
            self.events.append(label)
            return result

        return side_effect

    def _bench(self) -> MagicMock:
        bench = MagicMock()
        bench.name = SITE
        bench.path = self.root
        bench.bench_config = self.config
        bench.exists = True

        bench.supervisor.is_supervisord_running.return_value = True
        bench.supervisor.setup_supervisor.side_effect = self._setup_supervisor
        bench.set_common_bench_config.side_effect = self._record("common_site_config")
        bench.workers.docker_client.compose.up.side_effect = self._record("workers_up")
        bench.info.side_effect = self._record("info")
        bench.remove_bench.side_effect = lambda **_kw: (self.events.append("remove_bench"), self.remove_status)[1]
        return bench

    def _setup_supervisor(self, bench_path, *, force: bool = False, **_kwargs) -> None:
        """Record what `force` DECIDED, not that a call happened.

        The real `setup_supervisor` (bench_supervisor.py) returns without writing anything when
        the bench's `config/` already holds `*.fm.supervisor.conf` files and `force` is false;
        `force=True` regenerates regardless. That early return is the whole behavioural weight of
        the argument, so the recorder honours it and logs the outcome.
        """
        config_dir = Path(bench_path) / "workspace" / "frappe-bench" / "config"
        inherited = config_dir.is_dir() and any(config_dir.glob("*.fm.supervisor.conf"))
        self.events.append(
            "supervisor_config_kept_as_found" if inherited and not force else "supervisor_config_written"
        )

    def leave_a_supervisor_config_behind(self) -> None:
        """A bench directory that already carries an fm supervisor config, which is the only state
        in which `force` changes the outcome."""
        self.config_dir.mkdir(parents=True, exist_ok=True)
        (self.config_dir / "frappe-web.fm.supervisor.conf").write_text("[program:frappe-web]\ncommand=stale\n")

    # ------------------------------------------------------------------ health check recorders
    def server_answers(self, status: str) -> None:
        """Every health-check attempt gets the same HTTP status back."""

        def side_effect(**_kwargs) -> SubprocessOutput:
            self.events.append("attempt")
            return SubprocessOutput(stdout=[status], stderr=[], combined=[status], exit_code=0)

        self.bench.docker_client.compose.exec.side_effect = side_effect

    def server_is_unreachable(self) -> None:
        def side_effect(**_kwargs) -> SubprocessOutput:
            self.events.append("attempt")
            raise RuntimeError("container not up")

        self.bench.docker_client.compose.exec.side_effect = side_effect

    def record_sleeps(self, monkeypatch) -> None:
        """The waits go into the same ordered list as the attempts, so the assertion is about the
        SHAPE of the wait -- one gap between consecutive attempts, none after the last."""
        monkeypatch.setattr(
            "frappe_manager.site_manager.modules.bench_orchestrator.time.sleep",
            lambda seconds: self.events.append(f"waited {seconds}s"),
        )

    # ------------------------------------------------------------------ orchestrator
    def orchestrator(self) -> BenchOrchestrator:
        """The orchestrator with every phase these tests do not exercise turned into a recorder."""
        orchestrator = BenchOrchestrator(self.bench, output_handler=self.output)
        stubs = {
            "_external_database_gate": None,
            "_phase3_start_and_verify_bench": None,
            "_phase4_create_site": None,
            "_phase5_finalize": None,
            "_phase6_install_apps": True,
            "_skip_phase6_for_attach": True,
        }
        for name, result in stubs.items():
            setattr(orchestrator, name, MagicMock(side_effect=self._record(name.lstrip("_"), result=result)))
        return orchestrator

    def gate_decides(self, orchestrator: BenchOrchestrator, flow: db_probe.Flow | None) -> None:
        """Stand in for the gate the way the real one ends: with `_external_flow` set. Which shape
        of schema produces which flow is `test_bench_orchestrator_phases.py`'s business."""

        def stub() -> None:
            self.events.append("external_database_gate")
            orchestrator._external_flow = flow

        orchestrator._external_database_gate = stub  # type: ignore[method-assign]


def _fake_image_registry(monkeypatch, harness: _Harness, apps: str = "frappe\nerpnext\n") -> None:
    """The image path's two registry calls, recorded into the same event list rather than made."""
    monkeypatch.setattr(
        "frappe_manager.site_manager.modules.transport.fetch_image",
        lambda *_a, **_k: harness.events.append("fetch_image"),
    )

    def fake_cp(_tag, _source, dest, _client) -> None:
        harness.events.append("host_run_cp")
        Path(dest).parent.mkdir(parents=True, exist_ok=True)
        Path(dest).write_text(apps)

    monkeypatch.setattr("frappe_manager.utils.docker.host_run_cp", fake_cp)


# ------------------------------------------------------- the wait between two health-check attempts


@pytest.mark.timeout(15)
@pytest.mark.parametrize("broken", ["bad status", "unreachable"])
def test_the_server_check_waits_between_attempts_and_not_after_the_last_one(tmp_path, monkeypatch, broken):
    """`if i < max_retries - 1` is what makes the two-second sleep a GAP between attempts rather
    than a tail on each one. Thirty attempts, twenty-nine gaps, and the failure is raised the
    moment the last attempt fails: an operator watching a create die does not sit through one more
    wait for a request that is never going to be made. A raising exec is a retry on exactly the
    same schedule as a wrong status code, so both shapes of failure are pinned here.
    """
    harness = _Harness(_config(tmp_path), tmp_path)
    harness.record_sleeps(monkeypatch)
    if broken == "unreachable":
        harness.server_is_unreachable()
    else:
        harness.server_answers("502")

    with pytest.raises(Exception, match="Bench server not responding"):
        harness.orchestrator().verify_bench_server_responding()

    assert list(harness.events) == ["attempt", "waited 2s"] * 29 + ["attempt"]


@pytest.mark.timeout(15)
def test_a_server_that_answers_on_the_first_attempt_is_never_waited_on(tmp_path, monkeypatch):
    """The healthy create pays nothing for the retry budget: the `return` sits above the sleep."""
    harness = _Harness(_config(tmp_path), tmp_path)
    harness.record_sleeps(monkeypatch)
    harness.server_answers("404")

    harness.orchestrator().verify_bench_server_responding()

    assert list(harness.events) == ["attempt"]


@pytest.mark.timeout(15)
def test_a_server_that_comes_up_late_is_waited_for_only_until_it_does(tmp_path, monkeypatch):
    """The gap stops with the last failing attempt, not with the loop: once a good status arrives
    the create moves on immediately."""
    harness = _Harness(_config(tmp_path), tmp_path)
    harness.record_sleeps(monkeypatch)
    statuses = iter(["502", "502", "200"])

    def side_effect(**_kwargs) -> SubprocessOutput:
        harness.events.append("attempt")
        status = next(statuses)
        return SubprocessOutput(stdout=[status], stderr=[], combined=[status], exit_code=0)

    harness.bench.docker_client.compose.exec.side_effect = side_effect

    harness.orchestrator().verify_bench_server_responding()

    assert list(harness.events) == ["attempt", "waited 2s", "attempt", "waited 2s", "attempt"]


# ------------------------------------------------------------- the image path's host-side config


def test_the_image_path_regenerates_the_supervisor_config_instead_of_inheriting_one(tmp_path, monkeypatch):
    """`force=True` is what makes this write unconditional. Without it the generation returns
    early the moment the bench's `config/` already holds an fm supervisor conf, and phase 3 would
    then boot the bench on a config written for some other shape of bench -- different workers,
    different queue consumption, a different gunicorn wrapper -- instead of the one this create
    was asked for. The config this bench starts under is always the one generated from THIS
    create's bench_config.
    """
    harness = _Harness(_config(tmp_path, runtime="image"), tmp_path)
    _fake_image_registry(monkeypatch, harness)
    harness.leave_a_supervisor_config_behind()
    orchestrator = harness.orchestrator()
    harness.gate_decides(orchestrator, None)

    orchestrator._create_image_bench()

    assert harness.events.has("supervisor_config_kept_as_found") is False
    assert harness.events.has("supervisor_config_written") is True


def test_the_image_paths_host_side_config_is_written_before_the_image_is_even_fetched(tmp_path, monkeypatch):
    """common_site_config.json and the supervisor configs are mode-agnostic host files: they need
    no image, so they are written first and the fetch -- the expensive, network-bound step that
    can fail -- comes after. Both are on disk before the gate opens a connection and long before
    phase 3 starts a container, which is what lets the generation go through a one-off container.
    """
    harness = _Harness(_config(tmp_path, runtime="image"), tmp_path)
    _fake_image_registry(monkeypatch, harness)
    orchestrator = harness.orchestrator()
    harness.gate_decides(orchestrator, None)

    orchestrator._create_image_bench()

    harness.events.before("common_site_config", "supervisor_config_written")
    harness.events.before("supervisor_config_written", "fetch_image")
    harness.events.before("fetch_image", "external_database_gate")
    harness.events.before("external_database_gate", "phase3_start_and_verify_bench")


# ------------------------------------------------------------------------ the phase-6 skip


def test_the_phase_six_skip_announces_both_of_the_writes_it_is_refusing(tmp_path):
    """Two separate database writes are being skipped -- `bench install-app` and `bench migrate` --
    and this head is the only record the operator gets of it. Naming one of them, or naming them
    as alternatives, misreports what the create did to a site whose data predates fm. Nothing is
    issued to the container either: the skip is a refusal, not a deferral.
    """
    harness = _Harness(_config(tmp_path), tmp_path)
    orchestrator = BenchOrchestrator(harness.bench, output_handler=harness.output)

    assert orchestrator._skip_phase6_for_attach() is True

    heads = [call.args[0] for call in harness.output.change_head.call_args_list]
    assert len(heads) == 1
    assert heads[0].startswith("Skipping")
    assert "app installation and bench migrate" in heads[0]
    assert harness.bench.app_manager.install_apps_to_site.called is False
    assert harness.bench.app_manager._container_run.called is False


def test_an_attaching_image_create_skips_phase_six_the_same_way_a_mount_create_does(tmp_path, monkeypatch):
    """The image path carries its own copy of the `_attaching` ternary, so attach's one promise --
    no writes to a database fm did not create -- has to be kept here too. Phase 5 and the workers
    still run: nothing failed, so the bench is complete and is described rather than offered for
    removal.
    """
    harness = _Harness(_config(tmp_path, runtime="image"), tmp_path)
    _fake_image_registry(monkeypatch, harness)
    orchestrator = harness.orchestrator()
    harness.gate_decides(orchestrator, db_probe.Flow.attach)

    orchestrator._create_image_bench()

    assert harness.events.has("phase6_install_apps") is False
    assert harness.events.has("remove_bench") is False
    harness.events.before("skip_phase6_for_attach", "phase5_finalize")
    harness.events.before("phase5_finalize", "workers_up")
    harness.events.before("workers_up", "info")


def test_an_image_create_that_made_its_own_site_installs_the_apps(tmp_path, monkeypatch):
    """The control: with no attach decision the same ternary runs phase 6 for real."""
    harness = _Harness(_config(tmp_path, runtime="image"), tmp_path)
    _fake_image_registry(monkeypatch, harness)
    orchestrator = harness.orchestrator()
    harness.gate_decides(orchestrator, db_probe.Flow.adopt_empty)

    orchestrator._create_image_bench()

    assert harness.events.has("skip_phase6_for_attach") is False
    harness.events.before("phase4_create_site", "phase6_install_apps")
