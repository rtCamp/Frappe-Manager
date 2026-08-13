"""Attach writes nothing to the database: the contract, proven twice.

"Attach writes nothing" is true because of what fm *skips* -- `new-site`, phase 6, `bench
migrate`. That is a guarantee made of absences, and absences are not self-enforcing: nothing in
the pipeline says "attach must not write", so a step added to `create_bench` a year from now
would start writing to a customer's database with no test noticing. Hence two layers.

Layer 1 (structural, no Docker) drives `BenchOrchestrator` down the attach path with fakes and
asserts on every command the container runners were handed: no `bench new-site` in any form, no
`migrate`, no `install-app`, `_phase6_install_apps` and `_run_bench_migrate` never called, and
`[switch].migrate = false` persisted at the end. `new-site` is called out in *any* form on
purpose: `bootstrap_database` runs outside `new-site`'s `if setup:` block and opens with a
`DROP TABLE IF EXISTS` per core doctype, so `--no-setup-db` does not make it safe -- there is no
shape of that command that survives a schema which already holds a site.

`test_global_db_create_still_calls_new_site_and_phase_six` is the control: the same harness on a
`global-db` bench must record `new-site` and reach phase 6. Without it every assertion above
could pass because the harness records nothing at all.

Layer 2 (`integration`) is the empirical half: fingerprint the schema, run the attach, fingerprint
again. It skips when Docker or an fm bench is absent and never fails for the lack of one.
"""

import hashlib
import json
import shutil
import subprocess
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from frappe_manager.site_manager.bench_config import BenchConfig, DeployState
from frappe_manager.site_manager.modules import db_probe
from frappe_manager.site_manager.modules.bench_orchestrator import BenchOrchestrator
from frappe_manager.site_manager.modules.bench_site import BenchSiteManager

SITE = "app.example.com"
SCHEMA = "app_prod"
EXTERNAL_HOST = "mydb.abc.rds.amazonaws.com"
SITE_PASSWORD = "site-db-secret"  # noqa: S105
GLOBAL_DB_ROOT_PASSWORD = "global-db-root-secret"  # noqa: S105

_BASE_TOML = f"""
name = "{SITE}"
developer_mode = false
admin_tools = false
environment = "prod"

[[apps]]
name = "erpnext"
repo = "frappe/erpnext"
"""

_EXTERNAL_TABLE = f"""
[database."{SITE}"]
host = "{EXTERNAL_HOST}"
name = "{SCHEMA}"
user = "app_svc"
"""

# Written commands the attach path must never produce, in any form.
WRITING_FRAGMENTS = ("new-site", "migrate", "install-app", "reinstall", "restore", "drop-site")


def _config(tmp_path: Path, *, external: bool, runtime: str = "mount") -> BenchConfig:
    toml = _BASE_TOML + (_EXTERNAL_TABLE if external else "")
    if runtime == "image":
        toml = toml.replace('environment = "prod"', 'environment = "prod"\nruntime = "image"\nimage = "ghcr.io/fm/app"')
    path = tmp_path / "bench_config.toml"
    path.write_text(toml)
    config = BenchConfig.import_from_toml(path)
    if external:
        # What `fm create --attach-existing-site --db-password …` leaves on the config: create-time
        # only, never persisted.
        config.attach_existing_site = True
        config.db_password = SITE_PASSWORD
    if runtime == "image":
        config.deploy_state = DeployState(current_tag="ghcr.io/fm/app:v1")
    return config


def _attach_probe_result() -> db_probe.ProbeResult:
    """A clean probe of a schema that already holds a Frappe site. `decide_flow` is the real one."""
    return db_probe.ProbeResult(
        checks=(db_probe.ProbeCheck(db_probe.CHECK_CONNECT, db_probe.CheckStatus.ok, "connected"),),
        schema=db_probe.SchemaState(exists=True, table_count=412, is_frappe=True, installed_apps=("frappe", "erpnext")),
        server_enforces_tls=False,
        tls_in_force=False,
        user_exists=True,
    )


