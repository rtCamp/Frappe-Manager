"""Characterization: what AppCloner and BenchDevTools actually DECIDE.

Both modules were effectively untested (app_cloner 10%, bench_devtools 26% -- import only).
These tests pin TODAY's behaviour so the modules can be refactored/deduplicated safely. They are a
specification of the current contract, not a wish list: where the code does something questionable it
is pinned as-is and reported as a suspicion, never "fixed" here.

AppCloner contract defended:
- how a source is interpreted: `repo` that is already a full URL is used verbatim and is the ONLY
  attempt; a bare `owner/name` is expanded into an ordered auth fallback list (token -> https -> ssh)
  and each URL is tried in that order until one clone succeeds.
- what it refuses: `_clone_app()` refuses a subdirectory app (ValueError); `clone_apps_parallel()`
  refuses to swallow failures -- it aggregates every failure into one AppClonerError.
- an existing app directory short-circuits: no git operation, no module-name detection, no rename.
- the git operation itself: which kwargs reach `Repo.clone_from` for branch / commit / non-shallow
  refs, the prompt-suppressing env, and the extra `git checkout` a commit ref requires.
- monorepo grouping: apps sharing repo+ref are cloned once into a shared temp dir, each subdir is
  copied out, the app is renamed to its real Python module name, and the temp dir is removed.

BenchDevTools contract defended:
- the pip argv it builds for install/remove, and which exception each failure raises.
- the attach guard order: not-running is checked first, then a missing `code` binary stops the run.
- when a devcontainer label is regenerated (extensions or remoteUser differ) and when it is not.
- the debugger guard: only a workdir under `workspace` gets .vscode files, existing files are backed
  up, and a ruff install failure is downgraded to a warning.
"""

import json
import shlex
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
import typer
from git import GitCommandError

from frappe_manager.docker.docker_exceptions import DockerException
from frappe_manager.docker.subprocess_output import SubprocessOutput
from frappe_manager.output_manager import OutputHandler
from frappe_manager.site_manager import (
    VSCODE_LAUNCH_JSON,
    VSCODE_SETTINGS_JSON,
    VSCODE_TASKS_JSON,
)
from frappe_manager.site_manager.bench_config import AppConfig
from frappe_manager.site_manager.exceptions import (
    BenchAttachTocontainerFailed,
    BenchFailedToInstallDevPackages,
    BenchFailedToRemoveDevPackages,
    BenchNotRunning,
)
from frappe_manager.site_manager.modules import app_cloner as app_cloner_module
from frappe_manager.site_manager.modules import bench_devtools as devtools_module
from frappe_manager.site_manager.modules.app_cloner import AppCloner, AppClonerError
from frappe_manager.site_manager.modules.bench_devtools import BenchDevTools

COMMIT_SHA = "a" * 40  # 40 hex chars is what AppConfig.is_commit recognises


# --------------------------------------------------------------------------- helpers


def _out() -> MagicMock:
    return MagicMock(spec=OutputHandler)


def _cloner(tmp_path: Path, token: str | None = None, output: MagicMock | None = None) -> AppCloner:
    return AppCloner(tmp_path / "apps", github_token=token, output_handler=output)


def _app(name: str, repo: str, **kw) -> AppConfig:
    return AppConfig(name=name, repo=repo, **kw)


def _fake_clone_from(monkeypatch, *, fails: set[str] | None = None, populate=None) -> list[dict]:
    """Replace `Repo.clone_from` with a recorder that materialises the directory.

    `fails` holds repo URLs that must raise (a partially written directory is left behind, exactly
    like a real interrupted clone, so cleanup behaviour is observable).
    """
    fails = fails or set()
    calls: list[dict] = []

    def clone_from(url, path, **kwargs):
        calls.append({"url": url, "path": Path(path), "kwargs": kwargs})
        target = Path(path)
        target.mkdir(parents=True, exist_ok=True)
        if url in fails:
            (target / "partial").write_text("half a clone")
            raise GitCommandError(["git", "clone", url], 128)
        if populate:
            populate(target)
        return MagicMock(name=f"Repo({url})")

    monkeypatch.setattr(app_cloner_module, "Repo", SimpleNamespace(clone_from=clone_from))
    return calls


def _pyproject(path: Path, module_name: str) -> None:
    path.mkdir(parents=True, exist_ok=True)
    (path / "pyproject.toml").write_text(f'[project]\nname = "{module_name}"\n')


# --------------------------------------------------------------------------- AppCloner: init


def test_apps_dir_is_created_eagerly_including_parents(tmp_path):
    """Construction is a side effect: the apps directory exists afterwards even if nothing is cloned."""
    apps_dir = tmp_path / "benches" / "b1" / "workspace" / "frappe-bench" / "apps"
    assert not apps_dir.exists()

    cloner = AppCloner(apps_dir)

    assert apps_dir.is_dir()
    assert cloner.apps_dir == apps_dir


def test_apps_dir_accepts_a_string_and_normalises_to_path(tmp_path):
    cloner = AppCloner(str(tmp_path / "apps"))
    assert cloner.apps_dir == tmp_path / "apps"


# --------------------------------------------------------------------------- AppCloner: source interpretation


def test_bare_owner_name_expands_to_token_then_https_then_ssh(tmp_path):
    """Auth priority with a token: the token URL is tried FIRST, https and ssh remain as fallbacks."""
    cloner = _cloner(tmp_path, token="ghp_secret")

    methods = cloner._get_auth_methods(_app("erpnext", "frappe/erpnext"))

    assert methods == [
        ("GitHub Token", "https://ghp_secret@github.com/frappe/erpnext.git"),
        ("HTTPS", "https://github.com/frappe/erpnext.git"),
        ("SSH", "git@github.com:frappe/erpnext.git"),
    ]


def test_without_a_token_https_comes_first_and_no_token_url_is_built(tmp_path):
    cloner = _cloner(tmp_path)

    methods = cloner._get_auth_methods(_app("erpnext", "frappe/erpnext"))

    assert methods == [
        ("HTTPS", "https://github.com/frappe/erpnext.git"),
        ("SSH", "git@github.com:frappe/erpnext.git"),
    ]


