"""Characterization tests for ``BenchAppManager`` -- everything except ``_container_run``.

``_container_run`` (the run/exec transport matrix) is pinned by
``test_bench_app_run_matrix.py``; this file pins everything that DECIDES what gets
handed to it, plus the filesystem side effects and the error translation.

What is defended here, and why each one is load-bearing:

* **argv construction** (``install_app_to_env``, ``remove_app_from_env``,
  ``install_app_to_site``, ``build``, ``_install_python_deps_with_uv``,
  ``_install_node_deps``). A wrong flag still "works" all the way down to
  ``bench``, which then does something subtly different -- ``--overwrite`` that
  silently stopped being emitted would turn a re-install into a hard failure, and
  ``bench --site X install-app`` with the ``--site`` moved after the subcommand is
  simply not accepted. Every command string is asserted whole, not by substring.
* **which exception each failure becomes.** Each entry point pre-builds a
  specific ``BenchOperation*Failed`` and hands it to ``_container_run``, which is
  the only thing that turns a ``DockerException`` into it. Two paths deliberately
  pass *no* exception object (node deps, the non-uv pip path, the provision-image
  runner) so a docker failure escapes raw; that asymmetry is pinned.
* **the install/graft orchestration order**: clone -> apps.txt -> python deps ->
  node deps -> build, and the guards (``clone_only``, ``skip_clone``, empty list)
  that skip parts of it.
* **``graft_apps``' filesystem moves**: replace-in-place vs. append, stash vs.
  delete, apps.txt as the source of truth over a possibly stale config, and the
  ``finally`` that always removes the temp clone dir.
* **the version-requirement predicates** and the Python/Node environment setup
  decisions -- which of them means "skip installation" and which means "recreate
  the venv".
* **``_site_env``**, ``_filter_docker_warnings`` and ``_run_in_provision_image``'s
  ``docker run`` argv.

Suspicions are pinned as-is, never fixed; they are called out in the test names
and comments (search for "SUSPICION").

Everything below the unit is mocked at the seam: ``docker_client``, ``output``,
``bench_config``, and ``AppCloner``. No docker daemon, no network, no real
``~/frappe``; all filesystem work happens under ``tmp_path``.
"""

import os
import shlex
from unittest.mock import MagicMock

import pytest

from frappe_manager.docker import DOCKER_LINE_NOISE, DockerException
from frappe_manager.docker.subprocess_output import SubprocessOutput
from frappe_manager.site_manager.bench_config import AppConfig
from frappe_manager.site_manager.exceptions import (
    BenchOperationBenchAppInSiteFailed,
    BenchOperationBenchBuildFailed,
    BenchOperationBenchInstallAppInPythonEnvFailed,
    BenchOperationBenchRemoveAppFromPythonEnvFailed,
    BenchOperationException,
)
from frappe_manager.site_manager.modules import db_tls
from frappe_manager.site_manager.modules.app_cloner import AppClonerError
from frappe_manager.site_manager.modules.bench_app import BenchAppManager, merge_app_overrides

BENCH = "example.localhost"
BENCH_CLI = "/opt/user/.bin/bench"
VENV_PYTHON = "/workspace/frappe-bench/env/bin/python"
BENCH_MOUNT = "/workspace/frappe-bench"


def _manager(tmp_path, *, provision_image=None, external_db=False, apps_list=None, bench_name=BENCH):
    """A real ``BenchAppManager`` (real ``__init__``) with mocked collaborators.

    ``bench_path`` is a tmp dir, so ``frappe_bench_dir`` is real and writable.
    ``external_db=False`` keeps ``_site_env()`` empty, which is the common case;
    a bare MagicMock config would inject MYSQL_HOME into every assertion.
    """
    bench_config = MagicMock()
    bench_config.get_database_config.return_value = MagicMock() if external_db else None
    bench_config.apps_list = list(apps_list or [])
    bench_config.github_token = "gh-token"
    bench_config.use_uv = True
    bench_config.root_path = tmp_path
    bench_config.python_version = None
    bench_config.node_version = None
    return BenchAppManager(
        bench_name=bench_name,
        bench_path=tmp_path,
        docker_client=MagicMock(),
        bench_config=bench_config,
        output_handler=MagicMock(),
        provision_image=provision_image,
    )


def _output(combined=None, exit_code=0):
    lines = [] if combined is None else list(combined)
    return SubprocessOutput(stdout=list(lines), stderr=[], combined=list(lines), exit_code=exit_code)


def _docker_failure(message="boom"):
    return DockerException(["docker", "compose", "exec"], _output([message], exit_code=1))


def _commands(manager):
    """Every command string handed to the (mocked) ``_container_run``, in order."""
    return [call.args[0] for call in manager._container_run.call_args_list]


def _messages(mock):
    """The first positional argument of every call to an output mock."""
    return [call.args[0] for call in mock.call_args_list if call.args]


def _said(mock, needle):
    return any(needle in message for message in _messages(mock))


def _script(manager, rules, default=None):
    """Mock ``_container_run`` to answer per-command, matching ``rules`` in order."""

    def fake(command, *args, **kwargs):
        for needle, result in rules:
            if needle in command:
                if isinstance(result, Exception):
                    raise result
                return result
        return default

    manager._container_run = MagicMock(side_effect=fake)
    return manager._container_run


@pytest.fixture
def quiet_failure_rendering(monkeypatch):
    """Keep ``set_output``'s observable half, drop its rich rendering.

    The real ``set_output`` renders panels with the ``fm.*`` theme, which a bare
    ``rich.Console`` does not have; only the captured output matters here.
    """

    def set_output(self, output):
        self.output = output

    monkeypatch.setattr(BenchOperationException, "set_output", set_output)


def _fake_cloner(record, *, rename=None, error=None):
    """An ``AppCloner`` stand-in that materialises app dirs where it was pointed.

    ``rename`` reproduces the real cloner's post-clone correction of
    ``AppConfig.name`` (repo ``frappe-hello-world`` -> module
    ``frappe_hello_world``), which is a mutation of the caller's objects.
    """

    class Fake:
        def __init__(self, apps_dir, github_token=None, output_handler=None):
            self.apps_dir = apps_dir
            self.github_token = github_token
            self.output_handler = output_handler
            record.append(self)

        def clone_apps_parallel(self, apps, max_workers=5):
            if error is not None:
                raise error
            cloned = {}
            for app in apps:
                if rename and app.name in rename:
                    app.name = rename[app.name]
                target = self.apps_dir / app.name
                target.mkdir(parents=True, exist_ok=True)
                (target / "cloned").write_text(app.name)
                cloned[app.name] = target
            return cloned

    return Fake


def _bench_layout(tmp_path, apps_txt=None, existing_apps=()):
    """Create ``workspace/frappe-bench/{apps,sites}`` and optionally apps.txt."""
    frappe_bench = tmp_path / "workspace" / "frappe-bench"
    (frappe_bench / "sites").mkdir(parents=True)
    apps_dir = frappe_bench / "apps"
    apps_dir.mkdir()
    for name in existing_apps:
        (apps_dir / name).mkdir()
        (apps_dir / name / "uncommitted.py").write_text(f"# work in progress for {name}")
    if apps_txt is not None:
        (frappe_bench / "sites" / "apps.txt").write_text(apps_txt)
    return frappe_bench


# --------------------------------------------------------------------------------------
# __init__ / merge_app_overrides
# --------------------------------------------------------------------------------------


class TestConstruction:
    """The paths and the bench CLI prefix every command is built from."""

    def test_frappe_bench_dir_and_cli_prefix(self, tmp_path):
        manager = _manager(tmp_path)

        assert manager.frappe_bench_dir == tmp_path / "workspace" / "frappe-bench"
        assert manager.bench_cli_cmd == [BENCH_CLI]

    def test_provision_image_defaults_to_none(self, tmp_path):
        assert _manager(tmp_path).provision_image is None
        assert _manager(tmp_path, provision_image="fm/bake:latest").provision_image == "fm/bake:latest"


class TestMergeAppOverrides:
    """An override replaces its app IN PLACE (frappe stays first); unknowns append."""

    def test_override_replaces_in_place_and_order_is_preserved(self):
        current = [AppConfig.from_string(n) for n in ("frappe", "erpnext", "hrms")]
        override = AppConfig.from_string("erpnext:develop")

        merged = merge_app_overrides(current, [override])

        assert [a.name for a in merged] == ["frappe", "erpnext", "hrms"]
        assert merged[1] is override
        assert merged[1].ref == "develop"

    def test_unknown_modules_are_appended_in_override_order(self):
        current = [AppConfig.from_string("frappe")]

        merged = merge_app_overrides(current, [AppConfig.from_string("hrms"), AppConfig.from_string("insights")])

        assert [a.name for a in merged] == ["frappe", "hrms", "insights"]

    def test_last_override_for_a_name_wins(self):
        current = [AppConfig.from_string("frappe")]
        first = AppConfig.from_string("hrms:one")
        second = AppConfig.from_string("hrms:two")

        merged = merge_app_overrides(current, [first, second])

        assert [a.name for a in merged] == ["frappe", "hrms"]
        assert merged[1] is second

    def test_duplicate_names_in_current_collapse_to_the_last(self):
        # SUSPICION: dict-keyed merge silently de-duplicates the CURRENT list too,
        # so a duplicated apps.txt entry disappears even with no override for it.
        current = [AppConfig.from_string("frappe"), AppConfig.from_string("frappe:v15")]

        merged = merge_app_overrides(current, [])

        assert [a.name for a in merged] == ["frappe"]
        assert merged[0].ref == "v15"

    def test_no_overrides_returns_the_current_apps(self):
        current = [AppConfig.from_string("frappe"), AppConfig.from_string("erpnext")]

        assert [a.name for a in merge_app_overrides(current, [])] == ["frappe", "erpnext"]


# --------------------------------------------------------------------------------------
# install_app_to_env -- argv + error object
# --------------------------------------------------------------------------------------