class _Harness:
    """Every channel a command can reach a container through, funnelled into one list.

    `site_manager` is a real `BenchSiteManager` rather than a mock, so a future edit that calls
    `create_bench_site` on the attach path genuinely builds the `new-site` argv and lands here.
    """

    def __init__(self, config: BenchConfig, tmp_path: Path):
        self.commands: list[str] = []
        self.forbidden: list[str] = []
        self.stubbed: list[str] = []
        self.saved_migrate: list[bool | None] = []
        self.config = config
        self.bench = self._bench(config, tmp_path)

    # ------------------------------------------------------------------ recorders
    def run(self, command: str, **_kw) -> None:
        self.commands.append(command)

    def exec_argv(self, argv: list[str], **_kw) -> None:
        self.commands.append(" ".join(argv))

    def compose_run(self, command: str = "", **_kw):
        self.commands.append(command)
        output = MagicMock()
        output.combined = []
        return output

    @property
    def transcript(self) -> str:
        return "\n".join(self.commands)

    def _bench(self, config: BenchConfig, tmp_path: Path):
        bench = MagicMock()
        bench.name = SITE
        bench.path = tmp_path / "bench"
        bench.bench_config = config

        site_manager = object.__new__(BenchSiteManager)  # bypass __init__: no Docker, no services
        site_manager.bench_name = SITE
        site_manager.bench_cli_cmd = ["bench"]
        site_manager.bench_config = config
        site_manager.output = MagicMock()
        services = site_manager.services = MagicMock()
        services.database_manager.database_server_info.password = GLOBAL_DB_ROOT_PASSWORD
        services.database_manager.database_server_info.host = "global-db"
        services.database_manager.database_server_info.port = 3306
        site_manager._container_run = self.run  # type: ignore[method-assign]  # noqa: SLF001
        site_manager._container_exec_argv = self.exec_argv  # type: ignore[method-assign]  # noqa: SLF001
        bench.site_manager = site_manager

        bench.app_manager.bench_cli_cmd = ["bench"]
        bench.app_manager._container_run = self.run  # noqa: SLF001
        # Phase 6's first act; recorded as the command it stands for so the transcript assertions
        # catch it even if the call-tracking assertion is ever loosened.
        bench.app_manager.install_apps_to_site.side_effect = lambda *a, **kw: self.commands.append(
            f"bench --site {SITE} install-app erpnext"
        )
        bench.docker_client.compose.run.side_effect = self.compose_run
        bench.docker_client.compose.exec.side_effect = self.compose_run
        bench.save_bench_config.side_effect = lambda *a, **kw: self.saved_migrate.append(
            config.switch.migrate if config.switch else None
        )
        return bench

    # ------------------------------------------------------------------ orchestrator
    def orchestrator(self) -> BenchOrchestrator:
        orchestrator = BenchOrchestrator(self.bench, output_handler=MagicMock())

        # Phases that only touch the host filesystem, the compose file or the containers. Stubbed
        # because they need Docker; none of them can reach the database.
        for phase in (
            "_phase1_prepare_structure",
            "_phase2_initialize_bench",
            "_phase2_seed_from_image",
            "_phase3_start_and_verify_bench",
            "_phase5_finalize",
        ):
            setattr(orchestrator, phase, self._stub(phase))

        # The two writing steps. Recorded rather than executed: being called at all is the failure.
        orchestrator._phase6_install_apps = self._forbidden("_phase6_install_apps")  # noqa: SLF001
        orchestrator._run_bench_migrate = self._forbidden("_run_bench_migrate")  # noqa: SLF001

        # `create_bench` funnels every exception here, which swallows it and cleans up. Re-raise so
        # a broken fake surfaces as a failure instead of a silently truncated pipeline.
        def _reraise(exception: Exception):
            raise exception

        orchestrator._handle_creation_failure = _reraise  # type: ignore[method-assign]  # noqa: SLF001
        return orchestrator

    def _stub(self, name: str):
        def stub(*_a, **_kw):
            self.stubbed.append(name)

        return stub

    def _forbidden(self, name: str):
        def forbidden(*_a, **_kw) -> bool:
            self.forbidden.append(name)
            return True  # phase 6's "apps installed" flag, so the pipeline runs to the end

        return forbidden