def test_a_full_url_source_is_used_verbatim_and_gets_no_github_fallbacks(tmp_path, monkeypatch):
    """A non-GitHub URL must never be rewritten into github.com; it is the single attempt."""
    cloner = _cloner(tmp_path, token="ghp_secret")
    app = _app("custom", "https://git.example.com/team/custom.git")
    calls = _fake_clone_from(monkeypatch)

    cloner._clone_app(app)

    assert [c["url"] for c in calls] == ["https://git.example.com/team/custom.git"]


def test_a_validated_repo_url_is_tried_before_the_generic_fallbacks(tmp_path):
    """`repo_url` is what validation resolved; the cloner honours it first but keeps fallbacks."""
    cloner = _cloner(tmp_path)
    app = _app("erpnext", "frappe/erpnext", repo_url="git@github.com:frappe/erpnext.git")

    methods = cloner._get_auth_methods(app)

    assert methods[0] == ("Validated URL", "git@github.com:frappe/erpnext.git")
    assert ("SSH", "git@github.com:frappe/erpnext.git") not in methods  # not attempted twice
    assert methods[1] == ("HTTPS", "https://github.com/frappe/erpnext.git")


# --------------------------------------------------------------------------- AppCloner: refusals & guards


def test_clone_app_refuses_a_subdirectory_app(tmp_path, monkeypatch):
    """Standalone-only entry point: a monorepo app must go through _clone_monorepo_apps."""
    cloner = _cloner(tmp_path)
    calls = _fake_clone_from(monkeypatch)
    app = _app("payments", "frappe/monorepo", subdir_path="apps/payments")

    with pytest.raises(ValueError, match="_clone_app\\(\\) called with subdirectory app payments"):
        cloner._clone_app(app)

    assert calls == []  # refused before any git work


def test_an_existing_app_directory_is_returned_untouched_without_cloning(tmp_path, monkeypatch):
    """No git operation, no module-name detection, no rename: the directory wins as-is."""
    cloner = _cloner(tmp_path)
    existing = cloner.apps_dir / "erpnext"
    _pyproject(existing, "some_other_module")  # would force a rename if it were inspected
    calls = _fake_clone_from(monkeypatch)
    app = _app("erpnext", "frappe/erpnext")

    name, path = cloner._clone_app(app)

    assert (name, path) == ("erpnext", existing)
    assert calls == []
    assert app.name == "erpnext"  # NOT updated to the pyproject module name
    assert (existing / "pyproject.toml").exists()


def test_clone_apps_parallel_with_no_apps_does_nothing(tmp_path, monkeypatch):
    cloner = _cloner(tmp_path, output=_out())
    calls = _fake_clone_from(monkeypatch)

    assert cloner.clone_apps_parallel([]) == {}
    assert calls == []


def test_clone_monorepo_apps_with_no_apps_does_nothing(tmp_path, monkeypatch):
    cloner = _cloner(tmp_path)
    calls = _fake_clone_from(monkeypatch)

    assert cloner._clone_monorepo_apps([]) == {}
    assert calls == []


# --------------------------------------------------------------------------- AppCloner: fallback & error paths


def test_a_failed_auth_method_is_cleaned_up_and_the_next_one_is_tried(tmp_path, monkeypatch):
    """The half-written directory MUST be removed, otherwise the retry would hit the skip branch."""
    cloner = _cloner(tmp_path, token="ghp_secret")
    app = _app("erpnext", "frappe/erpnext")
    token_url = "https://ghp_secret@github.com/frappe/erpnext.git"
    calls = _fake_clone_from(monkeypatch, fails={token_url})

    name, path = cloner._clone_app(app)

    assert [c["url"] for c in calls] == [token_url, "https://github.com/frappe/erpnext.git"]
    assert (name, path) == ("erpnext", cloner.apps_dir / "erpnext")
    assert not (path / "partial").exists()  # the failed attempt's leftovers are gone


def test_when_every_auth_method_fails_the_error_names_the_app_repo_and_last_error(tmp_path, monkeypatch):
    cloner = _cloner(tmp_path)
    app = _app("erpnext", "frappe/erpnext")
    calls = _fake_clone_from(
        monkeypatch,
        fails={"https://github.com/frappe/erpnext.git", "git@github.com:frappe/erpnext.git"},
    )

    with pytest.raises(Exception) as excinfo:
        cloner._clone_app(app)

    assert len(calls) == 2  # every method was actually tried
    message = str(excinfo.value)
    assert message.startswith("Failed to clone erpnext from frappe/erpnext. Tried all authentication methods.")
    assert "Last error:" in message
    assert not (cloner.apps_dir / "erpnext").exists()


@pytest.mark.timeout(15)
def test_parallel_clone_aggregates_every_failure_into_one_error(tmp_path, monkeypatch):
    """Failures are collected, not raised on the first one, and the good app still gets cloned."""
    cloner = _cloner(tmp_path, output=_out())
    good = _app("erpnext", "frappe/erpnext")
    bad = _app("hrms", "frappe/hrms")
    _fake_clone_from(
        monkeypatch,
        fails={"https://github.com/frappe/hrms.git", "git@github.com:frappe/hrms.git"},
    )

    with pytest.raises(AppClonerError) as excinfo:
        cloner.clone_apps_parallel([good, bad], max_workers=2)

    message = str(excinfo.value)
    assert message.startswith("Failed to clone apps:\n")
    assert "  - hrms: Failed to clone hrms from frappe/hrms." in message
    assert "erpnext" not in message
    assert (cloner.apps_dir / "erpnext").is_dir()  # the successful clone is left in place


@pytest.mark.timeout(15)
def test_parallel_clone_reports_each_success_with_a_tick(tmp_path, monkeypatch):
    output = _out()
    cloner = _cloner(tmp_path, output=output)
    _fake_clone_from(monkeypatch)

    result = cloner.clone_apps_parallel([_app("erpnext", "frappe/erpnext"), _app("hrms", "frappe/hrms")])

    assert result == {
        "erpnext": cloner.apps_dir / "erpnext",
        "hrms": cloner.apps_dir / "hrms",
    }
    assert sorted(c.args[0] for c in output.print.call_args_list) == ["Cloned erpnext", "Cloned hrms"]
    assert {c.kwargs["emoji_code"] for c in output.print.call_args_list} == {":white_check_mark:"}