class TestInstallAppToEnv:
    """``bench get-app`` argv: options come from the parameter dict, app goes LAST."""

    def test_default_argv_emits_overwrite_only(self, tmp_path):
        manager = _manager(tmp_path)
        manager._container_run = MagicMock()

        manager.install_app_to_env("erpnext")

        assert _commands(manager) == [f"{BENCH_CLI} get-app --overwrite erpnext"]

    def test_branch_precedes_overwrite_and_the_app_is_last(self, tmp_path):
        manager = _manager(tmp_path)
        manager._container_run = MagicMock()

        manager.install_app_to_env("erpnext", branch="version-15")

        assert _commands(manager) == [f"{BENCH_CLI} get-app --branch version-15 --overwrite erpnext"]

    def test_skip_assets_flag_is_a_bare_flag_after_overwrite(self, tmp_path):
        manager = _manager(tmp_path)
        manager._container_run = MagicMock()

        manager.install_app_to_env("erpnext", branch="develop", skip_assets=True)

        assert _commands(manager) == [f"{BENCH_CLI} get-app --branch develop --overwrite --skip-assets erpnext"]

    def test_overwrite_false_drops_the_flag_entirely(self, tmp_path):
        manager = _manager(tmp_path)
        manager._container_run = MagicMock()

        manager.install_app_to_env("erpnext", overwrite=False)

        assert _commands(manager) == [f"{BENCH_CLI} get-app erpnext"]

    def test_empty_branch_string_is_dropped_rather_than_passed_empty(self, tmp_path):
        manager = _manager(tmp_path)
        manager._container_run = MagicMock()

        manager.install_app_to_env("erpnext", branch="")

        assert _commands(manager) == [f"{BENCH_CLI} get-app --overwrite erpnext"]

    def test_a_full_url_is_appended_verbatim(self, tmp_path):
        manager = _manager(tmp_path)
        manager._container_run = MagicMock()

        manager.install_app_to_env("https://github.com/frappe/hrms.git", branch="develop")

        assert _commands(manager) == [
            f"{BENCH_CLI} get-app --branch develop --overwrite https://github.com/frappe/hrms.git",
        ]

    def test_app_and_branch_are_never_validated_or_quoted(self, tmp_path):
        # SUSPICION: neither the app spec nor the branch is validated or shell-quoted
        # before being joined into a string that `_container_run` hands to
        # `/bin/bash -c`. Metacharacters reach the shell. Pinned as-is.
        manager = _manager(tmp_path)
        manager._container_run = MagicMock()

        manager.install_app_to_env("erpnext; touch /tmp/pwned", branch="a b")

        assert _commands(manager) == [f"{BENCH_CLI} get-app --branch a b --overwrite erpnext; touch /tmp/pwned"]

    def test_runs_via_exec_with_the_python_env_failure_object(self, tmp_path):
        manager = _manager(tmp_path)
        manager._container_run = MagicMock()

        manager.install_app_to_env("erpnext")

        kwargs = manager._container_run.call_args.kwargs
        assert set(kwargs) == {"on_failure"}  # no use_run -> exec transport
        failure = kwargs["on_failure"]()
        assert isinstance(failure, BenchOperationBenchInstallAppInPythonEnvFailed)
        assert (failure.bench_name, failure.app_name) == (BENCH, "erpnext")

    def test_docker_failure_surfaces_as_the_python_env_exception(self, tmp_path, quiet_failure_rendering):
        manager = _manager(tmp_path)
        manager.docker_client.compose.exec.side_effect = _docker_failure()

        with pytest.raises(BenchOperationBenchInstallAppInPythonEnvFailed) as excinfo:
            manager.install_app_to_env("erpnext")

        assert excinfo.value.app_name == "erpnext"
        assert excinfo.value.output.combined == ["boom"]


class TestRemoveAppFromEnv:
    """``bench remove-app`` argv: underscores become dashes, app goes LAST."""

    def test_default_argv_emits_both_flags(self, tmp_path):
        manager = _manager(tmp_path)
        manager._container_run = MagicMock()

        manager.remove_app_from_env("erpnext")

        assert _commands(manager) == [f"{BENCH_CLI} remove-app --no-backup --force erpnext"]

    @pytest.mark.parametrize(
        ("no_backup", "force", "expected"),
        [
            (False, True, f"{BENCH_CLI} remove-app --force erpnext"),
            (True, False, f"{BENCH_CLI} remove-app --no-backup erpnext"),
            (False, False, f"{BENCH_CLI} remove-app erpnext"),
        ],
    )
    def test_each_flag_is_independently_droppable(self, tmp_path, no_backup, force, expected):
        manager = _manager(tmp_path)
        manager._container_run = MagicMock()

        manager.remove_app_from_env("erpnext", no_backup=no_backup, force=force)

        assert _commands(manager) == [expected]

    def test_carries_the_remove_from_env_failure_object(self, tmp_path):
        manager = _manager(tmp_path)
        manager._container_run = MagicMock()

        manager.remove_app_from_env("erpnext")

        failure = manager._container_run.call_args.kwargs["on_failure"]()
        assert isinstance(failure, BenchOperationBenchRemoveAppFromPythonEnvFailed)
        assert (failure.bench_name, failure.app_name) == (BENCH, "erpnext")

    def test_docker_failure_surfaces_as_the_remove_exception(self, tmp_path, quiet_failure_rendering):
        manager = _manager(tmp_path)
        manager.docker_client.compose.exec.side_effect = _docker_failure()

        with pytest.raises(BenchOperationBenchRemoveAppFromPythonEnvFailed) as excinfo:
            manager.remove_app_from_env("erpnext")

        assert excinfo.value.output.combined == ["boom"]


class TestInstallAppToSite:
    """``--site`` is a bench GLOBAL option: it must precede ``install-app``."""

    def test_site_option_precedes_the_subcommand(self, tmp_path):
        manager = _manager(tmp_path)
        manager._container_run = MagicMock()

        manager.install_app_to_site("erpnext", "other.localhost")

        assert _commands(manager) == [f"{BENCH_CLI} --site other.localhost install-app erpnext"]

    def test_site_defaults_to_the_bench_name(self, tmp_path):
        manager = _manager(tmp_path)
        manager._container_run = MagicMock()

        manager.install_app_to_site("erpnext")

        assert _commands(manager) == [f"{BENCH_CLI} --site {BENCH} install-app erpnext"]

    def test_carries_the_app_in_site_failure_object(self, tmp_path):
        manager = _manager(tmp_path)
        manager._container_run = MagicMock()

        manager.install_app_to_site("erpnext", "other.localhost")

        failure = manager._container_run.call_args.kwargs["on_failure"]()
        assert isinstance(failure, BenchOperationBenchAppInSiteFailed)
        # SUSPICION: the message names the BENCH, not the site it was installing into.
        assert failure.message.endswith(f"Failed to install app erpnext in site {BENCH}.")
        assert "other.localhost" not in failure.message

    def test_docker_failure_surfaces_as_the_app_in_site_exception(self, tmp_path, quiet_failure_rendering):
        manager = _manager(tmp_path)
        manager.docker_client.compose.exec.side_effect = _docker_failure()

        with pytest.raises(BenchOperationBenchAppInSiteFailed):
            manager.install_app_to_site("erpnext")


class TestInstallAppsToSite:
    """Config order is dependency order: apps install one at a time, in list order."""

    def test_installs_every_configured_app_in_order(self, tmp_path):
        apps = [AppConfig.from_string(n) for n in ("frappe", "erpnext", "hrms")]
        manager = _manager(tmp_path, apps_list=apps)
        manager.install_app_to_site = MagicMock()

        manager.install_apps_to_site("other.localhost")

        assert [c.args for c in manager.install_app_to_site.call_args_list] == [
            ("frappe", "other.localhost"),
            ("erpnext", "other.localhost"),
            ("hrms", "other.localhost"),
        ]

    def test_site_defaults_to_the_bench_name(self, tmp_path):
        manager = _manager(tmp_path, apps_list=[AppConfig.from_string("erpnext")])
        manager.install_app_to_site = MagicMock()

        manager.install_apps_to_site()

        assert manager.install_app_to_site.call_args.args == ("erpnext", BENCH)

    def test_frappe_is_not_excluded_from_the_site_installs(self, tmp_path):
        # SUSPICION: graft_apps deliberately skips frappe ("the framework is never
        # installed to site") but this loop does not; a config listing frappe will
        # run `bench --site X install-app frappe`.
        manager = _manager(tmp_path, apps_list=[AppConfig.from_string("frappe")])
        manager.install_app_to_site = MagicMock()

        manager.install_apps_to_site()

        assert manager.install_app_to_site.call_args.args == ("frappe", BENCH)

    def test_empty_config_installs_nothing(self, tmp_path):
        manager = _manager(tmp_path, apps_list=[])
        manager.install_app_to_site = MagicMock()

        manager.install_apps_to_site()

        manager.install_app_to_site.assert_not_called()

    def test_a_failing_app_aborts_the_remaining_installs(self, tmp_path):
        apps = [AppConfig.from_string(n) for n in ("erpnext", "hrms")]
        manager = _manager(tmp_path, apps_list=apps)
        manager.install_app_to_site = MagicMock(
            side_effect=BenchOperationBenchAppInSiteFailed(bench_name=BENCH, app_name="erpnext"),
        )

        with pytest.raises(BenchOperationBenchAppInSiteFailed):
            manager.install_apps_to_site()

        assert manager.install_app_to_site.call_count == 1


class TestBuild:
    """``bench build`` repeats ``--app`` per app; the failure object lists them."""

    def test_no_app_list_builds_everything(self, tmp_path):
        manager = _manager(tmp_path)
        manager._container_run = MagicMock()

        manager.build()

        assert _commands(manager) == [f"{BENCH_CLI} build"]

    def test_each_app_gets_its_own_app_flag(self, tmp_path):
        manager = _manager(tmp_path)
        manager._container_run = MagicMock()

        manager.build(["frappe", "erpnext"])

        assert _commands(manager) == [f"{BENCH_CLI} build --app frappe --app erpnext"]

    def test_empty_app_list_builds_nothing_specific_but_is_not_none(self, tmp_path):
        manager = _manager(tmp_path)
        manager._container_run = MagicMock()

        manager.build([])

        assert _commands(manager) == [f"{BENCH_CLI} build"]
        assert manager._container_run.call_args.kwargs["on_failure"]().apps == []

    def test_the_failure_is_built_lazily_and_use_run_is_forwarded(self, tmp_path):
        """`on_failure` is a factory, so nothing is constructed unless the build fails."""
        manager = _manager(tmp_path)
        manager._container_run = MagicMock()

        manager.build(["erpnext"], use_run=True)

        kwargs = manager._container_run.call_args.kwargs
        assert set(kwargs) == {"on_failure", "use_run"}
        assert kwargs["use_run"] is True
        built = kwargs["on_failure"]()
        assert isinstance(built, BenchOperationBenchBuildFailed)
        assert built.apps == ["erpnext"]

    def test_docker_failure_surfaces_as_the_build_exception(self, tmp_path, quiet_failure_rendering):
        manager = _manager(tmp_path)
        manager.docker_client.compose.exec.side_effect = _docker_failure()

        with pytest.raises(BenchOperationBenchBuildFailed) as excinfo:
            manager.build(["erpnext", "hrms"])

        # SUSPICION: the pluralisation appends " app" and then " apps", so the
        # multi-app message literally reads "Failed to build app apps ...".
        assert excinfo.value.message.endswith("Failed to build app apps erpnext hrms")