@pytest.fixture(autouse=True)
def _no_real_benches(monkeypatch, tmp_path):
    """`_report_attach_warnings` scans fm's bench directory; point it at an empty one."""
    monkeypatch.setattr("frappe_manager.CLI_BENCHES_DIRECTORY", tmp_path / "benches")


@pytest.fixture
def _probe_says_attach(monkeypatch):
    """Stage one against a live server is Docker's business; the decision it feeds is not.

    `decide_flow` stays real, so the pipeline reaches the attach branch the same way a create does.
    """
    calls: list[dict] = []

    def fake_probe(_runner, **kwargs) -> db_probe.ProbeResult:
        calls.append(kwargs)
        return _attach_probe_result()

    monkeypatch.setattr(db_probe, "probe_stage_one", fake_probe)
    return calls


def _fake_the_app_image(monkeypatch) -> None:
    """The image runtime's two registry calls. Everything else on that path is real.

    It has no phase 2 and pre-creates `sites/<site>`, so it is the path where a stray
    `new-site --force` would be most at home, which is why it is driven here as well.
    """
    monkeypatch.setattr("frappe_manager.site_manager.modules.transport.fetch_image", lambda *a, **kw: None)

    def fake_cp(_tag, _src, dest, _client):
        Path(dest).parent.mkdir(parents=True, exist_ok=True)
        Path(dest).write_text("frappe\nerpnext\n")

    monkeypatch.setattr("frappe_manager.utils.docker.host_run_cp", fake_cp)


# --------------------------------------------------------------------------- layer 1: structural


@pytest.mark.parametrize("runtime", ["mount", "image"])
def test_attach_never_reaches_a_writing_step(tmp_path, monkeypatch, _probe_says_attach, runtime):
    """The whole create pipeline, attach flow: nothing that writes to the database is reached."""
    if runtime == "image":
        _fake_the_app_image(monkeypatch)

    harness = _Harness(_config(tmp_path, external=True, runtime=runtime), tmp_path)
    orchestrator = harness.orchestrator()

    orchestrator.create_bench()

    # The flow really was attach, so the assertions below are about the path under test.
    assert orchestrator._external_flow is db_probe.Flow.attach  # noqa: SLF001
    assert _probe_says_attach[0]["attach"] is True
    # …and the attach path really ran: Frappe's own make_site_dirs, which is what replaces new-site.
    assert "make_site_dirs" in harness.transcript

    assert harness.forbidden == []  # neither _phase6_install_apps nor _run_bench_migrate
    for fragment in WRITING_FRAGMENTS:
        assert fragment not in harness.transcript, f"attach issued a writing command: {fragment}"
    assert harness.bench.app_manager.install_apps_to_site.called is False


@pytest.mark.parametrize("runtime", ["mount", "image"])
def test_attach_persists_switch_migrate_false(tmp_path, monkeypatch, _probe_says_attach, runtime):
    """Without this the promise expires at the end of the create.

    `[switch].migrate` defaults to True, so the next `fm deploy` or `fm switch` would migrate data
    that predates fm, against an app set the parity check only warns about.
    """
    if runtime == "image":
        _fake_the_app_image(monkeypatch)

    config = _config(tmp_path, external=True, runtime=runtime)
    assert config.switch is None  # nothing in the fixture pre-supplies the answer
    harness = _Harness(config, tmp_path)

    harness.orchestrator().create_bench()

    assert config.switch is not None
    assert config.switch.migrate is False
    assert harness.saved_migrate, "bench_config.toml was never written"
    assert harness.saved_migrate[-1] is False  # persisted, not just set in memory