@pytest.mark.timeout(15)
def test_parallel_clone_works_without_an_output_handler(tmp_path, monkeypatch):
    """`self.output` is optional; every print is guarded."""
    cloner = AppCloner(tmp_path / "apps")
    _fake_clone_from(monkeypatch)

    assert cloner.clone_apps_parallel([_app("erpnext", "frappe/erpnext")]) == {
        "erpnext": cloner.apps_dir / "erpnext",
    }


@pytest.mark.timeout(15)
def test_renaming_to_the_python_module_name_is_reflected_in_the_result_and_the_config(tmp_path, monkeypatch):
    """A repo named with dashes installs under its Python module name; AppConfig is mutated too."""
    cloner = _cloner(tmp_path)
    app = _app("frappe-consent-management", "acme/frappe-consent-management")
    _fake_clone_from(monkeypatch, populate=lambda p: _pyproject(p, "frappe_consent_management"))

    result = cloner.clone_apps_parallel([app])

    assert result == {"frappe_consent_management": cloner.apps_dir / "frappe_consent_management"}
    assert app.name == "frappe_consent_management"
    assert not (cloner.apps_dir / "frappe-consent-management").exists()


def test_rename_is_abandoned_when_the_module_named_directory_already_exists(tmp_path, monkeypatch):
    """Collision: keep the repo name rather than clobbering the existing directory."""
    cloner = _cloner(tmp_path)
    squatter = cloner.apps_dir / "frappe_consent_management"
    squatter.mkdir(parents=True)
    (squatter / "keep-me").write_text("previous app")
    app = _app("frappe-consent-management", "acme/frappe-consent-management")
    _fake_clone_from(monkeypatch, populate=lambda p: _pyproject(p, "frappe_consent_management"))

    name, path = cloner._clone_app(app)

    assert (name, path) == ("frappe-consent-management", cloner.apps_dir / "frappe-consent-management")
    assert app.name == "frappe-consent-management"
    assert (squatter / "keep-me").read_text() == "previous app"  # untouched


# --------------------------------------------------------------------------- AppCloner: the git operation


def test_a_branch_ref_becomes_a_shallow_single_branch_clone(tmp_path, monkeypatch):
    cloner = _cloner(tmp_path)
    calls = _fake_clone_from(monkeypatch)
    app = _app("erpnext", "frappe/erpnext", ref="version-15")

    cloner._git_clone("https://github.com/frappe/erpnext.git", cloner.apps_dir / "erpnext", app)

    kwargs = calls[0]["kwargs"]
    assert kwargs["branch"] == "version-15"
    assert kwargs["depth"] == 1
    assert calls[0]["path"] == cloner.apps_dir / "erpnext"


def test_git_clone_always_disables_interactive_credential_prompts(tmp_path, monkeypatch):
    """A prompting clone would hang the whole bench create; these two env vars prevent it."""
    cloner = _cloner(tmp_path)
    calls = _fake_clone_from(monkeypatch)

    cloner._git_clone("https://github.com/frappe/erpnext.git", cloner.apps_dir / "erpnext", _app("e", "frappe/erpnext"))

    env = calls[0]["kwargs"]["env"]
    assert env["GIT_TERMINAL_PROMPT"] == "0"
    assert env["GIT_ASKPASS"] == "echo"
    assert "PATH" in env  # inherited from os.environ, not a bare dict


def test_a_non_shallow_refless_clone_passes_neither_branch_nor_depth(tmp_path, monkeypatch):
    """None-valued options are stripped rather than passed as `depth=None`."""
    cloner = _cloner(tmp_path)
    calls = _fake_clone_from(monkeypatch)
    app = _app("erpnext", "frappe/erpnext", shallow_clone=False)

    cloner._git_clone("https://github.com/frappe/erpnext.git", cloner.apps_dir / "erpnext", app)

    assert set(calls[0]["kwargs"]) == {"env"}


def test_a_commit_ref_clones_fully_then_checks_the_commit_out(tmp_path, monkeypatch):
    """A commit is not a branch and is not reachable in a depth-1 clone: both options are dropped."""
    cloner = _cloner(tmp_path)
    repos: list[MagicMock] = []

    def clone_from(url, path, **kwargs):
        Path(path).mkdir(parents=True, exist_ok=True)
        repo = MagicMock()
        repos.append(repo)
        return repo

    monkeypatch.setattr(app_cloner_module, "Repo", SimpleNamespace(clone_from=clone_from))
    app = _app("erpnext", "frappe/erpnext", ref=COMMIT_SHA)
    assert app.is_commit

    cloner._git_clone("https://github.com/frappe/erpnext.git", cloner.apps_dir / "erpnext", app)

    repos[0].git.checkout.assert_called_once_with(COMMIT_SHA)


def test_a_branch_clone_does_not_check_anything_out(tmp_path, monkeypatch):
    cloner = _cloner(tmp_path)
    repos: list[MagicMock] = []

    def clone_from(url, path, **kwargs):
        Path(path).mkdir(parents=True, exist_ok=True)
        repo = MagicMock()
        repos.append(repo)
        return repo

    monkeypatch.setattr(app_cloner_module, "Repo", SimpleNamespace(clone_from=clone_from))

    cloner._git_clone(
        "https://github.com/frappe/erpnext.git",
        cloner.apps_dir / "erpnext",
        _app("erpnext", "frappe/erpnext", ref="version-15"),
    )

    repos[0].git.checkout.assert_not_called()


# --------------------------------------------------------------------------- AppCloner: monorepo grouping


def _fake_monorepo_clone(monkeypatch, subdirs: dict[str, str | None], *, fails: set[str] | None = None) -> list[dict]:
    """Replace `_git_clone` with a recorder that materialises a monorepo tree.

    `subdirs` maps a path inside the repo to the Python module name its pyproject declares
    (None = no pyproject, so the directory name stands).
    """
    fails = fails or set()
    calls: list[dict] = []

    def git_clone(self, repo_url, clone_path, app):
        calls.append({"url": repo_url, "path": Path(clone_path), "existed": Path(clone_path).exists()})
        clone_path = Path(clone_path)
        clone_path.mkdir(parents=True, exist_ok=True)
        if repo_url in fails:
            (clone_path / "partial").write_text("half a clone")
            raise GitCommandError(["git", "clone", repo_url], 128)
        (clone_path / ".git").mkdir(exist_ok=True)
        for subdir, module in subdirs.items():
            target = clone_path / subdir
            target.mkdir(parents=True, exist_ok=True)
            (target / "README.md").write_text(subdir)
            if module:
                _pyproject(target, module)

    monkeypatch.setattr(AppCloner, "_git_clone", git_clone)
    return calls