# --------------------------------------------------------------------------------------
# dependency installation argv
# --------------------------------------------------------------------------------------

UV_INSTALL = f"uv pip install --python {VENV_PYTHON} --no-cache-dir -e apps/"


class TestInstallPythonDeps:
    """UV probe first, then one editable install per app -- argv identical either way."""

    def test_probes_for_uv_then_installs_each_app_editable(self, tmp_path):
        manager = _manager(tmp_path)
        manager._container_run = MagicMock()
        apps = [AppConfig.from_string("frappe"), AppConfig.from_string("erpnext")]

        manager._install_python_deps_with_uv(apps)

        assert _commands(manager) == ["which uv", f"{UV_INSTALL}frappe", f"{UV_INSTALL}erpnext"]

    def test_the_probe_captures_output_and_never_raises_a_bench_exception(self, tmp_path):
        manager = _manager(tmp_path)
        manager._container_run = MagicMock()

        manager._install_python_deps_with_uv([AppConfig.from_string("frappe")])

        probe, install = manager._container_run.call_args_list
        assert probe.kwargs == {"capture_output": True, "use_run": False}
        assert install.kwargs == {"use_run": False}

    def test_use_run_is_forwarded_to_every_call(self, tmp_path):
        manager = _manager(tmp_path)
        manager._container_run = MagicMock()

        manager._install_python_deps_with_uv([AppConfig.from_string("frappe")], use_run=True)

        assert all(call.kwargs["use_run"] is True for call in manager._container_run.call_args_list)

    def test_no_apps_still_probes_for_uv(self, tmp_path):
        manager = _manager(tmp_path)
        manager._container_run = MagicMock()

        manager._install_python_deps_with_uv([])

        assert _commands(manager) == ["which uv"]

    def test_use_uv_false_skips_the_probe_and_omits_the_exception_argument(self, tmp_path):
        # SUSPICION: the "fallback to pip" path documented in the docstring builds
        # the very same `uv pip install` command; the only real difference is that
        # it passes no on_failure, so a docker failure escapes raw.
        manager = _manager(tmp_path)
        manager._container_run = MagicMock()

        manager._install_python_deps_with_uv([AppConfig.from_string("frappe")], use_uv=False)

        assert _commands(manager) == [f"{UV_INSTALL}frappe"]
        assert manager._container_run.call_args.kwargs == {"use_run": False}

    def test_a_failure_mid_way_restarts_the_whole_list_and_warns(self, tmp_path):
        # SUSPICION: the retry restarts from the FIRST app, so apps that already
        # installed are installed a second time.
        manager = _manager(tmp_path)
        calls = []

        def fake(command, **kwargs):
            calls.append(command)
            if command.endswith("apps/erpnext") and calls.count(command) == 1:
                raise _docker_failure()

        manager._container_run = MagicMock(side_effect=fake)
        apps = [AppConfig.from_string("frappe"), AppConfig.from_string("erpnext")]

        manager._install_python_deps_with_uv(apps)

        assert calls == [
            "which uv",
            f"{UV_INSTALL}frappe",
            f"{UV_INSTALL}erpnext",
            f"{UV_INSTALL}frappe",
            f"{UV_INSTALL}erpnext",
        ]
        assert _said(manager.output.warning, "succeeded on retry")

    def test_the_retry_passes_no_exception_object(self, tmp_path):
        manager = _manager(tmp_path)
        attempts = {"n": 0}

        def fake(command, **kwargs):
            if command.startswith("uv pip install"):
                attempts["n"] += 1
                if attempts["n"] == 1:
                    raise _docker_failure()

        manager._container_run = MagicMock(side_effect=fake)

        manager._install_python_deps_with_uv([AppConfig.from_string("frappe")])

        assert manager._container_run.call_args.kwargs == {"use_run": False}

    def test_a_failing_retry_propagates_the_docker_exception_unwrapped(self, tmp_path):
        manager = _manager(tmp_path)
        manager._container_run = MagicMock(side_effect=_docker_failure())

        with pytest.raises(DockerException):
            manager._install_python_deps_with_uv([AppConfig.from_string("frappe")])

        assert not _said(manager.output.warning, "succeeded on retry")

    def test_a_failing_probe_alone_sends_everything_down_the_retry_path(self, tmp_path):
        manager = _manager(tmp_path)

        def fake(command, **kwargs):
            if command == "which uv":
                raise _docker_failure("no uv")

        manager._container_run = MagicMock(side_effect=fake)

        manager._install_python_deps_with_uv([AppConfig.from_string("frappe")])

        assert _commands(manager) == ["which uv", f"{UV_INSTALL}frappe"]
        assert _said(manager.output.warning, "succeeded on retry")


class TestInstallNodeDeps:
    """``bench setup requirements --node`` with no failure object of its own."""

    def test_argv_and_absent_exception_object(self, tmp_path):
        manager = _manager(tmp_path)
        manager._container_run = MagicMock()

        manager._install_node_deps()

        assert _commands(manager) == [f"{BENCH_CLI} setup requirements --node"]
        # SUSPICION: no on_failure, so a node-deps failure surfaces as a
        # bare DockerException rather than a BenchOperation* error.
        assert manager._container_run.call_args.kwargs == {"use_run": False}

    def test_use_run_is_forwarded(self, tmp_path):
        manager = _manager(tmp_path)
        manager._container_run = MagicMock()

        manager._install_node_deps(use_run=True)

        assert manager._container_run.call_args.kwargs == {"use_run": True}

    def test_docker_failure_propagates_raw(self, tmp_path):
        manager = _manager(tmp_path)
        manager.docker_client.compose.exec.side_effect = _docker_failure()

        with pytest.raises(DockerException):
            manager._install_node_deps()


# --------------------------------------------------------------------------------------
# install_apps orchestration
# --------------------------------------------------------------------------------------


class TestInstallApps:
    """Clone -> apps.txt -> python deps -> node deps -> build, with three guards."""

    @staticmethod
    def _stub_stages(manager):
        recorder = MagicMock()
        manager._update_apps_txt = recorder.apps_txt
        manager._update_apps_list_with_corrected_names = recorder.corrected
        manager._install_python_deps_with_uv = recorder.python_deps
        manager._install_node_deps = recorder.node_deps
        manager.build = recorder.build
        return recorder

    def test_empty_list_short_circuits_before_any_work(self, tmp_path, monkeypatch):
        manager = _manager(tmp_path)
        recorder = self._stub_stages(manager)
        monkeypatch.setattr(
            "frappe_manager.site_manager.modules.bench_app.AppCloner",
            MagicMock(side_effect=AssertionError("must not clone")),
        )

        assert manager.install_apps([]) == []
        assert _said(manager.output.print, "No apps to install")
        recorder.python_deps.assert_not_called()
        recorder.build.assert_not_called()

    def test_full_run_order_and_the_cloner_wiring(self, tmp_path, monkeypatch):
        manager = _manager(tmp_path)
        recorder = self._stub_stages(manager)
        made = []
        monkeypatch.setattr(
            "frappe_manager.site_manager.modules.bench_app.AppCloner",
            _fake_cloner(made),
        )
        apps = [AppConfig.from_string("frappe"), AppConfig.from_string("erpnext")]

        returned = manager.install_apps(apps, github_token="tok", use_uv=False, use_run=True)

        assert [name for name, _, _ in recorder.mock_calls] == [
            "apps_txt",
            "corrected",
            "python_deps",
            "node_deps",
            "build",
        ]
        assert made[0].apps_dir == manager.frappe_bench_dir / "apps"
        assert made[0].github_token == "tok"
        assert made[0].output_handler is manager.output
        recorder.python_deps.assert_called_once_with(apps, use_uv=False, use_run=True)
        recorder.node_deps.assert_called_once_with(use_run=True)
        recorder.build.assert_called_once_with(use_run=True)
        assert returned is apps

    def test_clone_only_stops_after_registering_the_clones(self, tmp_path, monkeypatch):
        manager = _manager(tmp_path)
        recorder = self._stub_stages(manager)
        monkeypatch.setattr("frappe_manager.site_manager.modules.bench_app.AppCloner", _fake_cloner([]))
        apps = [AppConfig.from_string("erpnext")]

        assert manager.install_apps(apps, clone_only=True) is apps

        recorder.apps_txt.assert_called_once_with(apps)
        recorder.python_deps.assert_not_called()
        recorder.build.assert_not_called()

    def test_skip_clone_goes_straight_to_the_dependency_stages(self, tmp_path, monkeypatch):
        manager = _manager(tmp_path)
        recorder = self._stub_stages(manager)
        monkeypatch.setattr(
            "frappe_manager.site_manager.modules.bench_app.AppCloner",
            MagicMock(side_effect=AssertionError("must not clone")),
        )
        apps = [AppConfig.from_string("erpnext")]

        assert manager.install_apps(apps, skip_clone=True) is apps

        recorder.apps_txt.assert_not_called()
        recorder.corrected.assert_not_called()
        recorder.python_deps.assert_called_once_with(apps, use_uv=True, use_run=False)
        recorder.build.assert_called_once_with(use_run=False)

    def test_skip_clone_with_clone_only_does_absolutely_nothing(self, tmp_path, monkeypatch):
        # SUSPICION: clone_only is checked AFTER the skip_clone block, so the two
        # together silently return the input without installing or cloning.
        manager = _manager(tmp_path)
        recorder = self._stub_stages(manager)
        monkeypatch.setattr("frappe_manager.site_manager.modules.bench_app.AppCloner", _fake_cloner([]))
        apps = [AppConfig.from_string("erpnext")]

        assert manager.install_apps(apps, skip_clone=True, clone_only=True) is apps

        recorder.python_deps.assert_not_called()
        recorder.build.assert_not_called()

    def test_cloner_error_becomes_a_python_env_failure_for_multiple_apps(self, tmp_path, monkeypatch):
        manager = _manager(tmp_path)
        recorder = self._stub_stages(manager)
        cause = AppClonerError("auth failed")
        monkeypatch.setattr(
            "frappe_manager.site_manager.modules.bench_app.AppCloner",
            _fake_cloner([], error=cause),
        )

        with pytest.raises(BenchOperationBenchInstallAppInPythonEnvFailed) as excinfo:
            manager.install_apps([AppConfig.from_string("erpnext")])

        assert excinfo.value.app_name == "multiple apps"
        assert excinfo.value.bench_name == BENCH
        assert excinfo.value.__cause__ is cause
        recorder.python_deps.assert_not_called()

    def test_a_non_cloner_error_escapes_unwrapped(self, tmp_path, monkeypatch):
        # SUSPICION: only AppClonerError is translated; anything else (an OSError
        # from the clone, say) reaches the caller as-is.
        manager = _manager(tmp_path)
        self._stub_stages(manager)
        monkeypatch.setattr(
            "frappe_manager.site_manager.modules.bench_app.AppCloner",
            _fake_cloner([], error=OSError("disk full")),
        )

        with pytest.raises(OSError, match="disk full"):
            manager.install_apps([AppConfig.from_string("erpnext")])

    def test_the_returned_list_is_the_callers_own_mutated_objects(self, tmp_path, monkeypatch):
        # The cloner corrects names in place (repo frappe-hello-world -> module
        # frappe_hello_world) and install_apps hands back that same list object.
        manager = _manager(tmp_path)
        self._stub_stages(manager)
        monkeypatch.setattr(
            "frappe_manager.site_manager.modules.bench_app.AppCloner",
            _fake_cloner([], rename={"frappe-hello-world": "frappe_hello_world"}),
        )
        apps = [AppConfig.from_string("acme/frappe-hello-world:main")]

        returned = manager.install_apps(apps)

        assert returned is apps
        assert [a.name for a in returned] == ["frappe_hello_world"]