def test_global_db_create_still_calls_new_site_and_phase_six(tmp_path):
    """Control for the two tests above: the same harness, a bench with no `[database]` entry.

    Everything the attach tests assert the absence of has to be present here, or those assertions
    prove only that the fakes record nothing.
    """
    harness = _Harness(_config(tmp_path, external=False), tmp_path)
    orchestrator = harness.orchestrator()

    orchestrator.create_bench()

    assert orchestrator._external_flow is None  # no probe, no gate: the create fm has always run  # noqa: SLF001
    assert "new-site" in harness.transcript
    assert harness.forbidden == ["_phase6_install_apps"]
    assert harness.config.switch is None  # migrate is left at its default for a bench fm owns


# --------------------------------------------------------------------------- layer 2: fingerprint

# `--skip-dump-date` is NOT optional and must not be "simplified" away: the dump footer carries a
# `-- Dump completed on <timestamp>` line, so without it every fingerprint differs and the check
# reports a write that never happened. That exact false positive was hit the first time this was
# run by hand. `--single-transaction` keeps the read consistent without locking, and
# `--no-tablespaces` avoids needing the PROCESS privilege.
DUMP_FLAGS = ("--single-transaction", "--no-tablespaces", "--skip-dump-date")
_SUBPROCESS_TIMEOUT = 120