@pytest.mark.timeout(15)
def test_apps_sharing_repo_and_ref_are_cloned_once(tmp_path, monkeypatch):
    """The grouping decision: one shared clone for the group, one extraction per app."""
    cloner = _cloner(tmp_path, output=_out())
    calls = _fake_monorepo_clone(monkeypatch, {"apps/one": None, "apps/two": None})
    apps = [
        _app("one", "acme/mono", ref="main", subdir_path="apps/one"),
        _app("two", "acme/mono", ref="main", subdir_path="apps/two"),
    ]

    result = cloner.clone_apps_parallel(apps)

    assert len(calls) == 1
    assert result == {"one": cloner.apps_dir / "one", "two": cloner.apps_dir / "two"}
    assert (cloner.apps_dir / "one" / "README.md").read_text() == "apps/one"


@pytest.mark.timeout(15)
def test_the_same_repo_at_two_refs_is_cloned_twice(tmp_path, monkeypatch):
    """Grouping is keyed on repo AND ref: different refs are different monorepos."""
    cloner = _cloner(tmp_path, output=_out())
    calls = _fake_monorepo_clone(monkeypatch, {"apps/one": None, "apps/two": None})
    apps = [
        _app("one", "acme/mono", ref="main", subdir_path="apps/one"),
        _app("two", "acme/mono", ref="develop", subdir_path="apps/two"),
    ]

    cloner.clone_apps_parallel(apps)

    assert len(calls) == 2


@pytest.mark.timeout(15)
def test_a_monorepo_failure_is_reported_once_per_app_in_the_group(tmp_path, monkeypatch):
    cloner = _cloner(tmp_path, output=_out())
    _fake_monorepo_clone(
        monkeypatch,
        {"apps/one": None},
        fails={"https://github.com/acme/mono.git", "git@github.com:acme/mono.git"},
    )
    apps = [
        _app("one", "acme/mono", ref="main", subdir_path="apps/one"),
        _app("two", "acme/mono", ref="main", subdir_path="apps/two"),
    ]

    with pytest.raises(AppClonerError) as excinfo:
        cloner.clone_apps_parallel(apps)

    message = str(excinfo.value)
    assert "  - one: Failed to clone monorepo acme/mono" in message
    assert "  - two: Failed to clone monorepo acme/mono" in message


def test_the_shared_monorepo_checkout_is_named_after_the_repo_and_removed_afterwards(tmp_path, monkeypatch):
    cloner = _cloner(tmp_path)
    calls = _fake_monorepo_clone(monkeypatch, {"apps/one": None})

    cloner._clone_monorepo_apps([_app("one", "acme/mono", subdir_path="apps/one")])

    assert calls[0]["path"] == cloner.apps_dir / ".tmp_monorepo_acme_mono"
    assert not (cloner.apps_dir / ".tmp_monorepo_acme_mono").exists()


def test_a_stale_shared_monorepo_checkout_is_wiped_before_cloning(tmp_path, monkeypatch):
    """A leftover from an earlier crashed run must not be mistaken for a fresh clone."""
    cloner = _cloner(tmp_path)
    stale = cloner.apps_dir / ".tmp_monorepo_acme_mono"
    stale.mkdir(parents=True)
    (stale / "junk").write_text("from a previous run")
    calls = _fake_monorepo_clone(monkeypatch, {"apps/one": None})

    cloner._clone_monorepo_apps([_app("one", "acme/mono", subdir_path="apps/one")])

    assert calls[0]["existed"] is False  # wiped before _git_clone ran


def test_monorepo_auth_fallback_cleans_up_and_retries(tmp_path, monkeypatch):
    cloner = _cloner(tmp_path, token="ghp_secret")
    token_url = "https://ghp_secret@github.com/acme/mono.git"
    calls = _fake_monorepo_clone(monkeypatch, {"apps/one": None}, fails={token_url})

    result = cloner._clone_monorepo_apps([_app("one", "acme/mono", subdir_path="apps/one")])

    assert [c["url"] for c in calls] == [token_url, "https://github.com/acme/mono.git"]
    assert calls[1]["existed"] is False  # the failed attempt was removed first
    assert result == {"one": cloner.apps_dir / "one"}


def test_a_monorepo_that_cannot_be_cloned_at_all_raises_naming_the_repo(tmp_path, monkeypatch):
    cloner = _cloner(tmp_path)
    _fake_monorepo_clone(
        monkeypatch,
        {"apps/one": None},
        fails={"https://github.com/acme/mono.git", "git@github.com:acme/mono.git"},
    )

    with pytest.raises(Exception, match="Failed to clone monorepo acme/mono"):
        cloner._clone_monorepo_apps([_app("one", "acme/mono", subdir_path="apps/one")])


def test_a_missing_subdirectory_raises_and_lists_the_available_directories(tmp_path, monkeypatch):
    """The error is a user-facing hint: visible directories only, dotfiles and files excluded."""
    cloner = _cloner(tmp_path)

    def git_clone(self, repo_url, clone_path, app):
        clone_path = Path(clone_path)
        (clone_path / ".git").mkdir(parents=True)
        (clone_path / "apps").mkdir()
        (clone_path / "docs").mkdir()
        (clone_path / "README.md").write_text("hi")

    monkeypatch.setattr(AppCloner, "_git_clone", git_clone)

    with pytest.raises(Exception) as excinfo:
        cloner._clone_monorepo_apps([_app("one", "acme/mono", subdir_path="apps/missing")])

    message = str(excinfo.value)
    assert "Subdirectory 'apps/missing' not found in monorepo." in message
    assert "docs" in message
    assert ".git" not in message
    assert "README.md" not in message