# --------------------------------------------------------------------------------------
# apps.txt / config bookkeeping
# --------------------------------------------------------------------------------------


class TestUpdateAppsTxt:
    """apps.txt is what frappe reads; entries are appended, never duplicated."""

    def test_creates_the_file_when_absent(self, tmp_path):
        _bench_layout(tmp_path)
        manager = _manager(tmp_path)

        manager._update_apps_txt([AppConfig.from_string("frappe"), AppConfig.from_string("erpnext")])

        assert (manager.frappe_bench_dir / "sites" / "apps.txt").read_text() == "frappe\nerpnext\n"

    def test_existing_order_is_kept_and_new_apps_appended(self, tmp_path):
        _bench_layout(tmp_path, apps_txt="frappe\nerpnext\n")
        manager = _manager(tmp_path)

        manager._update_apps_txt([AppConfig.from_string("hrms")])

        assert (manager.frappe_bench_dir / "sites" / "apps.txt").read_text() == "frappe\nerpnext\nhrms\n"

    def test_already_present_apps_are_not_duplicated(self, tmp_path):
        _bench_layout(tmp_path, apps_txt="frappe\nerpnext\n")
        manager = _manager(tmp_path)

        manager._update_apps_txt([AppConfig.from_string("erpnext"), AppConfig.from_string("hrms")])

        assert (manager.frappe_bench_dir / "sites" / "apps.txt").read_text() == "frappe\nerpnext\nhrms\n"

    def test_blank_lines_and_stray_whitespace_are_stripped(self, tmp_path):
        _bench_layout(tmp_path, apps_txt="\nfrappe \n\n  erpnext\n\n")
        manager = _manager(tmp_path)

        manager._update_apps_txt([])

        assert (manager.frappe_bench_dir / "sites" / "apps.txt").read_text() == "frappe\nerpnext\n"


class TestUpdateAppsListWithCorrectedNames:
    """Post-clone names are written back POSITIONALLY into the config list."""

    def test_names_are_replaced_by_index(self, tmp_path):
        original = [AppConfig.from_string("frappe"), AppConfig.from_string("acme/frappe-hello-world")]
        manager = _manager(tmp_path, apps_list=original)
        corrected = [AppConfig.from_string("frappe"), AppConfig.from_string("frappe_hello_world")]

        manager._update_apps_list_with_corrected_names(corrected)

        assert manager.bench_config.apps_list == corrected
        assert manager.bench_config.apps_list[1] is corrected[1]

    def test_extra_corrected_apps_beyond_the_config_are_dropped(self, tmp_path):
        # SUSPICION: the index guard silently discards corrections for apps the
        # config does not already have a slot for.
        manager = _manager(tmp_path, apps_list=[AppConfig.from_string("frappe")])
        corrected = [AppConfig.from_string("frappe"), AppConfig.from_string("erpnext")]

        manager._update_apps_list_with_corrected_names(corrected)

        assert [a.name for a in manager.bench_config.apps_list] == ["frappe"]

    def test_a_shorter_correction_list_leaves_the_tail_untouched(self, tmp_path):
        manager = _manager(
            tmp_path,
            apps_list=[AppConfig.from_string("frappe"), AppConfig.from_string("hrms")],
        )

        manager._update_apps_list_with_corrected_names([AppConfig.from_string("frappe:v15")])

        names = [a.name for a in manager.bench_config.apps_list]
        assert names == ["frappe", "hrms"]
        assert manager.bench_config.apps_list[0].ref == "v15"


class TestGetInstalledAppsList:
    """Only directories under ``apps/`` count as installed apps."""

    def test_returns_directories_and_ignores_files(self, tmp_path):
        frappe_bench = _bench_layout(tmp_path, existing_apps=("frappe", "erpnext"))
        (frappe_bench / "apps" / "apps.txt").write_text("noise")
        manager = _manager(tmp_path)

        found = manager.get_installed_apps_list()

        assert sorted(p.name for p in found) == ["erpnext", "frappe"]
        assert all(p.is_dir() for p in found)

    def test_empty_apps_dir_yields_an_empty_list(self, tmp_path):
        _bench_layout(tmp_path)

        assert _manager(tmp_path).get_installed_apps_list() == []

    def test_a_missing_apps_dir_raises_rather_than_returning_empty(self, tmp_path):
        # SUSPICION: callers get an OSError, not an empty list, when the bench has
        # not been provisioned yet.
        manager = _manager(tmp_path)

        with pytest.raises(FileNotFoundError):
            manager.get_installed_apps_list()


# --------------------------------------------------------------------------------------
# graft_apps
# --------------------------------------------------------------------------------------


@pytest.fixture
def graft_manager(tmp_path, monkeypatch):
    """A manager whose graft collaborators are stubbed but whose filesystem is real."""

    def make(apps_txt=None, existing_apps=(), apps_list=None, rename=None, error=None):
        _bench_layout(tmp_path, apps_txt=apps_txt, existing_apps=existing_apps)
        manager = _manager(tmp_path, apps_list=apps_list)
        record = []
        monkeypatch.setattr(
            "frappe_manager.site_manager.modules.app_cloner.AppCloner",
            _fake_cloner(record, rename=rename, error=error),
        )
        manager._install_python_deps_with_uv = MagicMock()
        manager._install_node_deps = MagicMock()
        manager.build = MagicMock()
        manager.cloners = record
        return manager

    return make


