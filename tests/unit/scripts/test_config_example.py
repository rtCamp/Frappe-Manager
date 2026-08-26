"""The committed example configs must match the models, and every key must explain itself.

`frappe_manager/templates/bench_config.toml` was a hand-written example that nothing generated,
nothing checked and nothing read. It drifted through two schema redesigns unnoticed and its last
accurate statement was several releases old. Generating the example from `Field(description=...)` only
helps if regenerating is enforced, so this is the enforcement: a field added, renamed, re-described or
newly excluded fails here until `just config-example` is run.

The second gate is what makes the file self-explaining rather than merely correct. A field with no
description renders as "No description." and fails, so documenting a new config key is not optional.
"""

import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO_ROOT / "scripts"))

from gen_config_example import TARGETS

EXAMPLES = sorted(TARGETS)


@pytest.mark.parametrize("relative", EXAMPLES, ids=lambda p: p.name)
def test_the_committed_example_matches_the_models(relative):
    """Run `just config-example` if this fails; the models are the source of truth, not the file."""
    expected = TARGETS[relative]()
    actual = (REPO_ROOT / relative).read_text()

    assert actual == expected, f"{relative} is stale. Regenerate it with `just config-example`."


@pytest.mark.parametrize("relative", EXAMPLES, ids=lambda p: p.name)
def test_every_documented_key_has_a_description(relative):
    """A config key nobody described is a key nobody can use without reading the source."""
    text = (REPO_ROOT / relative).read_text()

    assert "No description." not in text, (
        f"{relative} has a key with no Field(description=...). Describe it on the model."
    )


@pytest.mark.parametrize("relative", EXAMPLES, ids=lambda p: p.name)
def test_the_example_is_inert(relative):
    """It documents a file fm owns and writes, so every line is commented: pasting the whole thing
    somewhere must never set anything."""
    lines = (REPO_ROOT / relative).read_text().splitlines()

    live = [line for line in lines if line.strip() and not line.lstrip().startswith("#")]
    assert live == [], f"{relative} has uncommented lines: {live}"