def test_a_failed_extraction_leaves_the_shared_monorepo_checkout_behind(tmp_path, monkeypatch):
    """SUSPICION pinned, not fixed: cleanup sits after the loop, so a raise leaks the temp clone."""
    cloner = _cloner(tmp_path)
    _fake_monorepo_clone(monkeypatch, {"apps/one": None})

    with pytest.raises(Exception, match="not found in monorepo"):
        cloner._clone_monorepo_apps([_app("nope", "acme/mono", subdir_path="apps/nope")])

    assert (cloner.apps_dir / ".tmp_monorepo_acme_mono").is_dir()


def test_an_already_extracted_app_is_kept_and_not_overwritten(tmp_path, monkeypatch):
    cloner = _cloner(tmp_path)
    existing = cloner.apps_dir / "one"
    existing.mkdir(parents=True)
    (existing / "local-change").write_text("mine")
    _fake_monorepo_clone(monkeypatch, {"apps/one": None})

    result = cloner._clone_monorepo_apps([_app("one", "acme/mono", subdir_path="apps/one")])

    assert result == {"one": existing}
    assert (existing / "local-change").read_text() == "mine"
    assert not (existing / "README.md").exists()  # nothing was copied over it


def test_an_extracted_app_is_renamed_to_its_python_module_name(tmp_path, monkeypatch):
    cloner = _cloner(tmp_path, output=_out())
    app = _app("frappe-consent-management", "acme/mono", subdir_path="apps/frappe-consent-management")
    _fake_monorepo_clone(monkeypatch, {"apps/frappe-consent-management": "frappe_consent_management"})

    result = cloner._clone_monorepo_apps([app])

    assert result == {"frappe_consent_management": cloner.apps_dir / "frappe_consent_management"}
    assert app.name == "frappe_consent_management"
    assert not (cloner.apps_dir / "frappe-consent-management").exists()


def test_extraction_keeps_the_subdirectory_name_when_the_module_directory_is_taken(tmp_path, monkeypatch):
    cloner = _cloner(tmp_path, output=_out())
    squatter = cloner.apps_dir / "frappe_consent_management"
    squatter.mkdir(parents=True)
    (squatter / "keep-me").write_text("previous app")
    app = _app("frappe-consent-management", "acme/mono", subdir_path="apps/frappe-consent-management")
    _fake_monorepo_clone(monkeypatch, {"apps/frappe-consent-management": "frappe_consent_management"})

    result = cloner._clone_monorepo_apps([app])

    assert result == {"frappe-consent-management": cloner.apps_dir / "frappe-consent-management"}
    assert app.name == "frappe-consent-management"
    assert (squatter / "keep-me").read_text() == "previous app"


def test_extraction_reports_the_final_app_name(tmp_path, monkeypatch):
    output = _out()
    cloner = _cloner(tmp_path, output=output)
    _fake_monorepo_clone(monkeypatch, {"apps/one": "one_module"})

    cloner._clone_monorepo_apps([_app("one", "acme/mono", subdir_path="apps/one")])

    output.print.assert_called_once_with("Extracted one_module", emoji_code=":white_check_mark:")


def test_extraction_copies_symlinks_as_symlinks(tmp_path, monkeypatch):
    """`copytree(symlinks=True)`: a link inside the monorepo must not be dereferenced."""
    cloner = _cloner(tmp_path)

    def git_clone(self, repo_url, clone_path, app):
        clone_path = Path(clone_path)
        src = clone_path / "apps" / "one"
        src.mkdir(parents=True)
        (src / "real.txt").write_text("payload")
        (src / "link.txt").symlink_to("real.txt")

    monkeypatch.setattr(AppCloner, "_git_clone", git_clone)

    cloner._clone_monorepo_apps([_app("one", "acme/mono", subdir_path="apps/one")])

    assert (cloner.apps_dir / "one" / "link.txt").is_symlink()


@pytest.mark.timeout(15)
def test_standalone_and_monorepo_apps_are_routed_to_different_destinations(tmp_path, monkeypatch):
    """The routing decision in clone_apps_parallel, observed from both sides at once: a standalone
    app is cloned straight into its app directory, a subdir app into the shared temp checkout."""
    cloner = _cloner(tmp_path, output=_out())

    def populate(target: Path) -> None:
        if target.name.startswith(".tmp_monorepo_"):
            (target / "apps" / "one").mkdir(parents=True)

    calls = _fake_clone_from(monkeypatch, populate=populate)
    apps = [
        _app("erpnext", "frappe/erpnext"),
        _app("one", "acme/mono", ref="main", subdir_path="apps/one"),
    ]

    result = cloner.clone_apps_parallel(apps)

    destinations = {c["url"]: c["path"] for c in calls}
    assert destinations == {
        "https://github.com/frappe/erpnext.git": cloner.apps_dir / "erpnext",
        "https://github.com/acme/mono.git": cloner.apps_dir / ".tmp_monorepo_acme_mono",
    }
    assert set(result) == {"erpnext", "one"}


# --------------------------------------------------------------------------- AppCloner: validation delegation


def test_validate_repos_exist_delegates_and_unpacks_the_batch_result(monkeypatch):
    """The deprecated shim must keep returning the (all_valid, messages) tuple shape."""
    seen = {}

    def fake_batch(apps, github_token=None):
        seen["apps"] = apps
        seen["token"] = github_token
        return SimpleNamespace(all_valid=False, messages=["ok erpnext", "failed hrms"])

    monkeypatch.setattr(AppConfig, "validate_repos_batch", staticmethod(fake_batch))
    apps = [_app("erpnext", "frappe/erpnext")]

    assert AppCloner.validate_repos_exist(apps, "ghp_secret") == (False, ["ok erpnext", "failed hrms"])
    assert seen == {"apps": apps, "token": "ghp_secret"}


# --------------------------------------------------------------------------- BenchDevTools: fixtures


@pytest.fixture
def devtools(tmp_path):
    """A BenchDevTools whose only real collaborator is the filesystem under tmp_path."""

    def build(*, running: bool = True) -> BenchDevTools:
        return BenchDevTools(
            docker_client=MagicMock(),
            compose_file_manager=MagicMock(),
            bench_path=tmp_path / "bench",
            bench_name="test.localhost",
            is_running_fn=lambda: running,
            output_handler=_out(),
        )

    return build


def _docker_error() -> DockerException:
    return DockerException(["docker", "compose", "exec"], SubprocessOutput([], ["boom"], ["boom"], 1))