class TestGraftApps:
    """Overrides replace apps in place or get appended; apps.txt is the truth."""

    def test_replacing_a_known_app_adds_nothing_and_keeps_apps_txt(self, graft_manager):
        manager = graft_manager(apps_txt="frappe\nerpnext\n", existing_apps=("frappe", "erpnext"))

        added, stash_dir = manager.graft_apps([AppConfig.from_string("erpnext:develop")])

        assert added == []
        assert stash_dir is None
        apps_txt = manager.frappe_bench_dir / "sites" / "apps.txt"
        assert apps_txt.read_text() == "frappe\nerpnext\n"
        # the replaced copy is gone, the clone took its place
        assert (manager.frappe_bench_dir / "apps" / "erpnext" / "cloned").exists()
        assert not (manager.frappe_bench_dir / "apps" / "erpnext" / "uncommitted.py").exists()
        assert _said(manager.output.print, "Replacing app erpnext (frappe/erpnext:develop)")

    def test_a_new_app_is_reported_as_added_and_appended_to_apps_txt(self, graft_manager):
        manager = graft_manager(apps_txt="frappe\nerpnext\n", existing_apps=("frappe", "erpnext"))

        added, stash_dir = manager.graft_apps([AppConfig.from_string("hrms:main")])

        assert added == ["hrms"]
        assert stash_dir is None
        assert (manager.frappe_bench_dir / "sites" / "apps.txt").read_text() == "frappe\nerpnext\nhrms\n"
        assert _said(manager.output.print, "Adding app hrms (frappe/hrms:main)")

    def test_frappe_is_never_reported_as_installable_to_a_site(self, graft_manager):
        manager = graft_manager(apps_txt="erpnext\n", existing_apps=("erpnext",))

        added, _ = manager.graft_apps([AppConfig.from_string("frappe:version-15")])

        assert added == []
        assert (manager.frappe_bench_dir / "sites" / "apps.txt").read_text() == "erpnext\nfrappe\n"

    def test_default_ref_is_spelled_default_in_the_message(self, graft_manager):
        manager = graft_manager(apps_txt="frappe\n", existing_apps=("frappe",))

        manager.graft_apps([AppConfig.from_string("hrms")])

        assert _said(manager.output.print, "Adding app hrms (frappe/hrms:default)")

    def test_stash_true_moves_the_replaced_code_aside_intact(self, graft_manager):
        manager = graft_manager(apps_txt="frappe\nerpnext\n", existing_apps=("frappe", "erpnext"))

        _, stash_dir = manager.graft_apps([AppConfig.from_string("erpnext:develop")], stash=True)

        assert stash_dir is not None
        assert stash_dir.parent == manager.frappe_bench_dir
        assert stash_dir.name.startswith(".fm-apps-stash-")
        assert (stash_dir / "erpnext" / "uncommitted.py").read_text() == "# work in progress for erpnext"

    def test_one_stash_dir_is_shared_by_every_replaced_app(self, graft_manager):
        manager = graft_manager(apps_txt="frappe\nerpnext\n", existing_apps=("frappe", "erpnext"))

        _, stash_dir = manager.graft_apps(
            [AppConfig.from_string("frappe:v15"), AppConfig.from_string("erpnext:v15")],
            stash=True,
        )

        assert sorted(p.name for p in stash_dir.iterdir()) == ["erpnext", "frappe"]
        assert len([p for p in manager.frappe_bench_dir.iterdir() if p.name.startswith(".fm-apps-stash-")]) == 1

    def test_stash_false_deletes_the_replaced_code(self, graft_manager):
        manager = graft_manager(apps_txt="erpnext\n", existing_apps=("erpnext",))

        _, stash_dir = manager.graft_apps([AppConfig.from_string("erpnext:develop")], stash=False)

        assert stash_dir is None
        assert not any(p.name.startswith(".fm-apps-stash-") for p in manager.frappe_bench_dir.iterdir())

    def test_nothing_on_disk_to_replace_means_no_stash_dir_is_created(self, graft_manager):
        manager = graft_manager(apps_txt="frappe\nerpnext\n", existing_apps=("frappe",))

        added, stash_dir = manager.graft_apps([AppConfig.from_string("erpnext:develop")], stash=True)

        assert (added, stash_dir) == ([], None)
        assert (manager.frappe_bench_dir / "apps" / "erpnext" / "cloned").exists()

    def test_apps_txt_beats_a_stale_config_for_deciding_what_already_exists(self, graft_manager):
        # apps.txt says hrms is there; the (stale, image-created) config does not.
        manager = graft_manager(apps_txt="frappe\nhrms\n", existing_apps=("frappe", "hrms"), apps_list=[])

        added, _ = manager.graft_apps([AppConfig.from_string("hrms:main")])

        assert added == []
        assert (manager.frappe_bench_dir / "sites" / "apps.txt").read_text() == "frappe\nhrms\n"

    def test_a_missing_apps_txt_falls_back_to_the_config_list(self, graft_manager):
        manager = graft_manager(
            apps_txt=None,
            existing_apps=("frappe", "erpnext"),
            apps_list=[AppConfig.from_string("frappe"), AppConfig.from_string("erpnext")],
        )

        added, _ = manager.graft_apps([AppConfig.from_string("erpnext:develop")])

        assert added == []
        assert (manager.frappe_bench_dir / "sites" / "apps.txt").read_text() == "frappe\nerpnext\n"

    def test_an_empty_apps_txt_also_falls_back_to_the_config_list(self, graft_manager):
        manager = graft_manager(
            apps_txt="\n\n",
            existing_apps=("frappe",),
            apps_list=[AppConfig.from_string("frappe")],
        )

        added, _ = manager.graft_apps([AppConfig.from_string("hrms")])

        assert added == ["hrms"]
        assert (manager.frappe_bench_dir / "sites" / "apps.txt").read_text() == "frappe\nhrms\n"

    def test_the_config_keeps_the_override_objects_after_the_merge(self, graft_manager):
        manager = graft_manager(apps_txt="frappe\nerpnext\n", existing_apps=("frappe", "erpnext"))
        override = AppConfig.from_string("erpnext:develop")

        manager.graft_apps([override])

        merged = manager.bench_config.apps_list
        assert [a.name for a in merged] == ["frappe", "erpnext"]
        assert merged[1] is override

    def test_clones_land_in_a_temp_dir_that_is_always_removed(self, graft_manager):
        manager = graft_manager(apps_txt="frappe\n", existing_apps=("frappe",))

        manager.graft_apps([AppConfig.from_string("hrms")])

        assert manager.cloners[0].apps_dir == manager.frappe_bench_dir / "apps" / ".fm-graft-tmp"
        assert manager.cloners[0].github_token == "gh-token"
        assert not (manager.frappe_bench_dir / "apps" / ".fm-graft-tmp").exists()

    def test_a_clone_failure_still_removes_the_temp_dir_and_propagates(self, graft_manager):
        manager = graft_manager(
            apps_txt="frappe\n",
            existing_apps=("frappe",),
            error=AppClonerError("no such ref"),
        )

        # SUSPICION: unlike install_apps, graft_apps does NOT translate
        # AppClonerError into a BenchOperation* failure.
        with pytest.raises(AppClonerError):
            manager.graft_apps([AppConfig.from_string("hrms")])

        assert not (manager.frappe_bench_dir / "apps" / ".fm-graft-tmp").exists()
        assert (manager.frappe_bench_dir / "sites" / "apps.txt").read_text() == "frappe\n"
        manager.build.assert_not_called()

    def test_a_leftover_temp_dir_from_a_previous_run_is_wiped_first(self, graft_manager):
        manager = graft_manager(apps_txt="frappe\n", existing_apps=("frappe",))
        stale = manager.frappe_bench_dir / "apps" / ".fm-graft-tmp" / "hrms"
        stale.mkdir(parents=True)
        (stale / "junk").write_text("stale clone")

        manager.graft_apps([AppConfig.from_string("hrms")])

        assert not (manager.frappe_bench_dir / "apps" / "hrms" / "junk").exists()
        assert (manager.frappe_bench_dir / "apps" / "hrms" / "cloned").exists()

    def test_assets_rebuild_only_for_the_grafted_apps(self, graft_manager):
        manager = graft_manager(apps_txt="frappe\nerpnext\n", existing_apps=("frappe", "erpnext"))
        overrides = [AppConfig.from_string("erpnext:develop"), AppConfig.from_string("hrms")]

        manager.graft_apps(overrides, use_run=True)

        manager._install_python_deps_with_uv.assert_called_once_with(overrides, use_uv=True, use_run=True)
        manager._install_node_deps.assert_called_once_with(use_run=True)
        manager.build.assert_called_once_with(app_list=["erpnext", "hrms"], use_run=True)

    def test_the_post_clone_module_name_is_what_gets_grafted(self, graft_manager):
        manager = graft_manager(
            apps_txt="frappe\n",
            existing_apps=("frappe",),
            rename={"frappe-hello-world": "frappe_hello_world"},
        )

        added, _ = manager.graft_apps([AppConfig.from_string("acme/frappe-hello-world:main")])

        assert added == ["frappe_hello_world"]
        assert (manager.frappe_bench_dir / "apps" / "frappe_hello_world" / "cloned").exists()
        assert (manager.frappe_bench_dir / "sites" / "apps.txt").read_text() == "frappe\nfrappe_hello_world\n"


# --------------------------------------------------------------------------------------
# _site_env / _filter_docker_warnings
# --------------------------------------------------------------------------------------


class TestSiteEnv:
    """MYSQL_HOME points the mariadb CLI at this bench's own CA -- and only its own."""

    def test_a_global_db_bench_gets_no_option_file(self, tmp_path):
        manager = _manager(tmp_path, external_db=False)

        assert manager._site_env() == {}

    def test_an_external_db_bench_gets_its_own_sites_option_file(self, tmp_path):
        manager = _manager(tmp_path, external_db=True)

        assert manager._site_env() == {"MYSQL_HOME": db_tls.site_mysql_home(BENCH)}

    def test_the_lookup_is_keyed_on_this_benchs_name(self, tmp_path):
        manager = _manager(tmp_path, external_db=True, bench_name="other.localhost")

        env = manager._site_env()

        manager.bench_config.get_database_config.assert_called_once_with("other.localhost")
        assert env == {"MYSQL_HOME": db_tls.site_mysql_home("other.localhost")}


class TestFilterDockerWarnings:
    """Compose's ``time=... level=...`` chatter is stripped from captured output."""

    @pytest.mark.parametrize("level", ["warning", "info", "error"])
    def test_each_noisy_level_is_removed(self, tmp_path, level):
        manager = _manager(tmp_path)
        noisy = f'time="2024-05-01T10:00:00Z" level={level} msg="something"'

        result = manager._filter_docker_warnings(_output([noisy, "real output"]))

        assert result.combined == ["real output"]

    def test_leading_whitespace_does_not_hide_a_noisy_line(self, tmp_path):
        manager = _manager(tmp_path)
        noisy = '   time="2024-05-01T10:00:00Z" level=warning msg="x"'

        assert manager._filter_docker_warnings(_output([noisy, "keep"])).combined == ["keep"]

    def test_an_unknown_level_survives(self, tmp_path):
        manager = _manager(tmp_path)
        line = 'time="2024-05-01T10:00:00Z" level=debug msg="x"'

        assert manager._filter_docker_warnings(_output([line])).combined == [line]

    def test_the_pattern_is_anchored_so_embedded_text_survives(self, tmp_path):
        manager = _manager(tmp_path)
        line = 'bench said time="x" level=warning msg="y"'

        assert manager._filter_docker_warnings(_output([line])).combined == [line]

    def test_empty_combined_returns_the_same_object_untouched(self, tmp_path):
        manager = _manager(tmp_path)
        captured = _output([])
        captured.stdout = ["kept"]

        result = manager._filter_docker_warnings(captured)

        assert result is captured
        assert result.stdout == ["kept"]

    def test_filtering_mutates_combined_in_place_and_leaves_stdout_alone(self, tmp_path):
        # SUSPICION: only `combined` is filtered; the same noise stays in stdout,
        # so anything reading `.stdout` still sees compose warnings.
        manager = _manager(tmp_path)
        noisy = 'time="2024-05-01T10:00:00Z" level=warning msg="x"'
        captured = _output([noisy, "real"])

        result = manager._filter_docker_warnings(captured)

        assert result is captured
        assert captured.combined == ["real"]
        assert captured.stdout == [noisy, "real"]

    def test_exit_code_is_preserved(self, tmp_path):
        manager = _manager(tmp_path)

        assert manager._filter_docker_warnings(_output(["x"], exit_code=7)).exit_code == 7


# --------------------------------------------------------------------------------------
# _run_in_provision_image
# --------------------------------------------------------------------------------------


@pytest.fixture
def clean_passthrough_env(monkeypatch):
    """The five host variables the image runner forwards, all removed."""
    for key in ("DOCKER_HOST", "GITHUB_TOKEN", "GIT_TOKEN", "UV_LINK_MODE", "DOCKER_DEFAULT_PLATFORM"):
        monkeypatch.delenv(key, raising=False)


