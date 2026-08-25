"""Shell in this repo must pass shellcheck, including the bash embedded in action.yml.

`actionlint` runs shellcheck over `run:` blocks in workflow files, and that is how the
`.github/workflows` scripts stay honest. It does NOT understand a composite action: point
it at `action.yml` and it complains that `jobs` and `on` are missing, because it only
parses workflows. Verified, not assumed.

So the bash inside `action.yml` is the one shell in this repo that no linter reaches, and
it is the shell most likely to break someone's deploy. This test reaches it: each `run:`
block is extracted and shellchecked as bash.

Two files are excluded, with the count they carry today. They are pre-existing debt, not
something this check introduced, and silently excluding them would let that debt grow.
"""

import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

ACTION = Path("action.yml")
SCRIPTS = Path("scripts")

# Pre-existing findings, excluded deliberately rather than by omission. Clean one up and
# delete its entry: anything not named here is required to be clean, so a NEW script is
# covered automatically.
LEGACY_FINDINGS = {
    Path("scripts/install.sh"): 5,
    Path("scripts/migrate-test.sh"): 3,
}


def _shellcheck():
    exe = shutil.which("shellcheck")
    if exe is None:
        pytest.skip("shellcheck not installed; CI provides it via the shellcheck-py dev dependency")
    return exe


def _run(exe: str, source: str) -> list[str]:
    """Findings for one script, read from stdin so the name in the output is ours."""
    # exe comes from shutil.which, the script text from this repo. Nothing here is user input.
    result = subprocess.run(  # noqa: S603
        [exe, "--shell=bash", "--format=gcc", "-"],
        input=source,
        capture_output=True,
        text=True,
        check=False,
    )
    return [line for line in result.stdout.splitlines() if ":" in line]


def _action_run_blocks() -> list[tuple[str, str]]:
    doc = yaml.safe_load(ACTION.read_text())
    blocks = []
    for step in doc["runs"]["steps"]:
        if "run" not in step:
            continue
        # GitHub runs each block with bash -e; `set -euo pipefail` inside is ours.
        blocks.append((step.get("name", "<unnamed>"), step["run"]))
    return blocks


def _tracked_scripts() -> list[Path]:
    return sorted(p for p in SCRIPTS.glob("*.sh") if p not in LEGACY_FINDINGS)


class TestActionYmlShell:
    def test_the_action_has_run_blocks_to_check(self):
        """A selector that matches nothing would pass this file forever."""
        assert len(_action_run_blocks()) >= 5

    @pytest.mark.parametrize(("name", "source"), _action_run_blocks(), ids=lambda v: str(v)[:28])
    def test_each_run_block_is_clean(self, name, source):
        findings = _run(_shellcheck(), source)

        assert findings == [], f"shellcheck findings in action.yml step {name!r}:\n" + "\n".join(findings)


class TestScripts:
    @pytest.mark.parametrize("path", _tracked_scripts(), ids=str)
    def test_each_script_is_clean(self, path):
        findings = _run(_shellcheck(), path.read_text())

        assert findings == [], f"shellcheck findings in {path}:\n" + "\n".join(findings)

    @pytest.mark.parametrize(("path", "expected"), sorted(LEGACY_FINDINGS.items()))
    def test_excluded_scripts_do_not_get_worse(self, path, expected):
        """A ratchet, not a pass: these may shrink, never grow."""
        findings = _run(_shellcheck(), path.read_text())

        assert len(findings) <= expected, (
            f"{path} went from {expected} to {len(findings)} shellcheck findings:\n" + "\n".join(findings)
        )


class TestActionlintCannotDoThis:
    """The justification for this file. If actionlint ever learns composite actions, this
    test fails and the whole module can be reconsidered."""

    def test_actionlint_does_not_understand_a_composite_action(self):
        exe = shutil.which("actionlint")
        if exe is None:
            pytest.skip("actionlint not installed")

        # exe from shutil.which, argument is a repo path.
        result = subprocess.run([exe, str(ACTION)], capture_output=True, text=True, check=False)  # noqa: S603

        assert "missing in workflow" in result.stdout, (
            "actionlint now parses action.yml; re-check whether it shellchecks the run blocks, "
            "and if it does, this module is redundant"
        )