def _write_app_pyproject(bench_path: Path, app: str, body: str) -> None:
    app_dir = bench_path / "workspace" / "frappe-bench" / "apps" / app
    app_dir.mkdir(parents=True, exist_ok=True)
    (app_dir / "pyproject.toml").write_text(body)


# --------------------------------------------------------------------------- BenchDevTools: dev requirements


def test_dev_requirements_are_name_concatenated_with_their_version_spec(devtools, tmp_path):
    """`honcho = "==1.0"` becomes the pip spec `honcho==1.0`; an empty version yields a bare name."""
    tools = devtools()
    _write_app_pyproject(
        tools.bench_path,
        "frappe",
        '[tool.bench.dev-dependencies]\nhoncho = ""\nwatchdog = "==2.1.9"\n',
    )

    assert tools.get_apps_dev_requirements() == ["honcho", "watchdog==2.1.9"]


def test_dev_requirements_ignore_pyprojects_without_the_bench_dev_table(devtools):
    tools = devtools()
    _write_app_pyproject(tools.bench_path, "frappe", '[project]\nname = "frappe"\n')
    _write_app_pyproject(tools.bench_path, "erpnext", '[tool.bench.dev-dependencies]\nhoncho = ""\n')

    assert tools.get_apps_dev_requirements() == ["honcho"]


def test_dev_requirements_of_a_bench_without_apps_is_empty(devtools):
    """A missing apps directory is not an error: glob simply finds nothing."""
    tools = devtools()
    assert tools.get_apps_dev_requirements() == []


def test_remove_dev_packages_builds_a_non_interactive_uninstall(devtools):
    tools = devtools()
    _write_app_pyproject(
        tools.bench_path,
        "frappe",
        '[tool.bench.dev-dependencies]\nhoncho = ""\nwatchdog = "==2.1.9"\n',
    )

    tools.remove_dev_packages()

    tools.docker_client.compose.exec.assert_called_once_with(
        "frappe",
        command="/workspace/frappe-bench/env/bin/python -m pip uninstall --yes honcho watchdog==2.1.9",
        user="frappe",
        stream=False,
    )
    tools.output.print.assert_called_once_with("Removed dev packages from env")


def test_install_dev_packages_builds_a_quiet_upgrading_install(devtools):
    tools = devtools()
    _write_app_pyproject(tools.bench_path, "frappe", '[tool.bench.dev-dependencies]\nhoncho = ""\n')

    tools.install_dev_packages()

    tools.docker_client.compose.exec.assert_called_once_with(
        "frappe",
        command="/workspace/frappe-bench/env/bin/python -m pip install --quiet --upgrade honcho",
        user="frappe",
        stream=False,
    )
    tools.output.print.assert_called_once_with("Installed dev packages in env")


def test_a_failed_uninstall_raises_the_bench_scoped_error_carrying_pips_output(devtools):
    tools = devtools()
    error = _docker_error()
    tools.docker_client.compose.exec.side_effect = error

    with pytest.raises(BenchFailedToRemoveDevPackages) as excinfo:
        tools.remove_dev_packages()

    assert "pip uninstall" in str(excinfo.value)
    assert "boom" in str(excinfo.value)
    assert excinfo.value.__cause__ is error
    tools.output.print.assert_not_called()


def test_a_failed_install_raises_an_install_specific_error_carrying_pips_output(devtools):
    """An install failure reported as a failed REMOVAL sends the user after the wrong problem,
    and dropping the DockerException loses the only text that says why pip failed."""
    tools = devtools()
    error = _docker_error()
    tools.docker_client.compose.exec.side_effect = error

    with pytest.raises(BenchFailedToInstallDevPackages) as excinfo:
        tools.install_dev_packages()

    assert not isinstance(excinfo.value, BenchFailedToRemoveDevPackages)
    assert "pip install" in str(excinfo.value)
    assert "boom" in str(excinfo.value)
    assert excinfo.value.__cause__ is error
    tools.output.print.assert_not_called()


# --------------------------------------------------------------------------- BenchDevTools: attach guards


def test_attach_refuses_a_bench_that_is_not_running_before_doing_anything(devtools, monkeypatch):
    """The running check is the first gate: no .vscode files, no VS Code launch."""
    tools = devtools(running=False)
    run = MagicMock()
    monkeypatch.setattr(devtools_module.subprocess, "run", run)
    monkeypatch.setattr(devtools_module.shutil, "which", lambda name: "/usr/bin/code")

    with pytest.raises(BenchNotRunning):
        tools.attach_to_bench("frappe", ["ms-python.python"], "/workspace/frappe-bench", debugger=True)

    run.assert_not_called()
    assert not (tools.bench_path / "workspace" / "frappe-bench" / ".vscode").exists()
    tools.compose_file_manager.configure_service.assert_not_called()


def test_a_missing_vscode_binary_stops_the_attach_with_a_single_report(devtools, monkeypatch):
    """Was pinned as "only reported, then attach breaks": the check did not raise, so
    `_build_vscode_command`'s assert was the real gate and the user got the same failure twice
    (and a TypeError instead, under `python -O`). The check is now terminal."""
    tools = devtools()
    monkeypatch.setattr(devtools_module.shutil, "which", lambda name: None)

    with pytest.raises(typer.Exit) as excinfo:
        tools.attach_to_bench("frappe", [], "/workspace/frappe-bench")

    assert excinfo.value.exit_code == 1
    tools.output.display_error.assert_called_once_with(
        "Visual Studio Code binary i.e 'code' is not accessible via cli",
    )


def test_attach_launches_the_remote_container_uri_built_from_the_hex_container_name(devtools, monkeypatch):
    tools = devtools()
    tools.compose_file_manager.get_container_names.return_value = {"frappe": "test-localhost-frappe-1"}
    tools.compose_file_manager.get_labels.return_value = {}
    monkeypatch.setattr(devtools_module.shutil, "which", lambda name: "/usr/bin/code")
    run = MagicMock(return_value=SimpleNamespace(returncode=0))
    monkeypatch.setattr(devtools_module.subprocess, "run", run)

    tools.attach_to_bench("frappe", ["b.ext", "a.ext"], "/workspace/frappe-bench")

    expected_hex = b"test-localhost-frappe-1".hex()
    assert run.call_args.args[0] == shlex.join(
        [
            "/usr/bin/code",
            f"--folder-uri=vscode-remote://attached-container+{expected_hex}+/workspace/frappe-bench",
        ],
    )
    assert run.call_args.kwargs == {"shell": True}