@pytest.fixture
def fixed_ids(monkeypatch):
    monkeypatch.setattr(os, "getuid", lambda: 4321)
    monkeypatch.setattr(os, "getgid", lambda: 8765)


def _run_kwargs(manager):
    return manager.docker_client.run.call_args.kwargs


def _bash_script(manager):
    """The ``-c`` script docker was told to run, unquoted back out of shlex.join."""
    parts = shlex.split(_run_kwargs(manager)["command"])
    assert parts[0] == "-c"
    return parts[1]


@pytest.mark.usefixtures("clean_passthrough_env", "fixed_ids")
class TestRunInProvisionImage:
    """``fm bake`` has no compose service: commands go through plain ``docker run``."""

    def test_docker_run_argv(self, tmp_path):
        manager = _manager(tmp_path, provision_image="fm/bake:latest")
        manager.docker_client.run.return_value = iter([])

        manager._run_in_provision_image("bench build")

        kwargs = _run_kwargs(manager)
        assert kwargs["image"] == "fm/bake:latest"
        assert kwargs["user"] == "root"
        assert kwargs["entrypoint"] == "/bin/bash"
        assert kwargs["volume"] == [f"{manager.frappe_bench_dir}:{BENCH_MOUNT}"]
        assert kwargs["workdir"] == BENCH_MOUNT
        assert kwargs["pull"] == "missing"
        assert kwargs["rm"] is True
        assert kwargs["stream"] is True

    def test_the_bind_mount_target_is_created_before_the_run(self, tmp_path):
        manager = _manager(tmp_path, provision_image="fm/bake:latest")
        manager.docker_client.run.return_value = iter([])
        assert not manager.frappe_bench_dir.exists()

        manager._run_in_provision_image("bench build")

        assert manager.frappe_bench_dir.is_dir()

    def test_the_script_remaps_ids_chowns_then_drops_to_frappe(self, tmp_path):
        manager = _manager(tmp_path, provision_image="fm/bake:latest")
        manager.docker_client.run.return_value = iter([])

        manager._run_in_provision_image("bench build")

        script = _bash_script(manager)
        assert script.startswith("usermod -u 4321 frappe 2>/dev/null; ")
        assert "groupmod -g 8765 frappe 2>/dev/null; " in script
        assert f"chown -R frappe:frappe {BENCH_MOUNT} 2>/dev/null; " in script
        assert script.index("chown -R") > script.index("groupmod -g")
        assert script.index("exec gosu frappe") > script.index("chown -R")

    def test_the_inner_command_sources_bashrc_cds_and_is_quoted_as_one_word(self, tmp_path):
        manager = _manager(tmp_path, provision_image="fm/bake:latest")
        manager.docker_client.run.return_value = iter([])

        manager._run_in_provision_image("bench build && echo done", workdir="/opt/elsewhere")

        inner = shlex.split(_bash_script(manager))[-1]
        assert inner == "source /etc/bash.bashrc; cd /opt/elsewhere && bench build && echo done"
        assert _run_kwargs(manager)["workdir"] == "/opt/elsewhere"

    def test_runtime_env_defaults(self, tmp_path):
        manager = _manager(tmp_path, provision_image="fm/bake:latest")
        manager.docker_client.run.return_value = iter([])

        manager._run_in_provision_image("bench build")

        env = _run_kwargs(manager)["env"]
        assert env["HOME"] == BENCH_MOUNT
        assert env["USER"] == "frappe"
        assert env["GROUP"] == "frappe"
        assert env["PATH"].startswith(f"{BENCH_MOUNT}/.uv/python-default/bin:{BENCH_MOUNT}/.fnm/aliases/default/bin:")
        assert env["FNM_DIR"] == f"{BENCH_MOUNT}/.fnm"
        assert env["FNM_COREPACK_ENABLED"] == "true"
        assert env["COREPACK_ENABLE_DOWNLOAD_PROMPT"] == "0"
        assert env["UV_PYTHON_INSTALL_DIR"] == f"{BENCH_MOUNT}/.uv/python"
        assert env["UV_CACHE_DIR"] == f"{BENCH_MOUNT}/.uv/cache"
        assert env["UV_PYTHON_DOWNLOADS"] == "automatic"
        assert env["UV_PYTHON_PREFERENCE"] == "only-managed"
        assert env["BENCH_USE_UV"] == "true"
        assert env["PYTHONUNBUFFERED"] == "1"
        assert env["LC_ALL"] == env["LANG"] == env["LANGUAGE"] == "en_US.UTF-8"

    def test_absent_host_variables_are_not_invented(self, tmp_path):
        manager = _manager(tmp_path, provision_image="fm/bake:latest")
        manager.docker_client.run.return_value = iter([])

        manager._run_in_provision_image("bench build")

        env = _run_kwargs(manager)["env"]
        for key in ("DOCKER_HOST", "GITHUB_TOKEN", "GIT_TOKEN", "UV_LINK_MODE", "DOCKER_DEFAULT_PLATFORM"):
            assert key not in env

    def test_present_host_variables_are_forwarded(self, tmp_path, monkeypatch):
        monkeypatch.setenv("DOCKER_HOST", "unix:///tmp/docker.sock")
        monkeypatch.setenv("GITHUB_TOKEN", "gh-secret")
        manager = _manager(tmp_path, provision_image="fm/bake:latest")
        manager.docker_client.run.return_value = iter([])

        manager._run_in_provision_image("bench build")

        env = _run_kwargs(manager)["env"]
        assert env["DOCKER_HOST"] == "unix:///tmp/docker.sock"
        assert env["GITHUB_TOKEN"] == "gh-secret"
        assert "GIT_TOKEN" not in env

    def test_caller_env_is_merged_last_and_can_override_a_default(self, tmp_path):
        manager = _manager(tmp_path, provision_image="fm/bake:latest")
        manager.docker_client.run.return_value = iter([])

        manager._run_in_provision_image("bench build", env={"HOME": "/elsewhere", "MYSQL_HOME": "/tls/site"})

        env = _run_kwargs(manager)["env"]
        assert env["HOME"] == "/elsewhere"
        assert env["MYSQL_HOME"] == "/tls/site"

    def test_capture_output_filters_and_returns_the_captured_output(self, tmp_path):
        manager = _manager(tmp_path, provision_image="fm/bake:latest")
        noisy = 'time="2024-05-01T10:00:00Z" level=warning msg="x"'
        manager.docker_client.run.return_value = _output([noisy, "real"])

        result = manager._run_in_provision_image("bench build", capture_output=True)

        assert _run_kwargs(manager)["stream"] is False
        assert result.combined == ["real"]
        manager.output.live_lines.assert_not_called()

    def test_streaming_pipes_to_live_lines_with_the_docker_noise_filters(self, tmp_path):
        manager = _manager(tmp_path, provision_image="fm/bake:latest")
        stream = iter([("stdout", b"line")])
        manager.docker_client.run.return_value = stream

        assert manager._run_in_provision_image("bench build") is None

        manager.output.live_lines.assert_called_once_with(stream, line_filters=DOCKER_LINE_NOISE)

    def test_a_docker_failure_here_is_never_translated(self, tmp_path):
        # SUSPICION: the compose paths in _container_run wrap DockerException into
        # the caller's BenchOperation* failure; this one has no try/except, so bake
        # callers see a raw DockerException instead.
        manager = _manager(tmp_path, provision_image="fm/bake:latest")
        manager.docker_client.run.side_effect = _docker_failure()

        with pytest.raises(DockerException):
            manager._run_in_provision_image("bench build")


# --------------------------------------------------------------------------------------
# version requirement predicates
# --------------------------------------------------------------------------------------


class TestPythonVersionSatisfiesRequirement:
    """The guard that decides "keep this venv" vs. "recreate it"."""

    @pytest.mark.parametrize(
        ("major", "minor", "expected"),
        [(3, 9, False), (3, 10, True), (3, 13, True), (3, 14, False), (4, 0, False)],
    )
    def test_a_bounded_range_is_inclusive_below_and_exclusive_above(self, tmp_path, major, minor, expected):
        manager = _manager(tmp_path)

        assert manager._python_version_satisfies_requirement(major, minor, ">=3.10,<3.14") is expected

    @pytest.mark.parametrize(
        ("major", "minor", "expected"),
        [(3, 10, False), (3, 11, True), (3, 13, True), (4, 11, False)],
    )
    def test_caret_pins_the_major_and_allows_newer_minors(self, tmp_path, major, minor, expected):
        manager = _manager(tmp_path)

        assert manager._python_version_satisfies_requirement(major, minor, "^3.11") is expected

    def test_a_bare_two_part_version_is_an_exact_match(self, tmp_path):
        manager = _manager(tmp_path)

        assert manager._python_version_satisfies_requirement(3, 11, "3.11") is True
        assert manager._python_version_satisfies_requirement(3, 12, "3.11") is False

    def test_surrounding_whitespace_is_ignored(self, tmp_path):
        manager = _manager(tmp_path)

        assert manager._python_version_satisfies_requirement(3, 11, "  3.11  ") is True

    @pytest.mark.parametrize("requirement", [">=3.10", "<3.14", "3.11.2", "==3.11", "", "any"])
    def test_unrecognised_forms_never_satisfy(self, tmp_path, requirement):
        # SUSPICION: an open-ended `>=3.10` (a perfectly normal requires-python)
        # matches no branch and returns False, which makes callers recreate the
        # venv even though the installed interpreter is fine.
        manager = _manager(tmp_path)

        assert manager._python_version_satisfies_requirement(3, 11, requirement) is False

    def test_a_range_with_an_unparseable_upper_bound_never_satisfies(self, tmp_path):
        # `<=3.14` has no `<DIGIT` for the regex to find.
        manager = _manager(tmp_path)

        assert manager._python_version_satisfies_requirement(3, 11, ">=3.10,<=3.14") is False