def _run(argv: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(argv, capture_output=True, timeout=_SUBPROCESS_TIMEOUT, check=False)  # noqa: S603


def _docker_or_skip() -> None:
    if shutil.which("docker") is None:
        pytest.skip("docker is not installed")
    if _run(["docker", "info"]).returncode != 0:
        pytest.skip("the docker daemon is not reachable")


def _global_db_container_or_skip() -> str:
    result = _run(["docker", "ps", "--format", "{{.Names}}"])
    if result.returncode != 0:
        pytest.skip("could not list running containers")
    names = [name for name in result.stdout.decode().split() if "global-db" in name]
    if not names:
        pytest.skip("the fm global-db container is not running")
    return names[0]


def _local_site_or_skip() -> dict:
    """A site fm already runs on `global-db`.

    The contract under test is "this code path issues no writes", and the path does not care whose
    server it is talking to, so no external server is needed.
    """
    from frappe_manager import CLI_BENCHES_DIRECTORY

    if not CLI_BENCHES_DIRECTORY.is_dir():
        pytest.skip("no fm benches on this machine")
    for bench_dir in sorted(CLI_BENCHES_DIRECTORY.iterdir()):
        for config_path in (bench_dir / "workspace" / "frappe-bench" / "sites").glob("*/site_config.json"):
            try:
                site_config = json.loads(config_path.read_text())
            except (OSError, ValueError):
                continue
            external = site_config.get("db_host") and "global-db" not in str(site_config["db_host"])
            if external or not site_config.get("db_name") or not site_config.get("db_password"):
                continue
            return {
                "site": config_path.parent.name,
                "schema": site_config["db_name"],
                "user": site_config.get("db_user") or site_config["db_name"],
                "password": site_config["db_password"],
            }
    pytest.skip("no fm bench with a global-db site to fingerprint")
    raise AssertionError  # unreachable; pytest.skip raises


def _fingerprint(container: str, site: dict) -> str:
    dump = _run(
        [
            "docker",
            "exec",
            "-e",
            f"MYSQL_PWD={site['password']}",
            container,
            "mariadb-dump",
            *DUMP_FLAGS,
            "-h",
            "127.0.0.1",
            "-u",
            site["user"],
            site["schema"],
        ]
    )
    if dump.returncode != 0 or not dump.stdout:
        pytest.skip(f"could not dump {site['schema']}: {dump.stderr.decode()[:200]}")
    return hashlib.sha256(dump.stdout).hexdigest()


@pytest.mark.integration
def test_attach_leaves_the_schema_byte_identical():
    """Fingerprint, run the attach's database work, fingerprint again.

    The attach flow's *only* contact with the database is stage one of the probe: phase 4 returns
    before the staleness re-check, phase 6 never runs, and `new-site` is never built. So that probe
    is what gets sandwiched here -- if it ever grows a statement that is not read-only, the second
    fingerprint moves.
    """
    _docker_or_skip()
    container = _global_db_container_or_skip()
    site = _local_site_or_skip()

    # The determinism control, FIRST and with nothing in between: two dumps of an untouched schema.
    # Without it a non-deterministic dump is indistinguishable from a real write, and the failure
    # message would blame the code under test for the tool's behaviour.
    control_a = _fingerprint(container, site)
    control_b = _fingerprint(container, site)
    if control_a != control_b:
        pytest.skip("mariadb-dump is not deterministic on this server; the fingerprint cannot judge a write")

    def runner(command: str) -> str:
        result = _run(["docker", "exec", container, "/bin/sh", "-c", command])
        return (result.stdout + result.stderr).decode(errors="replace")

    result = db_probe.probe_stage_one(
        runner,
        host="127.0.0.1",
        port=3306,
        site_user=site["user"],
        site_password=site["password"],
        schema=site["schema"],
        bench_apps=("frappe",),
        attach=True,
    )
    connect = result.check(db_probe.CHECK_CONNECT)
    if connect is not None and connect.status is db_probe.CheckStatus.fail:
        pytest.skip(f"the probe could not reach {site['schema']}: {connect.detail}")

    decision = db_probe.decide_flow(
        result,
        attach=True,
        credentials=db_probe.CredentialInputs(site_password_given=True, admin_given=False, db_name=site["schema"]),
        schema=site["schema"],
        host="global-db",
    )
    if decision.flow is not db_probe.Flow.attach:
        pytest.skip(f"{site['schema']} is not attachable, so nothing was exercised: {decision.message}")

    assert _fingerprint(container, site) == control_a, (
        f"the attach path changed {site['schema']}: it is supposed to write nothing at all"
    )


def test_probe_runner_strips_compose_lifecycle_noise():
    """`compose run --rm` narrates on the same stream the probe parses.

    The stage-one runner uses `compose run --rm`, which prints " Container <name> Creating"
    and friends before the command's own output. `db_probe` reads the first line of a reply
    positionally, so an unfiltered lifecycle line is parsed AS the query result: a schema that
    exists gets reported absent and the create then picks the provisioning flow against a
    populated schema. Caught live against a real external server.
    """
    from frappe_manager.site_manager.modules.bench_orchestrator import _strip_compose_noise

    noisy = [
        " Container extdbb-frappe-run-9f2 Creating",
        " Container extdbb-frappe-run-9f2 Created",
        " Network extdbb_default Removing",
        "1\t0",
    ]
    assert _strip_compose_noise(noisy) == "1\t0"

    # a real reply is never mistaken for noise, including error text and multi-row output
    assert _strip_compose_noise(["ERROR 1141 (42000): no such grant"]) == "ERROR 1141 (42000): no such grant"
    assert _strip_compose_noise(["tabDocType", "tabSingles"]) == "tabDocType\ntabSingles"
    # a table named like a lifecycle word is data, not noise: three bare columns, not the shape
    assert _strip_compose_noise(["Container\tCreated\textra"]) == "Container\tCreated\textra"


def test_attach_writes_migrate_false_before_the_pipeline_can_fail():
    """`[switch].migrate = false` must land with the attach DECISION, not after the pipeline.

    It is a setting, not a record of completion. A create that dies in a later phase still
    leaves the bench directory and its `[database]` entry on disk, so writing the flag last
    produced exactly the bench it exists to prevent: attached to someone else's data with
    migrate still on, which `fm deploy` would then act on. Measured live: a phase-5 failure
    left the flag unwritten.
    """
    import inspect

    from frappe_manager.site_manager.modules import bench_orchestrator as bo

    gate = inspect.getsource(bo.BenchOrchestrator._external_database_gate)
    assert "_disable_migrate_for_attach" in gate, "the flag must be written by the gate"

    # and phase 6's skip must no longer be the thing that writes it
    skip = inspect.getsource(bo.BenchOrchestrator._skip_phase6_for_attach)
    assert "save_bench_config" not in skip, "the flag write must not sit after the pipeline"