def test_a_nonzero_vscode_exit_is_an_attach_failure(devtools, monkeypatch):
    tools = devtools()
    tools.compose_file_manager.get_container_names.return_value = {"frappe": "c1"}
    tools.compose_file_manager.get_labels.return_value = {}
    monkeypatch.setattr(devtools_module.shutil, "which", lambda name: "/usr/bin/code")
    monkeypatch.setattr(devtools_module.subprocess, "run", MagicMock(return_value=SimpleNamespace(returncode=1)))

    with pytest.raises(BenchAttachTocontainerFailed):
        tools.attach_to_bench("frappe", [], "/workspace/frappe-bench")


def test_attach_regenerates_the_compose_label_before_launching_vscode(devtools, monkeypatch):
    """Ordering matters: the container must be recreated with the new label first."""
    tools = devtools()
    tools.compose_file_manager.get_container_names.return_value = {"frappe": "c1"}
    tools.compose_file_manager.get_labels.return_value = {}
    order: list[str] = []
    tools.compose_file_manager.configure_service.side_effect = lambda *a, **k: order.append("configure")
    tools.docker_client.compose.up.side_effect = lambda *a, **k: order.append("up")
    monkeypatch.setattr(devtools_module.shutil, "which", lambda name: "/usr/bin/code")

    def run(cmd, shell=False):
        order.append("code")
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(devtools_module.subprocess, "run", run)

    tools.attach_to_bench("frappe", ["a.ext"], "/workspace/frappe-bench")

    assert order == ["configure", "up", "code"]
    tools.docker_client.compose.up.assert_called_once_with(
        services=["frappe"],
        detach=True,
        pull="never",
        force_recreate=False,
    )


# --------------------------------------------------------------------------- BenchDevTools: devcontainer label


def _written_label(extensions: list[str], user: str = "frappe") -> dict:
    """The label map exactly as `_apply_new_config` writes it into the compose file.

    `ComposeFile.get_labels(service)` returns that mapping (or None when the service has none),
    which is what `_get_previous_container_config` reads back.
    """
    return {
        "devcontainer.metadata": json.dumps(
            [{"remoteUser": user, "customizations": {"vscode": {"extensions": extensions}}}],
        ),
    }


def test_the_devcontainer_label_carries_the_user_shell_settings_and_sorted_extensions(devtools):
    tools = devtools()
    tools.compose_file_manager.get_labels.return_value = {}

    tools._update_container_config("frappe", ["a.ext", "b.ext"])

    labels = tools.compose_file_manager.configure_service.call_args.kwargs["labels"]
    assert tools.compose_file_manager.configure_service.call_args.args == ("frappe",)
    config = json.loads(labels["devcontainer.metadata"])
    assert len(config) == 1
    assert config[0]["remoteUser"] == "frappe"
    assert config[0]["remoteEnv"] == {"SHELL": "/bin/bash"}
    assert config[0]["customizations"]["vscode"]["extensions"] == ["a.ext", "b.ext"]
    assert config[0]["customizations"]["vscode"]["settings"] == VSCODE_SETTINGS_JSON


def test_attach_sorts_the_requested_extensions_before_they_reach_the_label(devtools, monkeypatch):
    """Unsorted input would make the comparison against the previous label flap forever."""
    tools = devtools()
    tools.compose_file_manager.get_container_names.return_value = {"frappe": "c1"}
    tools.compose_file_manager.get_labels.return_value = {}
    monkeypatch.setattr(devtools_module.shutil, "which", lambda name: "/usr/bin/code")
    monkeypatch.setattr(devtools_module.subprocess, "run", MagicMock(return_value=SimpleNamespace(returncode=0)))

    tools.attach_to_bench("frappe", ["z.ext", "a.ext"], "/workspace/frappe-bench")

    labels = tools.compose_file_manager.configure_service.call_args.kwargs["labels"]
    config = json.loads(labels["devcontainer.metadata"])
    assert config[0]["customizations"]["vscode"]["extensions"] == ["a.ext", "z.ext"]


def test_a_previously_written_label_is_read_back_as_the_extensions_it_recorded(devtools):
    """Was pinned as `== []`: the reader subscripted the label MAPPING with `[0]`, the resulting
    KeyError(0) was swallowed, and the metadata this module itself wrote was never parsed. That
    bug is exactly what the old assertion pinned, so the expectation flips with the fix."""
    tools = devtools()
    tools.compose_file_manager.get_labels.return_value = _written_label(["a.ext", "b.ext"])

    assert tools._get_previous_container_config() == ["a.ext", "b.ext"]


def test_an_unchanged_extension_list_no_longer_regenerates_the_label_or_recreates_the_container(devtools):
    """Was pinned as "regenerates every time": because the previous list always read back empty,
    every attach with at least one extension rewrote the compose label and re-upped the frappe
    service. An unchanged config must be a no-op."""
    tools = devtools()
    tools.compose_file_manager.get_labels.return_value = _written_label(["a.ext", "b.ext"])

    tools._update_container_config("frappe", ["a.ext", "b.ext"])

    tools.compose_file_manager.configure_service.assert_not_called()
    tools.docker_client.compose.up.assert_not_called()


def test_dropping_every_extension_still_reaches_the_container(devtools):
    """An empty request no longer matches a non-empty recorded list, so removing extensions
    regenerates the label (this used to be the only reachable no-op, for the wrong reason)."""
    tools = devtools()
    tools.compose_file_manager.get_labels.return_value = _written_label(["a.ext"])

    tools._update_container_config("frappe", [])

    tools.compose_file_manager.configure_service.assert_called_once()
    tools.docker_client.compose.up.assert_called_once()


def test_changing_only_the_remote_user_reaches_the_container(devtools):
    """Was pinned as "never reaches the container": `_config_needs_update` accepted `user` and
    ignored it, which was harmless only while the check was stuck on True. Now that the extension
    comparison works, an unread `user` would silently drop `fm code --user`."""
    tools = devtools()
    tools.compose_file_manager.get_labels.return_value = _written_label([])

    tools._update_container_config("someone-else", [])

    labels = tools.compose_file_manager.configure_service.call_args.kwargs["labels"]
    assert json.loads(labels["devcontainer.metadata"])[0]["remoteUser"] == "someone-else"