class TestNodeVersionSatisfiesRequirement:
    """Same guard for Node, major version only."""

    @pytest.mark.parametrize(("major", "expected"), [(17, False), (18, True), (24, True)])
    def test_lower_bound_only(self, tmp_path, major, expected):
        manager = _manager(tmp_path)

        assert manager._node_version_satisfies_requirement(major, ">=18") is expected

    @pytest.mark.parametrize(("major", "expected"), [(17, False), (18, True), (20, False)])
    def test_caret_pins_the_major_exactly(self, tmp_path, major, expected):
        manager = _manager(tmp_path)

        assert manager._node_version_satisfies_requirement(major, "^18") is expected

    def test_a_bare_major_is_an_exact_match(self, tmp_path):
        manager = _manager(tmp_path)

        assert manager._node_version_satisfies_requirement(18, "18") is True
        assert manager._node_version_satisfies_requirement(20, "18") is False

    def test_an_upper_bound_next_to_a_lower_bound_is_ignored(self, tmp_path):
        # SUSPICION: `>=18 <21` takes the `>=` branch and never looks at `<21`,
        # so Node 22 is reported as satisfying it.
        manager = _manager(tmp_path)

        assert manager._node_version_satisfies_requirement(22, ">=18 <21") is True

    @pytest.mark.parametrize("requirement", ["18.x", "18.0.0", "", "lts"])
    def test_forms_with_anything_after_the_major_never_satisfy(self, tmp_path, requirement):
        # SUSPICION: `18.x` and `18.0.0` are ordinary package.json engine values
        # and both return False, forcing a reinstall of Node 18 every time.
        manager = _manager(tmp_path)

        assert manager._node_version_satisfies_requirement(18, requirement) is False


# --------------------------------------------------------------------------------------
# get_current_runtime_versions
# --------------------------------------------------------------------------------------

PY_VERSION_CMD = f"{BENCH_MOUNT}/env/bin/python --version"


class TestGetCurrentRuntimeVersions:
    """What is installed now, or None -- never an exception."""

    def test_both_versions_are_parsed_from_the_two_probes(self, tmp_path):
        manager = _manager(tmp_path)
        _script(
            manager,
            [(PY_VERSION_CMD, _output(["Python 3.13.1"])), ("node --version", _output(["v22.4.0"]))],
        )

        assert manager.get_current_runtime_versions() == {"python": "3.13.1", "node": "22.4.0"}
        assert _commands(manager) == [PY_VERSION_CMD, "node --version"]

    def test_the_probes_capture_output_and_never_raise_a_bench_exception(self, tmp_path):
        manager = _manager(tmp_path)
        _script(manager, [], default=_output(["Python 3.13.1"]))

        manager.get_current_runtime_versions(use_run=True)

        for call in manager._container_run.call_args_list:
            assert call.kwargs == {"capture_output": True, "use_run": True}

    def test_a_nonzero_exit_leaves_that_version_unknown(self, tmp_path):
        manager = _manager(tmp_path)
        _script(
            manager,
            [
                (PY_VERSION_CMD, _output(["Python 3.13.1"], exit_code=1)),
                ("node --version", _output(["v22.4.0"])),
            ],
        )

        assert manager.get_current_runtime_versions() == {"python": None, "node": "22.4.0"}

    def test_no_output_at_all_leaves_both_unknown(self, tmp_path):
        manager = _manager(tmp_path)
        _script(manager, [], default=None)

        assert manager.get_current_runtime_versions() == {"python": None, "node": None}

    def test_a_two_component_python_version_is_not_recognised(self, tmp_path):
        # SUSPICION: the regex demands three components, so a hypothetical
        # `Python 3.13` banner reports None rather than 3.13.
        manager = _manager(tmp_path)
        _script(manager, [(PY_VERSION_CMD, _output(["Python 3.13"]))], default=_output([]))

        assert manager.get_current_runtime_versions()["python"] is None

    def test_a_probe_that_blows_up_is_swallowed_and_the_other_still_runs(self, tmp_path):
        manager = _manager(tmp_path)
        _script(
            manager,
            [(PY_VERSION_CMD, _docker_failure()), ("node --version", _output(["v22.4.0"]))],
        )

        assert manager.get_current_runtime_versions() == {"python": None, "node": "22.4.0"}

    def test_a_failing_node_probe_is_swallowed_independently(self, tmp_path):
        manager = _manager(tmp_path)
        _script(
            manager,
            [(PY_VERSION_CMD, _output(["Python 3.11.9"])), ("node --version", _docker_failure())],
        )

        assert manager.get_current_runtime_versions() == {"python": "3.11.9", "node": None}

    def test_the_version_is_taken_from_anywhere_in_the_joined_output(self, tmp_path):
        manager = _manager(tmp_path)
        _script(
            manager,
            [
                (PY_VERSION_CMD, _output(["warning: ignore me", "Python 3.11.9"])),
                ("node --version", _output(["some noise", "v20.11.1"])),
            ],
        )

        assert manager.get_current_runtime_versions() == {"python": "3.11.9", "node": "20.11.1"}


# --------------------------------------------------------------------------------------
# setup_python_and_node_environments
# --------------------------------------------------------------------------------------

SCAN_MARKER = "/workspace/frappe-bench/.uv/python/cpython-*"
SYMLINK_MARKER = "ln -sf python/"
VENV_MARKER = "uv venv env"


def _find(commands, needle):
    return [c for c in commands if needle in c]


class TestSetupEnvironmentsPython:
    """Which Python decision was taken: skip, reuse an installed one, or install."""

    def test_no_requirements_at_all_touches_no_container(self, tmp_path):
        manager = _manager(tmp_path)
        manager._container_run = MagicMock()

        assert manager.setup_python_and_node_environments() is False
        manager._container_run.assert_not_called()

    def test_a_satisfied_requirement_skips_installation_entirely(self, tmp_path):
        manager = _manager(tmp_path)
        manager.bench_config.python_version = ">=3.10,<3.14"
        _script(manager, [(PY_VERSION_CMD, _output(["Python 3.12.4"]))])

        assert manager.setup_python_and_node_environments() is False
        assert _commands(manager) == [PY_VERSION_CMD]
        assert _said(manager.output.print, "✓ Python 3.12 already satisfies >=3.10,<3.14 - skipping installation")

    def test_the_skip_message_names_frappes_own_requirement_when_readable(self, tmp_path):
        frappe_bench = _bench_layout(tmp_path)
        (frappe_bench / "apps" / "frappe").mkdir()
        (frappe_bench / "apps" / "frappe" / "pyproject.toml").write_text(
            '[project]\nrequires-python = ">=3.10,<3.14"\n',
        )
        manager = _manager(tmp_path)
        manager.bench_config.python_version = ">=3.10,<3.14"
        _script(manager, [(PY_VERSION_CMD, _output(["Python 3.12.4"]))])

        manager.setup_python_and_node_environments()

        assert _said(manager.output.print, "(Frappe requires: >=3.10,<3.14)")

    def test_frappes_requirement_is_read_under_the_bench_dir_not_the_config_file(self, tmp_path):
        """In production ``bench_config.root_path`` is the bench_config.toml FILE, not the bench
        directory (``create.py`` and ``bench_service.py`` both pass the file, and
        ``import_from_toml`` sets it to the path it read). Deriving the frappe app path from it
        looks under ``<bench>/bench_config.toml/workspace/...``, which can never exist, so the
        annotation silently disappears. The lookup must go through ``frappe_bench_dir``."""
        frappe_bench = _bench_layout(tmp_path)
        (frappe_bench / "apps" / "frappe").mkdir()
        (frappe_bench / "apps" / "frappe" / "pyproject.toml").write_text(
            '[project]\nrequires-python = ">=3.10,<3.14"\n',
        )
        manager = _manager(tmp_path)
        manager.bench_config.root_path = tmp_path / "bench_config.toml"
        manager.bench_config.python_version = ">=3.10,<3.14"
        _script(manager, [(PY_VERSION_CMD, _output(["Python 3.12.4"]))])

        manager.setup_python_and_node_environments()

        assert _said(manager.output.print, "(Frappe requires: >=3.10,<3.14)")

    def test_an_unsatisfied_requirement_reuses_the_newest_installed_interpreter(self, tmp_path):
        manager = _manager(tmp_path)
        manager.bench_config.python_version = "^3.13"
        _script(
            manager,
            [
                (PY_VERSION_CMD, _output(["Python 3.11.0"])),
                (
                    SCAN_MARKER,
                    _output(
                        [
                            "cpython-3.13.1-linux-aarch64-gnu",
                            "cpython-3.14.0-linux-aarch64-gnu",
                            "cpython-3.20.0-linux-aarch64-gnu download available",
                            "/usr/bin/python3 cpython-3.99.0",
                        ],
                    ),
                ),
            ],
        )

        assert manager.setup_python_and_node_environments() is False

        commands = _commands(manager)
        assert _find(commands, "uv python install") == []
        assert "ln -sf python/cpython-3.14.0-linux-aarch64-gnu python-default" in _find(commands, SYMLINK_MARKER)[0]
        assert _find(commands, VENV_MARKER) == []
        assert _said(manager.output.print, "Found Python 3.14.0 satisfying ^3.13")
        assert _said(manager.output.print, "Python 3.11 does not satisfy ^3.13 - will recreate venv")
        assert _said(manager.output.print, "Skipping venv recreation")

    def test_no_usable_interpreter_installs_one_and_relinks_the_detected_dir(self, tmp_path):
        manager = _manager(tmp_path)
        manager.bench_config.python_version = "3.11"
        _script(
            manager,
            [
                (PY_VERSION_CMD, _output([], exit_code=1)),
                (SCAN_MARKER, _output([])),
                ("ls -1", _output(["cpython-3.11.9-linux-aarch64-gnu"])),
            ],
        )

        assert manager.setup_python_and_node_environments() is False

        commands = _commands(manager)
        assert _find(commands, "uv python install") == ["uv python install cpython-3.11"]
        assert _find(commands, "ls -1") == [
            "ls -1 /workspace/frappe-bench/.uv/python/ | grep '^cpython-3.11' | sort -V | tail -1",
        ]
        assert "ln -sf python/cpython-3.11.9-linux-aarch64-gnu python-default" in _find(commands, SYMLINK_MARKER)[0]
        assert _said(manager.output.print, "Installing Python 3.11 via uv..")
        assert _said(manager.output.print, "Installed Python 3.11 via uv")

    def test_an_undetectable_install_falls_back_to_the_bare_cpython_name(self, tmp_path):
        manager = _manager(tmp_path)
        manager.bench_config.python_version = "3.11"
        _script(
            manager,
            [
                (PY_VERSION_CMD, _output([], exit_code=1)),
                (SCAN_MARKER, _output([])),
                ("ls -1", _output(["/usr/bin/python3.11"])),
            ],
        )

        manager.setup_python_and_node_environments()

        assert "ln -sf python/cpython-3.11 python-default" in _find(_commands(manager), SYMLINK_MARKER)[0]

    def test_recreate_moves_the_old_env_aside_and_reports_a_recreated_venv(self, tmp_path):
        manager = _manager(tmp_path)
        manager.bench_config.python_version = "3.11"
        _script(
            manager,
            [
                (PY_VERSION_CMD, _output([], exit_code=1)),
                (SCAN_MARKER, _output([])),
                ("ls -1", _output(["cpython-3.11.9-linux-aarch64-gnu"])),
            ],
        )

        assert manager.setup_python_and_node_environments(recreate_python_env=True) is True

        venv_cmd = _find(_commands(manager), VENV_MARKER)[0]
        assert "mv env env.bak-$timestamp" in venv_cmd
        assert "uv venv env --python cpython-3.11.9-linux-aarch64-gnu --seed --link-mode=copy" in venv_cmd
        assert _said(manager.output.print, "Created virtual environment with Python cpython-3.11.9-linux-aarch64-gnu")

    def test_recreate_reports_the_scanned_version_when_one_was_reused(self, tmp_path):
        manager = _manager(tmp_path)
        manager.bench_config.python_version = "^3.13"
        _script(
            manager,
            [
                (PY_VERSION_CMD, _output(["Python 3.11.0"])),
                (SCAN_MARKER, _output(["cpython-3.13.2-linux-aarch64-gnu"])),
            ],
        )

        assert manager.setup_python_and_node_environments(recreate_python_env=True) is True
        assert _said(manager.output.print, "Created virtual environment with Python 3.13.2")

    def test_an_unparseable_requirement_skips_the_python_stage_silently(self, tmp_path):
        manager = _manager(tmp_path)
        manager.bench_config.python_version = "not-a-version"
        _script(manager, [(PY_VERSION_CMD, _output(["Python 3.12.4"]))])

        assert manager.setup_python_and_node_environments() is False
        assert _commands(manager) == [PY_VERSION_CMD]
        assert _find(_commands(manager), "uv python install") == []

    def test_a_failure_mid_setup_warns_and_keeps_the_default_python(self, tmp_path):
        manager = _manager(tmp_path)
        manager.bench_config.python_version = "3.11"
        _script(
            manager,
            [
                (PY_VERSION_CMD, _output([], exit_code=1)),
                (SCAN_MARKER, _docker_failure("scan exploded")),
            ],
        )

        assert manager.setup_python_and_node_environments() is False
        assert _said(manager.output.warning, "Failed to setup Python 3.11")
        assert _said(manager.output.warning, "Continuing with default Python version")

    def test_a_failing_version_probe_does_not_stop_the_install_decision(self, tmp_path):
        manager = _manager(tmp_path)
        manager.bench_config.python_version = "3.11"
        _script(
            manager,
            [
                (PY_VERSION_CMD, _docker_failure("no container")),
                (SCAN_MARKER, _output(["cpython-3.11.9-linux"])),
            ],
        )

        assert manager.setup_python_and_node_environments() is False
        assert _find(_commands(manager), SYMLINK_MARKER) != []


