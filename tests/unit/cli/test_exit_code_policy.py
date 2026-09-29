"""Exit 2 means the command line was wrong; exit 1 means fm tried and could not.

The same mistake used to answer differently depending on which half caught it: `fm create` with
no bench name exited 2 from Click's parser, `fm info` exited 1 from fm's own handler after paying
the whole callback. The rule is in docs/commands/index.md; these pin it.
"""

import pytest

from frappe_manager.exceptions import FrappeManagerException, MissingArgumentError, NonInteractiveError
from frappe_manager.utils.callbacks import _resolve_bench

pytestmark = pytest.mark.timeout(15)


def test_a_missing_required_argument_is_a_usage_error(monkeypatch):
    """No bench name and no way to ask for one: exit 2, the code the parser gives for the same thing."""
    monkeypatch.setattr("frappe_manager.utils.callbacks.get_sitename_from_current_path", lambda: None)
    monkeypatch.setattr(
        "frappe_manager.utils.callbacks._pick_bench_name",
        lambda: (_ for _ in ()).throw(EOFError("no tty")),
    )

    with pytest.raises(MissingArgumentError) as excinfo:
        _resolve_bench(None)

    assert excinfo.value.exit_code == 2


def test_a_refusal_fm_understood_stays_at_one():
    """`NonInteractiveError` also covers "pass --yes", where the command line was fine and fm declined."""
    assert NonInteractiveError("needs --yes").exit_code == 1


def test_the_default_for_every_other_failure_is_one():
    """Only a usage error opts into 2; a new exception class must not silently become one."""
    assert FrappeManagerException("anything").exit_code == 1


def test_a_missing_argument_is_still_a_non_interactive_error():
    """Callers and tests that catch the parent must keep catching it: this narrows, it does not move."""
    assert issubclass(MissingArgumentError, NonInteractiveError)