def test_a_service_with_labels_but_no_devcontainer_metadata_yields_no_extensions(devtools):
    tools = devtools()
    tools.compose_file_manager.get_labels.return_value = {"other.label": "x"}

    assert tools._get_previous_container_config() == []


def test_a_service_with_no_labels_at_all_yields_no_extensions(devtools):
    """Was pinned as TypeError: `ComposeFile.get_labels` returns None for a service without
    labels and only KeyError was handled, so attach died reading its own missing label."""
    tools = devtools()
    tools.compose_file_manager.get_labels.return_value = None

    assert tools._get_previous_container_config() == []


# --------------------------------------------------------------------------- BenchDevTools: debugger config


def test_debugger_config_is_refused_outside_the_workspace_directory(devtools):
    tools = devtools()

    tools._setup_debugger_config("/etc")

    tools.output.warning.assert_called_once_with("Debugger configuration is only supported for workspace directory")
    assert not (tools.bench_path / "etc").exists()
    tools.docker_client.compose.exec.assert_not_called()


def test_debugger_config_writes_the_three_vscode_files_and_installs_ruff(devtools):
    tools = devtools()

    tools._setup_debugger_config("/workspace/frappe-bench/")

    vscode_dir = tools.bench_path / "workspace" / "frappe-bench" / ".vscode"
    assert json.loads((vscode_dir / "tasks.json").read_text()) == VSCODE_TASKS_JSON
    assert json.loads((vscode_dir / "launch.json").read_text()) == VSCODE_LAUNCH_JSON
    assert json.loads((vscode_dir / "settings.json").read_text()) == VSCODE_SETTINGS_JSON
    tools.docker_client.compose.exec.assert_called_once_with(
        service="frappe",
        command="/workspace/frappe-bench/env/bin/pip install ruff",
        user="frappe",
        stream=True,
    )
    tools.output.print.assert_called_with("Synced vscode debugger configuration")


def test_config_files_are_written_sorted_and_indented(devtools):
    """Stable formatting is what keeps the backup diff readable."""
    tools = devtools()
    target = tools.bench_path
    target.mkdir(parents=True)
    path = target / "settings.json"

    tools._write_config_file(path, {"b": 1, "a": 2})

    assert path.read_text() == '{\n    "a": 2,\n    "b": 1\n}'


def test_an_existing_config_file_is_backed_up_before_being_replaced(devtools):
    tools = devtools()
    vscode_dir = tools.bench_path / "workspace" / "frappe-bench" / ".vscode"
    vscode_dir.mkdir(parents=True)
    (vscode_dir / "launch.json").write_text('{"mine": true}')

    tools._sync_vscode_config_files("workspace/frappe-bench")

    backups = list(vscode_dir.glob("launch.*.json"))
    assert len(backups) == 1
    assert backups[0].read_text() == '{"mine": true}'
    assert json.loads((vscode_dir / "launch.json").read_text()) == VSCODE_LAUNCH_JSON
    assert not list(vscode_dir.glob("tasks.*.json"))  # nothing to back up


def test_a_ruff_install_failure_is_a_warning_not_a_failed_attach(devtools):
    tools = devtools()
    tools.docker_client.compose.exec.side_effect = _docker_error()

    tools._setup_debugger_config("/workspace/frappe-bench")

    tools.output.warning.assert_called_once_with("Not able to install ruff in env")
    assert (tools.bench_path / "workspace" / "frappe-bench" / ".vscode" / "launch.json").exists()


def test_attach_without_the_debugger_flag_writes_no_vscode_files(devtools, monkeypatch):
    tools = devtools()
    tools.compose_file_manager.get_container_names.return_value = {"frappe": "c1"}
    tools.compose_file_manager.get_labels.return_value = {}
    monkeypatch.setattr(devtools_module.shutil, "which", lambda name: "/usr/bin/code")
    monkeypatch.setattr(devtools_module.subprocess, "run", MagicMock(return_value=SimpleNamespace(returncode=0)))

    tools.attach_to_bench("frappe", [], "/workspace/frappe-bench")

    assert not (tools.bench_path / "workspace" / "frappe-bench" / ".vscode").exists()


def test_attach_with_the_debugger_flag_syncs_config_before_launching_vscode(devtools, monkeypatch):
    """The debugger files must exist before VS Code opens the folder, or it reads stale config."""
    tools = devtools()
    tools.compose_file_manager.get_container_names.return_value = {"frappe": "c1"}
    tools.compose_file_manager.get_labels.return_value = {}
    monkeypatch.setattr(devtools_module.shutil, "which", lambda name: "/usr/bin/code")
    launch_existed_at_attach: list[bool] = []

    def run(cmd, shell=False):
        launch_existed_at_attach.append(
            (tools.bench_path / "workspace" / "frappe-bench" / ".vscode" / "launch.json").exists(),
        )
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(devtools_module.subprocess, "run", run)

    tools.attach_to_bench("frappe", ["a.ext"], "/workspace/frappe-bench", debugger=True)

    assert launch_existed_at_attach == [True]


def test_the_label_this_module_writes_is_the_shape_it_reads_back(devtools):
    """Writer and reader have to agree or the comparison is worthless: feed the label produced by
    one attach straight back into the next one.

    Was pinned as "expects a shape this module never writes": the reader needed a LIST of label
    dicts holding a JSON OBJECT, while `get_labels` returns the label MAPPING and
    `_apply_new_config` writes a JSON ARRAY, so this round trip never closed.
    """
    tools = devtools()
    tools.compose_file_manager.get_labels.return_value = {}

    tools._update_container_config("frappe", ["a.ext", "b.ext"])
    written = tools.compose_file_manager.configure_service.call_args.kwargs["labels"]

    tools.compose_file_manager.get_labels.return_value = written
    tools.compose_file_manager.configure_service.reset_mock()
    tools._update_container_config("frappe", ["a.ext", "b.ext"])

    tools.compose_file_manager.configure_service.assert_not_called()