class TestSetupEnvironmentsNode:
    """Which Node decision was taken: skip, reuse, or fnm install."""

    def test_a_satisfied_requirement_skips_installation(self, tmp_path):
        manager = _manager(tmp_path)
        manager.bench_config.node_version = ">=18"
        _script(manager, [("node --version", _output(["v20.11.1"]))])

        assert manager.setup_python_and_node_environments() is False
        assert _commands(manager) == ["node --version"]
        assert _said(manager.output.print, "Node 20 already satisfies >=18 - skipping installation")

    def test_a_failing_node_probe_falls_through_to_installing(self, tmp_path):
        manager = _manager(tmp_path)
        manager.bench_config.node_version = "^18"
        _script(
            manager,
            [
                ("node --version", _docker_failure("no container")),
                ("fnm list", _output(["v18.20.2"])),
                ("fnm default", _output([])),
                ("yarn --version", _output(["1.22.22"])),
            ],
        )

        assert manager.setup_python_and_node_environments() is False
        assert _find(_commands(manager), "fnm list") == ["fnm list | grep 'v18' || true"]

    def test_the_skip_message_names_frappes_own_engine_range_when_readable(self, tmp_path):
        frappe_bench = _bench_layout(tmp_path)
        (frappe_bench / "apps" / "frappe").mkdir()
        (frappe_bench / "apps" / "frappe" / "package.json").write_text('{"engines": {"node": ">=18"}}')
        manager = _manager(tmp_path)
        manager.bench_config.node_version = ">=18"
        _script(manager, [("node --version", _output(["v20.11.1"]))])

        manager.setup_python_and_node_environments()

        assert _said(manager.output.print, "(Frappe requires: >=18)")

    def test_frappes_engine_range_is_read_under_the_bench_dir_not_the_config_file(self, tmp_path):
        """Same production shape as the Python side: ``root_path`` is the config FILE."""
        frappe_bench = _bench_layout(tmp_path)
        (frappe_bench / "apps" / "frappe").mkdir()
        (frappe_bench / "apps" / "frappe" / "package.json").write_text('{"engines": {"node": ">=18"}}')
        manager = _manager(tmp_path)
        manager.bench_config.root_path = tmp_path / "bench_config.toml"
        manager.bench_config.node_version = ">=18"
        _script(manager, [("node --version", _output(["v20.11.1"]))])

        manager.setup_python_and_node_environments()

        assert _said(manager.output.print, "(Frappe requires: >=18)")

    def test_an_already_present_version_is_not_reinstalled(self, tmp_path):
        manager = _manager(tmp_path)
        manager.bench_config.node_version = "^18"
        _script(
            manager,
            [
                ("node --version", _output(["v20.11.1"])),
                ("fnm list", _output(["* v18.20.2 default"])),
                ("fnm default", _output([])),
                ("yarn --version", _output(["1.22.22"])),
            ],
        )

        manager.setup_python_and_node_environments()

        commands = _commands(manager)
        assert commands == [
            "node --version",
            "fnm list | grep 'v18' || true",
            "fnm default 18",
            "yarn --version",
        ]
        assert _said(manager.output.print, "Node 18 already installed")
        assert _said(manager.output.print, "Set Node 18 as default")
        assert _said(manager.output.print, "Yarn is available for Node 18")

    def test_a_missing_version_is_installed_then_made_default(self, tmp_path):
        manager = _manager(tmp_path)
        manager.bench_config.node_version = "^18"
        _script(
            manager,
            [
                ("node --version", _output(["v20.11.1"])),
                ("fnm list", _output([])),
                ("fnm install", _output([])),
                ("fnm default", _output([])),
                ("yarn --version", _output(["1.22.22"])),
            ],
        )

        manager.setup_python_and_node_environments()

        assert _commands(manager) == [
            "node --version",
            "fnm list | grep 'v18' || true",
            "fnm install 18",
            "fnm default 18",
            "yarn --version",
        ]
        assert _said(manager.output.print, "Installing Node 18 via fnm..")
        assert _said(manager.output.print, "Installed Node 18 via fnm")

    def test_a_failed_install_warns_and_skips_the_rest_of_the_node_stage(self, tmp_path):
        manager = _manager(tmp_path)
        manager.bench_config.node_version = "^18"
        _script(
            manager,
            [
                ("node --version", _output(["v20.11.1"])),
                ("fnm list", _output([])),
                ("fnm install", _output([], exit_code=1)),
            ],
        )

        assert manager.setup_python_and_node_environments() is False
        assert _find(_commands(manager), "fnm default") == []
        assert _said(manager.output.warning, "Failed to setup Node 18: fnm install 18 failed with exit code 1")
        assert _said(manager.output.warning, "Continuing with default Node version")

    def test_a_failed_default_warns_but_still_checks_yarn(self, tmp_path):
        manager = _manager(tmp_path)
        manager.bench_config.node_version = "^18"
        _script(
            manager,
            [
                ("node --version", _output(["v20.11.1"])),
                ("fnm list", _output(["v18.20.2"])),
                ("fnm default", _output([], exit_code=1)),
                ("yarn --version", _output(["1.22.22"])),
            ],
        )

        manager.setup_python_and_node_environments()

        assert _said(manager.output.warning, "Could not set Node 18 as default, but continuing")
        assert _find(_commands(manager), "yarn --version") == ["yarn --version"]

    def test_a_missing_yarn_only_warns(self, tmp_path):
        manager = _manager(tmp_path)
        manager.bench_config.node_version = "^18"
        _script(
            manager,
            [
                ("node --version", _output(["v20.11.1"])),
                ("fnm list", _output(["v18.20.2"])),
                ("fnm default", _output([])),
                ("yarn --version", _output([], exit_code=127)),
            ],
        )

        assert manager.setup_python_and_node_environments() is False
        assert _said(manager.output.warning, "Yarn not available after Node 18 installation - corepack may have failed")

    def test_an_unparseable_engine_range_skips_the_node_stage(self, tmp_path):
        manager = _manager(tmp_path)
        manager.bench_config.node_version = "lts/*"
        _script(manager, [("node --version", _output(["v20.11.1"]))])

        assert manager.setup_python_and_node_environments() is False
        assert _commands(manager) == ["node --version"]

    def test_use_run_is_forwarded_to_every_probe(self, tmp_path):
        manager = _manager(tmp_path)
        manager.bench_config.node_version = ">=18"
        _script(manager, [("node --version", _output(["v20.11.1"]))])

        manager.setup_python_and_node_environments(use_run=True)

        assert all(call.kwargs["use_run"] is True for call in manager._container_run.call_args_list)

    def test_python_runs_before_node(self, tmp_path):
        manager = _manager(tmp_path)
        manager.bench_config.python_version = ">=3.10,<3.14"
        manager.bench_config.node_version = ">=18"
        _script(
            manager,
            [(PY_VERSION_CMD, _output(["Python 3.12.4"])), ("node --version", _output(["v20.11.1"]))],
        )

        manager.setup_python_and_node_environments()

        assert _commands(manager) == [PY_VERSION_CMD, "node --version"]
